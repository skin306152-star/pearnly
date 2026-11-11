"""Cowork 识别单在共用层「同一张票只写一次」里的声明（hooks ``pearnly_ledger_tickets``，`pearnly_core.ledger_tickets`；复核 5 批之第 1 批）。

同一张票 = 同一工作区、同一客户公司、同一条连接与账套里身份键相同（`ticket_identity`，随写入维护在 ``ticket_key`` 列）、
身份是精确的（一方没有税号只算近似，不跳）。只看我们自己的推送记录（识别单上的推送状态）：Express / MR.ERP 里会计手录的同号照写。
"""
import frappe

HISTORY = "Cowork OCR History"
PUSH_RECORD = "Cowork Push Record"
EXACT = "exact"
SCAN = 20
STATES = {"success": "entered", "pending": "queued"}


def _book(value) -> str:
    """账套键按同一个比（推送前存的是选的写法 ``FLOWOP``，推送后存规范化的 ``flowop``，11-09 实验栈）。"""
    return str(value or "").strip().casefold()


def twin(doc_id) -> dict:
    """``{"doc_id", "docnum", "state", "key"}``：同一张票已由另一条写成（entered）或正在写（queued）；认出身份但没写过的只回
    ``{"key"}``（写之前按它加锁）；认不出身份的回 {}。写成的另带核账本要的 ``ledger`` / ``connection`` / ``account_set`` /
    ``direction``（那条推送记录写进的账本、连接、账套与进销方向；``docnum`` 是账本里的单号，录过的票先核账本，A 线 11-11）。"""
    from pearnly_core import ledger_tickets

    row = frappe.db.get_value(HISTORY, doc_id, ["name", "workspace", "client", "ticket_key", "ticket_level", "push_connection",
                                                "push_account_set"], as_dict=True)
    if not isinstance(row, dict) or not row.get("ticket_key") or row.get("ticket_level") != EXACT \
            or not row.get("push_connection"):
        return {}
    key = ":".join(str(part or "") for part in (row.workspace, row.client, row.push_connection, _book(row.push_account_set),
                                                row.ticket_key))
    for other in frappe.get_all(HISTORY, filters={"workspace": row.workspace, "ticket_key": row.ticket_key, "name": ["!=", row.name],
                                                  "push_connection": row.push_connection,
                                                  "last_push_status": ["in", list(STATES)]},
                                fields=["name", "client", "push_account_set", "last_push_status", "last_push_record"],
                                order_by="modified desc", limit_page_length=SCAN):
        if (other.client or "") != (row.client or "") or _book(other.push_account_set) != _book(row.push_account_set):
            continue
        state = STATES[other.last_push_status]
        found = {"doc_id": other.name, "docnum": "", "state": state, "key": key}
        if state == ledger_tickets.ENTERED and other.last_push_record:
            found.update(_ledger(other.last_push_record))
        return found
    return {"key": key}


def _ledger(record) -> dict:
    """那条推送记录写进哪里：账本里的单号、连接、账套、进销方向（载荷里写的）、账本类型（按连接的适配器）。"""
    import json

    row = frappe.db.get_value(PUSH_RECORD, record, ["external_docnum", "connection", "account_key", "payload_json"], as_dict=True)
    if not row:
        return {}
    try:
        payload = json.loads(row.payload_json or "{}")
    except ValueError:
        payload = {}
    adapter = frappe.db.get_value("Cowork ERP Connection", row.connection, "adapter") if row.connection else ""
    return {"docnum": str(row.external_docnum or ""), "connection": str(row.connection or ""),
            "account_set": str(row.account_key or ""), "direction": str((payload or {}).get("direction") or ""),
            "ledger": "mrerp" if adapter == "mrerp" else "express"}
