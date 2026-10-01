"""Cloud Run 回调站点（任务派发，见 ``ocr.remote.tasks``）：结果写好了 / 读的过程中的进度。

- 身份：只认谷歌签发、受众正是本站回调地址、签发对象是识别服务账号（``tasks.invoker``）且邮箱已验证的身份令牌
  （放在 ``X-Pearnly-Callback`` 头里：``Authorization: Bearer`` 会被 Frappe 当成自己的 OAuth 令牌拦下）。谷歌公钥按其
  缓存时间缓存，遇到不认识的 kid 才重取。别的站点的令牌（受众不同）在这里过不去。
- 结果回调只做两件事：把「结果在哪」记在这份识别任务的检查点里（按派发号，重复回调写同一行），唤醒这份任务
  （``work.wake``）。读结果、复核权限、存结果、扣费都在识别任务里照旧做——这里不碰任何业务数据，也不信回调里的结果内容。
- 进度回调只作展示：按识别任务的类别交给产品登记的函数（hooks ``cowork_remote_progress``），送丢、重复都不影响结果。
- 幂等：同一派发号的结果只记一次（同一行覆盖为同一内容）；已结束的任务、别的站点的、对不上的一律回 200 不处理
  （不让 Cloud Run 反复重试）；只有令牌不对回 401、站点没开任务派发回 404。
"""
import json
import threading
import time

import frappe

from pearnly_core.execution import work as core_work
from pearnly_cowork.ocr.remote import contract, tasks

HEADER = "X-Pearnly-Callback"
CERTS = "https://www.googleapis.com/oauth2/v1/certs"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
ACTIVE = ("queued", "running", "deferred")
KINDS = ("cowork.invoice", "cowork.vat_check", "cowork.recon_document")
_certs = {"value": None, "until": 0.0, "refreshed": 0.0}
_lock = threading.Lock()
# 遇到不认识的 kid 时最多这么久强制重取一次公钥（伪造的令牌不能让每个请求都去谷歌等公钥、占住网页线程）。
REFRESH_SECONDS = 60
# 验签不通过记错误日志，每个进程最多这么久记一次（防刷屏）。
REFUSED_LOG_SECONDS = 300
_refused = {"at": 0.0}
PROGRESS_KEY = "cowork.remote:progress"


class Refused(Exception):
    pass


def google_certs(*, refresh=False, http=None, clock=time.time):
    """谷歌签身份令牌用的公钥（``{kid: 证书}``），按回复的 ``Cache-Control: max-age`` 缓存。"""
    import requests

    with _lock:
        fresh = _certs["value"] and _certs["until"] > clock()
        if fresh and (not refresh or clock() - _certs["refreshed"] < REFRESH_SECONDS):
            return _certs["value"]
    response = (http or requests).get(CERTS, timeout=(5, 10))
    response.raise_for_status()
    age = 3600
    for part in (response.headers.get("Cache-Control") or "").split(","):
        name, _sep, value = part.strip().partition("=")
        if name == "max-age" and value.isdigit():
            age = int(value)
    with _lock:
        _certs.update(value=response.json(), until=clock() + max(60, min(age, 86400)), refreshed=clock())
        return _certs["value"]


def verify(token, audience, email, *, certs=None):
    """核对身份令牌：签名（谷歌公钥）、未过期、受众、签发方、签发对象邮箱已验证且就是识别服务账号。不对抛 ``Refused``。"""
    from google.auth import jwt

    if not isinstance(token, str) or token.count(".") != 2:
        raise Refused("missing token")
    keys = certs or google_certs()
    try:
        header = jwt.decode_header(token)
        if header.get("kid") not in keys and certs is None:
            keys = google_certs(refresh=True)
        claims = jwt.decode(token, certs=keys, audience=audience, clock_skew_in_seconds=30)
    except (ValueError, KeyError) as exc:
        raise Refused("invalid token") from exc
    if claims.get("iss") not in ISSUERS or claims.get("email") != email or claims.get("email_verified") is not True:
        raise Refused("wrong signer")
    return claims


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive():
    config = tasks.settings()
    if config is None:
        frappe.local.response.http_status_code = 404
        return {"ok": 0}
    try:
        verify(frappe.get_request_header(HEADER), tasks.callback_url(config), tasks.invoker(config))
    except Refused as refused:
        frappe.local.response.http_status_code = 401
        _log_refused(str(refused))
        return {"ok": 0}
    try:
        body = contract.parse_callback(json.loads(frappe.request.get_data() or b"{}"))
    except (ValueError, TypeError):
        return {"ok": 0, "reason": "invalid"}
    if body["site"] != frappe.local.site:
        return {"ok": 0, "reason": "site"}
    row = frappe.db.get_value(core_work.DT, body["work"], ["kind", "status", "arguments", "user"], as_dict=True)
    if not row or row.kind not in KINDS or row.status not in ACTIVE:
        return {"ok": 0, "reason": "finished"}
    if body["type"] in ("reply", "failed"):
        with core_work.context(body["work"]):
            core_work.checkpoint(tasks.REPLY_KEY + body["dispatch"], "reply",
                                 body["reply"] if body["type"] == "reply" else {"failed": True})
            _progressed()
        core_work.wake(body["work"])
        return {"ok": 1}
    if body["type"] == "progress":
        apply_progress(row, body)
    with core_work.context(body["work"]):
        _progressed()
    return {"ok": 1}


def _progressed():
    """记下「云上还有进展」的时刻（``work.call_progress`` 的最近一次进展据此算）：付费调用的检查点在云上、库里看不到，
    否则对账等识别时「还在一页页读完就延长」永远不会触发（2026-10-01 复查）。只更新这一行的时间，不算付费调用。"""
    core_work.checkpoint(PROGRESS_KEY, core_work.PROGRESS, {"at": time.time()})


def _log_refused(reason):
    now = time.time()
    if now - _refused["at"] < REFUSED_LOG_SECONDS:
        return
    _refused["at"] = now
    try:
        frappe.log_error(title="Cowork OCR callback refused", message=reason[:500])
    except Exception:
        pass


def apply_progress(row, body):
    """进度事件交给产品（只作展示）。按派发号记住已应用到第几个事件：重复 / 乱序送到的不再应用；结果到了时
    识别任务只补上没应用过的（``tasks.call``）。"""
    paths = (frappe.get_hooks("cowork_remote_progress") or {}).get(row.kind) or []
    if not paths:
        return
    key = tasks.progress_key(body["work"], body["dispatch"])
    state = frappe.cache.get_value(key) or {"seq": 0}
    start = state["seq"] - body["seq"]
    if start < 0 or start >= len(body["events"]):
        return  # 前面的还没到（乱序）或这批已经应用过
    events = body["events"][start:]
    try:
        frappe.get_attr(paths[-1])(json.loads(row.arguments or "{}"), events, state, user=row.user)
    except Exception:
        frappe.log_error(title="cowork remote progress", message=frappe.get_traceback()[-1500:])
    state["seq"] = body["seq"] + len(body["events"])
    frappe.cache.set_value(key, state, expires_in_sec=86400)
