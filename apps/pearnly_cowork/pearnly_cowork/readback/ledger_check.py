"""Cowork 核账本（hooks ``pearnly_ledger_check["express"]``，共用入口 `pearnly_core.ledger_tickets.check_present`）：经 Cowork 自己的
CC 桌面版按需请求通道（`companion.request_service`）发一件 ``express_doc_check``（CC 1.12.20 起），只读单头、回每张在不在。

``connection`` 是 Cowork 的 Express 连接、``account_set_key`` 是这份账套的键（同回读）。此刻没有电脑能做（不在线、没有一台够得到
这份账套、CC 桌面版太旧还不认这种请求）不建请求、回空（共用入口整批记 unknown）；等到上限还没读好也回空，并删掉这件请求
（电脑晚些读好也没人取）。只读：不写账、不改推送记录。
"""
import time
from uuid import uuid4

import frappe

from pearnly_cowork.companion import request_service
from pearnly_cowork.connections import service as connections
from pearnly_core import ledger_tickets
from pearnly_core.express_rules.targets.identity import normalize_express_account_key

KIND = "express_doc_check"
POLL_SECONDS = 2


def _account(connection, account_set_key):
    """连接目录里的这本账套 ``{key, path, root}``；不在（或不是 Express 连接）回 None。"""
    if connections.adapter_of(connection) != "express":
        return None
    wanted = normalize_express_account_key(account_set_key)
    for row in (connections.targets(connection) or {}).get("account_sets") or []:
        if wanted and normalize_express_account_key(row.get("source_id")) == wanted:
            attributes = row.get("attributes") or {}
            return {"key": wanted, "path": str(attributes.get("path") or row.get("source_id") or ""),
                    "root": str(attributes.get("root") or "")}
    return None


def check(connection, account_set_key, items, *, ledger="express", wait=60) -> dict:
    if ledger != "express":
        return {}
    account = _account(connection, account_set_key)
    if not account:
        return {}
    if request_service.waiting(connection, KIND, account_key=account["key"], user=frappe.session.user):
        return {}
    params = ledger_tickets.request_params(account["path"], account["root"], items)
    made = request_service.create(KIND, connection, params, "doc_check:" + uuid4().hex, account_key=account["key"])
    frappe.db.commit()  # 电脑在另一个请求里领：先提交，它才看得到
    deadline = time.monotonic() + max(0, int(wait or 0))
    found = {}
    while True:
        frappe.db.rollback()  # 结束这次的读快照：每次都读到电脑刚交上来的
        state = request_service.status(made["request"])["status"]
        if state == "done":
            content, _meta = request_service.result(made["request"])
            found = ledger_tickets.parse_result(content)
            break
        if state not in ("requested", "leased") or time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    request_service.drop(made["request"])
    frappe.db.commit()
    return found
