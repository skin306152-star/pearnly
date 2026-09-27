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


def post(url, headers, payload, timeout_s, model, bucket):
    waited = 0.0
    while True:
        try:
            return _post(url, headers, payload, timeout_s, model, bucket)
        except Deferred as exc:
            # 只有明确可重试的（额度 / 429 / 503）才会走到这里；结果不明（Uncertain）照旧往上抛。
            delay = float(exc.delay or 1)
            if not frappe.flags.get("pearnly_ocr_work") or delay > WAIT_STEP_LIMIT \
                    or waited + delay > WAIT_PER_CALL:
                raise
            time.sleep(delay)
            waited += delay


def _post(url, headers, payload, timeout_s, model, bucket):
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
    timeout = max(1, min(120, float(timeout_s)))
    with budget.permit(bucket, work.JOB_TIMEOUT):
        work.checkpoint(key, "started")
        deadline = time.monotonic() + timeout
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=(10, timeout), stream=True)
        except requests.RequestException as exc:
            # It may have reached the provider. Persist started; no automatic paid replay.
            raise Uncertain("Provider transport outcome unknown") from exc
        delay = retry_delay(response)
        budget.feedback(bucket, response.status_code, delay)
        if response.status_code in (429, 503):
            work.checkpoint(key, "rejected", {"http": response.status_code})
            response.close()
            raise Deferred("provider_" + str(response.status_code), delay)
        try:
            raw = bytearray()
            for chunk in response.iter_content(65536):
                if time.monotonic() > deadline:
                    raise Uncertain("Provider response exceeded elapsed time limit")
                if len(raw) + len(chunk) > 8 * 1024 * 1024:
                    raise Uncertain("Provider response exceeded safe memory limit")
                raw.extend(chunk)
            response._content = bytes(raw)
            response._content_consumed = True
        except requests.RequestException as exc:
            raise Uncertain("Provider response was interrupted") from exc
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
