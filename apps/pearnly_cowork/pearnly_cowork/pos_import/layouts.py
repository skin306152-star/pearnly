"""POS 导出版式的存取（原生 DocType ``Cowork POS Layout``）：按「工作空间 + 公司 + 表头指纹」记一份认好的列（问一次、记住）。

公司 = 推送连接 + 账套所属公司（同一家公司跨年度的账套共用，见 `service.company_key`）。认得出、不用问的版式第一次就记下
（``origin=auto``），同一版式以后直接用；人回答过的记 ``person``。只经本模块读写：原生接口的读、写、删、分享一律拒绝。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.pos_import import contracts

DOCTYPE = "Cowork POS Layout"
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def guard(doc):
    if doc.flags.cowork_pos_layout_write is not _WRITE:
        deny()


def _key(workspace, company, fingerprint):
    return hashlib.sha256(json.dumps([workspace, company, fingerprint], ensure_ascii=False).encode()).hexdigest()


def remembered(workspace, company, fingerprint):
    """记过的版式（``contracts.valid_layout`` 通过的）；没有回 None。调用方已核过录入权限。"""
    value = frappe.db.get_value(DOCTYPE, _key(workspace, company, fingerprint), "layout_json")
    if not value:
        return None
    try:
        layout = json.loads(value)
    except (TypeError, ValueError):
        return None
    return layout if contracts.valid_layout(layout) else None


def revision(layout) -> str:
    """记住的版式的修订（内容摘要）：答过、补过状态值就变；没有版式是空串。"""
    if not layout:
        return ""
    return hashlib.sha256(json.dumps(layout, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]


def remember(workspace, company, fingerprint, headers, layout, *, origin="person"):
    """记下 / 改掉这份版式（同一公司同一指纹只有一条）。"""
    if not contracts.valid_layout(layout) or not company or len(company) > 140 or origin not in ("auto", "person"):
        frappe.throw(_("pos.layout_invalid"))
    name = _key(workspace, company, fingerprint)
    existing = frappe.db.exists(DOCTYPE, name)
    doc = frappe.get_doc(DOCTYPE, name, for_update=True) if existing else frappe.get_doc({
        "doctype": DOCTYPE, "layout_key": name, "workspace": workspace, "company": company,
        "fingerprint": fingerprint})
    encoded = json.dumps(layout, ensure_ascii=False, sort_keys=True)
    if existing and doc.layout_json == encoded and (doc.origin == "person" or origin == "auto"):
        return name
    doc.update({"headers_json": json.dumps([str(h)[:200] for h in headers][:256], ensure_ascii=False),
                "layout_json": encoded, "origin": origin, "set_by": frappe.session.user, "set_at": now_datetime()})
    doc.flags.cowork_pos_layout_write = _WRITE
    doc.flags.ignore_version = True
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return name


def guard_delete(doc):
    if frappe.flags.get("cowork_pos_layout_delete") is not _WRITE:
        deny()


def forget(workspace, company, fingerprint) -> bool:
    """删掉这份版式：下次同版式的导出重新认、重新问。"""
    name = _key(workspace, company, fingerprint)
    if not frappe.db.exists(DOCTYPE, name):
        return False
    frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
    # 删除时框架重新取文档，对象上的标记带不过去：删除期间在本请求的标记上放同一个对象。
    frappe.flags.cowork_pos_layout_delete = _WRITE
    try:
        frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, force=True)
    finally:
        frappe.flags.cowork_pos_layout_delete = None
    return True


__all__ = ["DOCTYPE", "deny", "deny_query", "forget", "guard", "guard_delete", "remember", "remembered",
           "reject_share", "revision"]
