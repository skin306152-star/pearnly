"""Bounded provider calls with durable page/model checkpoints and usage evidence."""
import json
import random
import ssl
import threading
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import frappe
import requests
from requests.adapters import HTTPAdapter
from requests.utils import DEFAULT_CA_BUNDLE_PATH

from . import budget, work
from .errors import Deferred, Uncertain


# 证书库只解析一次（2026-10-01 生产压测）：requests 2.33 每开一个 HTTPS 连接都把 certifi 整个证书库重新解析一遍，
# 生产 CPU 上每次约 0.12 秒、每张发票约 0.5 秒，占我们这边识别 CPU 的约五分之一。这里把同一份证书库装进一个共用的
# TLS 上下文（与 urllib3 自己建的上下文同一个工厂、同样的校验要求），其余照旧：每次调用新建会话和连接、用完关闭，
# 发出的请求与证书 / 主机名校验都不变。自定义证书库（REQUESTS_CA_BUNDLE 等）不走共用上下文，照 urllib3 原样每次新建。
_tls = None
_tls_lock = threading.Lock()


def _tls_context():
    global _tls
    with _tls_lock:
        if _tls is None:
            from urllib3.util.ssl_ import create_urllib3_context

            context = create_urllib3_context(cert_reqs=ssl.CERT_REQUIRED)
            context.load_verify_locations(cafile=DEFAULT_CA_BUNDLE_PATH)
            _tls = context
        return _tls


