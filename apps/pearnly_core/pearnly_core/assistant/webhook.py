"""引擎回调：页面关着也由服务端接手工具调用（ADR-019，清单第 16 项，用户 2026-10-11 定现在做）。

在引擎的控制台登记一个回调地址，订阅会话空闲事件（订哪些、怎么认由适配器说，`Adapter.wake`）；会话停下来等
Pearnly 工具结果时，引擎向本站 `receive` 发一条只带会话编号的通知（Standard Webhooks 签名，密钥在站点配置
`pearnly_assistant_webhook_secret`，只存配置不进仓库）。这里只做「叫醒」：验签、按事件编号去重、找到对话，
排一个后台任务去读事件列表、以会话主人的身份执行还在等的调用（`toolrun`，与推送线程、页面兜底共用一条执行记录，
同一次调用只执行一遍）。

- 回应要快、不能跳转：引擎每个事件最多送 3 次，失败就丢；回 3xx 会让这个回调地址被自动停用。
- 会话不是本站的：按建会话时记下的 `pearnly_site` 原样转给那个站点（同一套签名，它自己再验）；只转一次。
- 有请求才处理，不开定时轮询、不起常驻进程（零常驻花费）。
"""
import json
import time

import frappe

from pearnly_core.assistant import config, contracts, engine, toolrun

SESSION = "Pearnly Assistant Session"
SEEN_TTL = 24 * 3600
FORWARDED = "X-Pearnly-Forwarded"
FORWARD_TIMEOUT = 10
JOB_TIMEOUT = 30 * 60


def _reply(status: int, text: str):
    frappe.local.response.http_status_code = status
    return {"status": text}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive():
    secret = frappe.conf.get("pearnly_assistant_webhook_secret")
    if not secret:
        # 还没配密钥（刚在控制台登记、密钥还没写进站点配置）：回 200，免得引擎把连续失败的地址自动停用
        return {"status": "not_configured"}
    body = frappe.request.get_data(as_text=True)
    headers = dict(frappe.request.headers)
    try:
        contracts.verify_webhook(secret, headers, body, time.time())
        event = json.loads(body)
        event_id = contracts.event_id(event.get("id"))
    except (contracts.ContractError, ValueError, AttributeError):
        return _reply(401, "rejected")
    remote = engine.adapter().wake(event)
    if not remote:
        return {"status": "ignored"}
    try:
        remote = contracts.session_id(remote)
    except contracts.ContractError:
        return {"status": "ignored"}
    from pearnly_core.execution import state as pearnly_state

    # 同一事件重送（引擎重试、转发）只处理一次
    if not pearnly_state.store().set(pearnly_state.store().make_key(f"pearnly_assistant:webhook:{event_id}"), 1,
                                     nx=True, ex=SEEN_TTL):
        return {"status": "duplicate"}
    row = _local(remote)
    if row:
        frappe.enqueue("pearnly_core.assistant.webhook.run_pending", queue="long", timeout=JOB_TIMEOUT,
                       job_id=f"assistant-tools-{remote}", deduplicate=True,
                       conversation=row.session_id, remote=remote, user=row.user)
        return {"status": "queued"}
    return {"status": _forward(remote, body, headers)}


def _local(remote: str):
    """本站哪个对话用着这段远端会话（第一段就是对话编号；接续后的在 `remote_ids` 里）。"""
    fields = ["session_id", "user", "engine_code"]
    row = frappe.db.get_value(SESSION, {"session_id": remote}, fields, as_dict=True)
    if not row:
        found = frappe.get_all(SESSION, filters={"remote_ids": ["like", f'%"{remote}"%']}, fields=fields, limit=1)
        row = found[0] if found else None
    return row if row and row.engine_code == config.engine_code() else None


def _forward(remote: str, body: str, headers: dict) -> str:
    """不是本站的会话：转给建它的站点（同一引擎账号下多个站点共用一个回调地址时）。"""
    if any(k.lower() == FORWARDED.lower() for k in headers):
        return "unknown"
    try:
        site = str(engine.adapter().metadata(remote).get("pearnly_site") or "")
    except Exception:
        return "unknown"
    if not site or site == frappe.local.site or not contracts.site_host(site):
        return "unknown"
    import requests

    keep = {k: v for k, v in headers.items() if k.lower().startswith("webhook-") or k.lower() == "content-type"}
    try:
        requests.post(f"https://{site}/api/method/pearnly_core.assistant.webhook.receive", data=body.encode(),
                      headers={**keep, FORWARDED: "1"}, timeout=FORWARD_TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        return "forward_failed"
    return "forwarded"


def run_pending(conversation: str, remote: str, user: str) -> None:
    """后台：读事件列表，把还在等的 Pearnly 工具调用以会话主人的身份执行（已执行过的由执行记录挡住）。"""
    frappe.set_user(user)
    # 要用户在对话里点「允许」的，执行记录登记成等待、不执行，等他点（`toolrun.execute`）
    for call in contracts.pending_calls(engine.adapter().events(remote, newest_first=True)):
        toolrun.execute(conversation, remote, call)
