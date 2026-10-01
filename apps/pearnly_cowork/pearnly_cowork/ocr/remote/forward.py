"""服务器这边：把一份识别交给 Cloud Run 并等它读完（ADR-015）。只在 Frappe 识别任务里调用：
发票录入（``workflow._recognize``）、销项税核查的发票（``recon_center.invoice_ocr``）、对账三类文档（``recon_center.document_ocr``）。

受理、余额、权限复核、存结果、扣费照旧在服务器；这里只负责：
- 发出前按租户 / 全站名额放行（``budget.permit``，与本机识别共用同一套计数，租户之间公平轮转）；
- 等回复前放开数据库连接（``pool.release_db``），几十份同时在等也不占连接；
- 用识别服务账号签发的身份令牌调用不公开的 Cloud Run 服务；
- 读按行回复：中间的进度交给调用方，最后把结果交回（或把暂缓 / 结果不明 / 明确拒绝 / 读不出照本机同样的方式抛出）。

开关：站点配置 ``pearnly_cowork_ocr_remote`` = {"enabled": 1, "url": "https://…run.app"}；不开或关掉就照旧在本机识别。
"""
import threading

import frappe
import requests

from pearnly_core import ocr
from pearnly_core.execution import budget, pool, work
from pearnly_core.execution.errors import Deferred, Uncertain
from pearnly_cowork.ocr.engine import errors as engine_errors
from pearnly_cowork.ocr.providers.client import ProviderError
from pearnly_cowork.ocr.remote import contract

CONFIG = "pearnly_cowork_ocr_remote"
PATHS = {"invoice": "/v1/cowork/invoice", "document": "/v1/cowork/document"}
# 一份最多跑 420 秒就让出（contract.YIELD_SECONDS）；单次调用最长 180 秒；Cloud Run 每 20 秒发一行 tick。
READ_IDLE_SECONDS = 240
_tokens, _lock = {}, threading.Lock()


class RemoteError(RuntimeError):
    """Cloud Run 报的程序错误（不是引擎的内容错误）：照本机执行的程序错误处理（workflow_error），不重放付费调用。"""


def settings():
    # 没有站点上下文（离线测试、脚本）时当作没开：不抛出（frappe.conf 未绑定时为假）。
    value = (frappe.conf or {}).get(CONFIG)
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
    """发票录入：返回 ``(pipeline_json, evidence)``（见 :func:`send`）。"""
    return send(work_name=doc.work_name, batch=doc.name, filename=filename, content=content,
                page_count=int(doc.page_count), anchor_date=str(doc.anchor_date), frozen=frozen,
                examples={"examples_by_tax": examples["examples_by_tax"]} if examples else None,
                progress=progress, post=post, id_token=id_token)


def send(*, work_name, batch, filename, content, page_count, anchor_date, frozen, examples=None, variant="intake",
         yield_seconds=contract.YIELD_SECONDS, progress=None, post=None, id_token=None):
    """一份发票交给 Cloud Run 读：返回 ``(pipeline_json, evidence)``。"""
    config = _config()
    body = contract.request(site=frappe.local.site, work=work_name, batch=batch, filename=filename, content=content,
        page_count=page_count, anchor_date=anchor_date, environment=frozen["environment"], policy=frozen["policy"],
        examples=examples, variant=variant, yield_seconds=yield_seconds)

    def on_event(event):
        if event["event"] == "page" and progress:
            progress(event["page"])

    final = _call(config, PATHS["invoice"], body, on_event, frozen, post=post, id_token=id_token)
    return final["pipeline"], final["evidence"]


