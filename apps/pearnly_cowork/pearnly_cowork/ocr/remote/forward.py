"""服务器这边：把一份发票识别交给 Cloud Run 并等它读完（ADR-0015）。只在 Frappe 识别任务（``workflow._recognize``）里调用。

受理、余额、权限复核、存结果、扣费照旧在服务器；这里只负责：
- 发出前按租户 / 全站名额放行（``budget.permit``，与本机识别共用同一套计数，租户之间公平轮转）；
- 用识别服务账号签发的身份令牌调用不公开的 Cloud Run 服务；
- 读按行回复：读到新的一页就推进度，最后把结果交回（或把暂缓 / 结果不明 / 明确拒绝照本机同样的方式抛出）。

开关：站点配置 ``pearnly_cowork_ocr_remote`` = {"enabled": 1, "url": "https://…run.app"}；不开或关掉就照旧在本机识别。
"""
import threading
import time

import frappe
import requests

from pearnly_core import ocr
from pearnly_core.execution import budget, work
from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_cowork.ocr.providers.client import ProviderError
from pearnly_cowork.ocr.remote import contract

CONFIG = "pearnly_cowork_ocr_remote"
PATH = "/v1/cowork/invoice"
# 一份最多跑 420 秒就让出（contract.YIELD_SECONDS）；单次调用最长 180 秒；Cloud Run 每 20 秒发一行 tick。
READ_IDLE_SECONDS = 240
_tokens, _lock = {}, threading.Lock()


class RemoteError(RuntimeError):
    """Cloud Run 报的引擎 / 输入错误：照本机执行的程序错误处理（workflow_error），不重放付费调用。"""


def settings():
    value = frappe.conf.get(CONFIG)
    if not isinstance(value, dict) or not value.get("enabled"):
        return None
    url = str(value.get("url") or "").rstrip("/")
    if not url.startswith("https://") or not url.endswith(".run.app"):
        return None
    return {"url": url, "rpm": int(value.get("rpm") or 0) or None}


def _id_token(audience):
    """用识别服务账号（``ocr.credentials_path``）签身份令牌，Cloud Run IAM 只认这个身份。快过期才重签。"""
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    with _lock:
        credentials = _tokens.get(audience)
        if credentials is None:
            credentials = service_account.IDTokenCredentials.from_service_account_file(
                ocr.credentials_path(), target_audience=audience)
            _tokens[audience] = credentials
        if not credentials.valid:
            credentials.refresh(Request())
        return credentials.token


def recognize(doc, frozen, examples, content, filename, progress, *, post=None, id_token=None):
    """返回 ``(pipeline_json, evidence)``；暂缓抛 ``Deferred``，结果不明抛 ``Uncertain``，明确拒绝抛 ``ProviderError``。"""
    config = settings()
    if config is None:
        raise RuntimeError("Remote OCR is not enabled")
    body = contract.request(site=frappe.local.site, work=doc.work_name, batch=doc.name, filename=filename,
        content=content, page_count=int(doc.page_count), anchor_date=str(doc.anchor_date),
        environment=frozen["environment"], policy=frozen["policy"],
        examples={"examples_by_tax": examples["examples_by_tax"]} if examples else None)
    url = config["url"] + PATH
    bucket = budget.scope("cloudrun", frozen["environment"].get("GCP_PROJECT", ""), "cowork", "invoice")
    with budget.permit(bucket, work.JOB_TIMEOUT, **({"rpm": config["rpm"]} if config["rpm"] else {})):
        try:
            token = (id_token or _id_token)(config["url"])
        except Exception as exc:  # 还没发出任何付费调用：稍后重试
            raise Deferred("remote_token_unavailable", 15) from exc
        try:
            response = (post or requests.post)(url, json=body, timeout=(30, READ_IDLE_SECONDS), stream=True,
                                                headers={"Authorization": "Bearer " + token})
        except (requests.ConnectionError, requests.Timeout) as exc:
            # 没连上 / 发送中断：Cloud Run 可能已开始读。检查点在存储桶里，下次接着读；不在这里判结果。
            raise Deferred("remote_unreachable", 15) from exc
        try:
            if response.status_code in (429, 500, 502, 503, 504):
                raise Deferred("remote_http_" + str(response.status_code), 15)
            if response.status_code != 200:
                raise RemoteError("remote_http_" + str(response.status_code))
            final = None
            try:
                for raw in response.iter_lines(chunk_size=8192):
                    if not raw:
                        continue
                    event = contract.parse_line(raw)
                    if event["event"] == "page":
                        progress(event["page"])
                    elif event["event"] == "result":
                        final = event
                        break
            except (requests.RequestException, ValueError) as exc:
                raise Deferred("remote_interrupted", 15) from exc
        finally:
            response.close()
    if final is None:
        # 连接正常结束却没有结果行：Cloud Run 实例中途没了。检查点会判断哪些调用已付费 / 结果不明。
        raise Deferred("remote_interrupted", 15)
    status = final["status"]
    if status == "done":
        return final["pipeline"], final["evidence"]
    if status == "deferred":
        raise Deferred(final.get("reason") or "remote_deferred", final.get("delay") or 15)
    if status == "uncertain":
        raise Uncertain(final.get("reason") or "Remote provider result is unknown")
    if status == "refused":
        raise ProviderError(final.get("code") or "ocr.provider_http_error", http_status=final.get("http_status"),
                            provider_code=final.get("provider_code"))
    raise RemoteError(str(final.get("code") or "remote_error"))
