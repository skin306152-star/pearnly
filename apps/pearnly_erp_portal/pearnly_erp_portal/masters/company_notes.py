"""ERP 公司的知识库（ADR-020，ERP 版 CC 按公司组织对话）：一条一句，团队共写；随批末系统消息带给 CC（`cc_context`）。

规则同 Cowork 公司主档的知识库（`pearnly_cowork.companies.notes`），纯规则就是同一份（共用层 `pearnly_core.assistant.team_notes`：
一条最多 500 字、每家最多 50 条、去掉控制字符并把空白合成一个空格）：

- 按创建时间从旧到新排；删就真删，不留历史版本；记下谁、何时写的，CC 经「允许」卡片写的记 `origin = cc` 与当时的对话。
- 权限由调用方核（`cc_companies.require_write`）；本模块只管形状与归属：改、删的条目必须属于这家公司。
- 表 `ERP Company Note` 只经本模块写（原生接口只给系统管理员读）。
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.assistant import team_notes

NOTE = "ERP Company Note"
COMPANY = "ERP Company"
NOTES_MAX = team_notes.NOTES_MAX
TEXT_MAX = team_notes.TEXT_MAX
FIELDS = ["name", "text", "origin", "created_by", "created_at", "updated_by", "updated_at", "creation"]
INVALID = "Note is empty or too long"
FULL = "Notes are full"
NOT_FOUND = "Note not found"


def clean(value) -> str:
    """规整一条说明；为空或超长拒绝。"""
    text = team_notes.normalize(value)
    if not team_notes.valid(text):
        frappe.throw(_(INVALID))
    return text


def listing(company) -> list:
    rows = frappe.get_all(NOTE, filters={"company": company}, fields=FIELDS, order_by="creation asc, name asc",
                          limit_page_length=NOTES_MAX * 2)
    return [{"id": row.name, "text": row.text or "", "origin": row.origin or "person",
             "updated_by": row.updated_by or row.created_by or "",
             "updated_at": str(row.updated_at or row.created_at or row.creation or "")} for row in rows]


def one(company, note):
    """这家公司的这一条；不是这家的、不存在的一律回「找不到」。"""
    if not isinstance(note, str) or not note or len(note) > 140:
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    row = frappe.db.get_value(NOTE, {"name": note, "company": company}, ["name", "text"], as_dict=True)
    if not row:
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return row


def save(company, text, note=None, *, origin="person", session="") -> dict:
    """没给 `note` 是新增，给了是修改（核对这条属于这家公司）；回存好的那条。"""
    text = clean(text)
    origin = origin if origin in team_notes.ORIGINS else "person"
    user, now = frappe.session.user, now_datetime()
    if note:
        one(company, note)
        doc = frappe.get_doc(NOTE, note, for_update=True)
        doc.update({"text": text, "updated_by": user, "updated_at": now})
        if origin == "cc":
            doc.update({"origin": "cc", "session": str(session or "")[:140]})
        doc.save(ignore_permissions=True)
    else:
        frappe.db.get_value(COMPANY, company, "name", for_update=True)  # 同一家公司同时加的排队数上限
        if frappe.db.count(NOTE, {"company": company}) >= NOTES_MAX:
            frappe.throw(_(FULL))
        doc = frappe.new_doc(NOTE)
        doc.update({"company": company, "text": text, "origin": origin,
                    "session": str(session or "")[:140] if origin == "cc" else "", "created_by": user,
                    "created_at": now, "updated_by": user, "updated_at": now})
        doc.insert(ignore_permissions=True)
    return {"id": doc.name, "text": doc.text, "origin": doc.origin, "updated_by": user, "updated_at": str(now)}


def delete(company, note) -> dict:
    row = one(company, note)
    frappe.db.delete(NOTE, {"name": row.name})
    return {"id": row.name, "text": row.text or ""}


__all__ = ["NOTES_MAX", "TEXT_MAX", "clean", "delete", "listing", "one", "save"]
