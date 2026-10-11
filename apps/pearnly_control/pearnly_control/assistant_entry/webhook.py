"""入口 fatcc.com 统一接收引擎回调，按会话归属转给租户站点（ADR-019，清单第 16 项生产版）。

生产只在引擎控制台登记这一个地址（订阅会话空闲事件），开户不再需要去控制台：租户站点建对话时已在计费服务登记
会话归属（`routes`），这里验签、去重后查到站点，把原文与签名头原样转过去，由站点自己再验签、以会话主人执行
（`pearnly_core.assistant.webhook`）。签名密钥在本站与各租户站点配置 `pearnly_assistant_webhook_secret`
（开户的 `set_assistant` 推到站点；本站由 `bootstrap-ai-entry.py` 写入），不进仓库。

回应要快、不能跳转（引擎每个事件最多送 3 次；3xx 会让地址被停用）；查不到归属的照样回 200，免得连续失败被停用。
"""
import json
import time

import frappe
import requests

from pearnly_control.assistant_entry import contracts, entry

SEEN_TTL = 24 * 3600
FORWARD_TIMEOUT = 10
TARGET = "/api/method/pearnly_core.assistant.webhook.receive"


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive():
    from pearnly_core.assistant import contracts as assistant_contracts
    from pearnly_core.assistant import engines

    entry._require()
    secret = frappe.conf.get("pearnly_assistant_webhook_secret")
    if not secret:
        return {"status": "not_configured"}
    body = frappe.request.get_data(as_text=True)
    headers = dict(frappe.request.headers)
    try:
        assistant_contracts.verify_webhook(secret, headers, body, time.time())
        event = json.loads(body)
        event_id = assistant_contracts.event_id(event.get("id"))
    except (assistant_contracts.ContractError, ValueError, AttributeError):
        frappe.local.response.http_status_code = 401
        return {"status": "rejected"}
    session = engines.wake(event)
    if not session:
        return {"status": "ignored"}
    if not frappe.cache.set(frappe.cache.make_key(f"pearnly_entry:webhook:{event_id}"), 1, nx=True, ex=SEEN_TTL):
        return {"status": "duplicate"}
    try:
        site = contracts.site(entry._billing("route", session=session).get("site"))
    except (contracts.ContractError, frappe.ValidationError):
        frappe.clear_messages()
        return {"status": "unknown"}
    keep = {k: v for k, v in headers.items() if k.lower().startswith("webhook-") or k.lower() == "content-type"}
    try:
        requests.post(f"https://{site}{TARGET}", data=body.encode(), headers={**keep, "X-Pearnly-Forwarded": "1"},
                      timeout=FORWARD_TIMEOUT, allow_redirects=False)
    except requests.RequestException:
        return {"status": "forward_failed"}
    return {"status": "forwarded"}