def send_document(*, work_name, filename, content, page_count, document_type, frozen, regions=None,
                  yield_seconds=contract.YIELD_SECONDS, on_read=None, on_extracted=None, on_unread=None,
                  post=None, id_token=None):
    """对账三类文档交给 Cloud Run 读：返回旧线上管线结果（``legacy``）；读的过程中按事件回调看板进度
    （读完几页 / 一页抽完 / 一页读不出，与本机执行同一时机）。"""
    config = _config()
    body = contract.document_request(site=frappe.local.site, work=work_name, filename=filename, content=content,
        page_count=page_count, document_type=document_type, environment=frozen["environment"], regions=regions,
        yield_seconds=yield_seconds)

    def on_event(event):
        kind = event["event"]
        if kind == "read" and on_read:
            on_read(int(event.get("pages") or 1))
        elif kind == "extracted" and on_extracted and not event.get("reread"):
            on_extracted(int(event.get("page") or 0), event.get("text") or "")
        elif kind == "unread" and on_unread:
            on_unread(int(event.get("page") or 0))

    return _call(config, PATHS["document"], body, on_event, frozen, post=post, id_token=id_token)["legacy"]


def _config():
    config = settings()
    if config is None:
        raise RuntimeError("Remote OCR is not enabled")
    return config


def _call(config, path, body, on_event, frozen, *, post=None, id_token=None):
    """拿名额 → 放开数据库连接 → 发出并按行读回复 → 结果行；非「读完」照本机方式抛出。"""
    bucket = budget.scope("cloudrun", frozen["environment"].get("GCP_PROJECT", ""), "cowork", path.rsplit("/", 1)[-1])
    with budget.permit(bucket, work.JOB_TIMEOUT, **({"rpm": config["rpm"]} if config["rpm"] else {})):
        # 等 Cloud Run 回复的十几秒里不占数据库连接（几十份同时在等时连接数不涨）；回来后第一条查询自动重连。
        pool.release_db()
        try:
            token = (id_token or _id_token)(config["url"])
        except Exception as exc:  # 还没发出任何付费调用：稍后重试
            raise Deferred("remote_token_unavailable", 15) from exc
        try:
            response = (post or requests.post)(config["url"] + path, json=body, timeout=(30, READ_IDLE_SECONDS),
                                                stream=True, headers={"Authorization": "Bearer " + token})
        except (requests.ConnectionError, requests.Timeout) as exc:
            # 没连上 / 发送中断：Cloud Run 可能已开始读。检查点在存储桶里，下次接着读；不在这里判结果。
            raise Deferred("remote_unreachable", 15) from exc
        final = None
        try:
            if response.status_code in (429, 500, 502, 503, 504):
                raise Deferred("remote_http_" + str(response.status_code), 15)
            if response.status_code != 200:
                raise RemoteError("remote_http_" + str(response.status_code))
            try:
                for raw in response.iter_lines(chunk_size=8192):
                    if not raw:
                        continue
                    event = contract.parse_line(raw)
                    if event["event"] == "result":
                        final = event
                        break
                    on_event(event)
            except (requests.RequestException, ValueError) as exc:
                raise Deferred("remote_interrupted", 15) from exc
        finally:
            response.close()
    if final is None:
        # 连接正常结束却没有结果行：Cloud Run 实例中途没了。检查点会判断哪些调用已付费 / 结果不明。
        raise Deferred("remote_interrupted", 15)
    status = final["status"]
    if status == "done":
        return final
    if status == "deferred":
        raise Deferred(final.get("reason") or "remote_deferred", final.get("delay") or 15)
    if status == "uncertain":
        raise Uncertain(final.get("reason") or "Remote provider result is unknown")
    if status == "refused":
        raise ProviderError(final.get("code") or "ocr.provider_http_error", http_status=final.get("http_status"),
                            provider_code=final.get("provider_code"))
    if final.get("code") in contract.ENGINE_ERRORS:
        raise getattr(engine_errors, final["code"])(str(final.get("error_code") or "ocr.engine_error"),
            stage=str(final.get("stage") or ""), page_number=int(final.get("page_number") or 0),
            detail=str(final.get("detail") or ""))
    raise RemoteError(str(final.get("code") or "remote_error"))