class _SharedTLS(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = _tls_context()
        super().init_poolmanager(*args, **kwargs)

    def cert_verify(self, conn, url, verify, cert):
        super().cert_verify(conn, url, verify, cert)
        options = getattr(conn, "conn_kw", None)  # urllib3 2：上下文随连接参数传给每个新连接
        if not isinstance(options, dict) or options.get("ssl_context") is not _tls_context():
            return
        if verify is True and conn.ca_certs == DEFAULT_CA_BUNDLE_PATH and not getattr(conn, "ca_cert_dir", None):
            conn.ca_certs = None  # 共用上下文里已装好同一份证书库
        else:
            options.pop("ssl_context")  # 自定义证书库或不校验：照 urllib3 原样每次新建


def _http_post(url, **kwargs):
    """等同 `requests.post`（每次一个新会话、用完关闭），只是 HTTPS 用共用的 TLS 上下文。"""
    with requests.Session() as session:
        session.mount("https://", _SharedTLS())
        return session.request("post", url, **kwargs)


def retry_delay(response):
    delay = 5 + random.uniform(0, 2)
    value = response.headers.get("Retry-After", "")
    try:
        delay = max(delay, float(value))
    except (TypeError, ValueError):
        try:
            delay = max(delay, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    return min(300, delay)


# 后台识别任务里，拿不到额度 / 被 429 要求冷却时，短的等待原地等（同时每次重试都刷新排队位置），
# 不整份让出。多页压测（2026-09-27，`scripts/ocr-multipage-stress.py`）：50 页 PDF 每次让出都要从第 1 页
# 起把检查点取回一遍，越往后越慢（16 → 35 秒）；超过公平队列 30 秒的「还在排」时限后排到队尾，从第 8 次
# 续跑起一个请求也发不出去，照这样会在 30 分钟后过期失败。网页请求里仍然立即让出，不卡住页面。
WAIT_STEP_LIMIT = 20
WAIT_PER_CALL = 60


def _in_queue_job() -> bool:
    try:
        return bool(frappe.flags.get("pearnly_ocr_work"))
    except RuntimeError:  # 没有站点上下文（离线测试直接调用）：不是后台识别任务
        return False


def post(url, headers, payload, timeout_s, model, bucket, *, rpm=None, wait=True, label=None,
         hand_back=(), shared_quota=False):
    """``rpm``：这个桶自己的每分钟上限（见 ``budget.permit``）。``wait=False``：拿不到额度 / 被 429 时不原地等，
    直接把 Deferred 交给调用方（Document AI 换下一个区域，用户 2026-09-30「遇到限流马上转区域，不要原地重试」）。
    ``hand_back``：这些原因前缀的 Deferred 不原地等、直接交给调用方（Gemini 被谷歌拒绝就转美国多区域入口；
    自家额度的等待照旧原地等）。``shared_quota``：共享额度（Gemini DSQ），零星拒绝不让整个模型停下（``budget.feedback``）。
    ``label``：限流计数的标签（``budget.note``）。"""
    waited = 0.0
    while True:
        try:
            return _post(url, headers, payload, timeout_s, model, bucket, rpm=rpm, label=label,
                         shared_quota=shared_quota)
        except Deferred as exc:
            # 只有明确可重试的（额度 / 429 / 503）才会走到这里；结果不明（Uncertain）照旧往上抛。
            delay = float(exc.delay or 1)
            budget.note("deferred:" + str(exc.reason), label)
            if not wait or not _in_queue_job() or delay > WAIT_STEP_LIMIT \
                    or waited + delay > WAIT_PER_CALL or str(exc.reason).startswith(tuple(hand_back)):
                raise
            time.sleep(delay)
            waited += delay
            budget.note("waited_seconds", label, delay)


# 结果不明后名额还留多久（2026-09-29 Cowork 压测）：原来一律留到任务时限（630 秒），每租户 3 个名额被几次
# 读超时 / 连接重置占满，整个租户的识别停摆。我们这边已经放弃、连接已关：被对方重置或没连上的马上还；
# 读超时对方可能还在算，留 60 秒。看不到结束的中断（任务超时、进程被杀）仍按原来的保守租期。
READ_TIMEOUT_LEASE_SECONDS = 60
UPLOAD_STEP_SECONDS = 30


def _lease_after(exc):
    return READ_TIMEOUT_LEASE_SECONDS if isinstance(exc, requests.exceptions.ReadTimeout) else 0


def _post(url, headers, payload, timeout_s, model, bucket, rpm=None, label=None, shared_quota=False):
    key = work.checkpoint_key(payload, model)
    saved = work.checkpoint(key)
    if saved and saved["state"] == "done":
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(saved["body"]).encode()
        response._content_consumed = True
        return response
    if saved and saved["state"] == "started":
        raise Uncertain("Previous provider result is unknown")
    # requests' read timeout is an idle timeout, not a whole-response deadline.
    # Keep the permit beyond native RQ's hard job deadline even if an upstream
    # trickles bytes. Completed responses release immediately; unknown outcomes
    # retain the conservative lease instead of creating another active request.
    # 上限 180 秒：旧线上 Cowork 对账抽取就是 180 秒，密的账单页 3.8 要 120 秒以上（2026-09-29 压测：
    # 等回复超时反复重试、整套失败）。其余调用方传的都不超过 120，行为不变。
    timeout = max(1, min(180, float(timeout_s)))
    with budget.permit(bucket, work.JOB_TIMEOUT, **({"rpm": rpm} if rpm else {})):
        work.checkpoint(key, "started")
        deadline = time.monotonic() + timeout
        try:
            # 第一项在 urllib3 里同时是连接和「发送请求体每一步」的时限：生产高负载时大文件上传 Document AI 卡住超过 10 秒
            # 就被自己掐断（2026-10-01 pB：连续 3 次 The write operation timed out，一套银行账单整套读不出）。放到 30 秒，
            # 只让慢的上传能传完；读回复的时限不变。
            response = _http_post(url, headers=headers, json=payload, timeout=(UPLOAD_STEP_SECONDS, timeout), stream=True)
        except requests.RequestException as exc:
            # It may have reached the provider. Persist started; no automatic paid replay.
            raise Uncertain("Provider transport outcome unknown", lease_seconds=_lease_after(exc)) from exc
        delay = retry_delay(response)
        budget.feedback(bucket, response.status_code, delay, shared=shared_quota)
        if response.status_code in (429, 503):
            budget.note("http_" + str(response.status_code), label)
            work.checkpoint(key, "rejected", {"http": response.status_code})
            response.close()
            raise Deferred("provider_" + str(response.status_code), delay)
        try:
            raw = bytearray()
            for chunk in response.iter_content(65536):
                if time.monotonic() > deadline:
                    raise Uncertain("Provider response exceeded elapsed time limit", lease_seconds=0)
                if len(raw) + len(chunk) > 8 * 1024 * 1024:
                    raise Uncertain("Provider response exceeded safe memory limit", lease_seconds=0)
                raw.extend(chunk)
            response._content = bytes(raw)
            response._content_consumed = True
        except requests.RequestException as exc:
            raise Uncertain("Provider response was interrupted", lease_seconds=_lease_after(exc)) from exc
        finally:
            response.close()
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                work.checkpoint(key, "rejected", {"http": 200, "error": "invalid_json"})
            else:
                # Includes usageMetadata, modelVersion and each completed model/page.
                # No raw image bytes or auth headers are stored in the checkpoint.
                work.checkpoint(key, "done", body)
        else:
            work.checkpoint(key, "rejected", {"http": response.status_code})
        return response
