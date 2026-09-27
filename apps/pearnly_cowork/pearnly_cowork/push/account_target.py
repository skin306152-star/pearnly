"""编辑页里给一张票换推送账套（用户 2026-09-27：LINE 收单不在聊天里选账套，识别后自动挑，挑错 / 没挑出在这里改）。

只改账套、不改连接（目标 ERP 是入口选的）；已推送或推送中的票不能改；所选账套必须在这条连接
当前目录里、可用、可写；按记录版本防覆盖。推送时服务端与小助手照旧再核。
"""
import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.history import storage
from pearnly_cowork.targets.identity import normalize_express_account_key
from pearnly_cowork.connections import directory
from pearnly_cowork.connections.state import CONNECTION

_LOCKED = ("pending", "success")


def set_account(name, account_set_key, expected_version):
    scope.require("intake")
    doc = storage.read(name, for_update=True)
    doc.check_permission("write")
    if storage.revision(expected_version) != int(doc.record_version):
        storage.conflict()
    if doc.last_push_status in _LOCKED:
        frappe.throw(_("This record has already been pushed; its account set cannot be changed."))
    connection = doc.push_connection or ""
    owner = frappe.db.get_value(CONNECTION, connection, ["workspace", "client"], as_dict=True) \
        if connection else None
    if not owner or (owner.workspace, owner.client or "") != (doc.workspace, doc.client or ""):
        frappe.throw(_("erp.account_set_unavailable"))
    adapter, rows = directory.current(connection)
    key = normalize_express_account_key(account_set_key) if adapter == "express" else str(account_set_key or "").strip()
    usable = any(
        str(row.get("source_id") or "") == key and row.get("active", True) is not False
        and (row.get("attributes") or {}).get("writable", True) is not False
        for row in rows if isinstance(row, dict))
    if not key or not usable:
        frappe.throw(_("erp.account_set_unavailable"))
    frappe.db.set_value(storage.DOCTYPE, doc.name, {"push_account_set": key,
                                                   "record_version": int(doc.record_version) + 1})
    return storage.project(storage.read(doc.name))
