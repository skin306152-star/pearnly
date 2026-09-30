"""Bounded provider calls with durable page/model checkpoints and usage evidence."""
import json
import random
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import frappe
import requests

from . import budget, work
from .errors import Deferred, Uncertain


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


def post(url, headers, payload, timeout_s, model, bucket, *, rpm=None, wait=True, label=None):
    """``rpm``：这个桶自己的每分钟上限（见 ``budget.permit``）。``wait=False``：拿不到额度 / 被 429 时不原地等，
    直接把 Deferred 交给调用方（Document AI 换下一个区域，用户 2026-09-30「遇到限流马上转区域，不要原地重试」）。
    ``label``：限流计数的标签（``budget.note``）。"""
    waited = 0.0
    while True:
        try:
            return _post(url, headers, payload, timeout_s, model, bucket, rpm=rpm, label=label)
        except Deferred as exc:
            # 只有明确可重试的（额度 / 429 / 503）才会走到这里；结果不明（Uncertain）照旧往上抛。
            delay = float(exc.delay or 1)
            budget.note("deferred:" + str(exc.reason), label)
            if not wait or not _in_queue_job() or delay > WAIT_STEP_LIMIT \
                    or waited + delay > WAIT_PER_CALL:
                raise
            time.sleep(delay)
            waited += delay
            budget.note("waited_seconds", label, delay)


# 结果不明后名额还留多久（2026-09-29 Cowork 压测）：原来一律留到任务时限（630 秒），每租户 3 个名额被几次
# 读超时 / 连接重置占满，整个租户的识别停摆。我们这边已经放弃、连接已关：被对方重置或没连上的马上还；
# 读超时对方可能还在算，留 60 秒。看不到结束的中断（任务超时、进程被杀）仍按原来的保守租期。
READ_TIMEOUT_LEASE_SECONDS = 60


def _lease_after(exc):
    return READ_TIMEOUT_LEASE_SECONDS if isinstance(exc, requests.exceptions.ReadTimeout) else 0


def _post(url, headers, payload, timeout_s, model, bucket, rpm=None, label=None):
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
            response = requests.post(url, headers=headers, json=payload, timeout=(10, timeout), stream=True)
        except requests.RequestException as exc:
            # It may have reached the provider. Persist started; no automatic paid replay.
            raise Uncertain("Provider transport outcome unknown", lease_seconds=_lease_after(exc)) from exc
        delay = retry_delay(response)
        budget.feedback(bucket, response.status_code, delay)
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
