"""公司知识库（ADR-020）：一条一句，团队共写；随批末系统消息带给 CC（`context`）。

- 每家公司最多 `NOTES_MAX` 条、每条最多 `TEXT_MAX` 字；去掉控制字符、空白合成一个空格，不能为空。
- 按创建时间从旧到新排。删就真删，不留历史版本；记下谁、何时写的，CC 经「允许」卡片写的记 `origin = cc` 与当时的对话。
- 权限由调用方核（录入权限）；本模块只管形状与归属：改、删的条目必须属于这家公司。
"""
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.companies.access import NOTE, read, write

NOTES_MAX = 50
TEXT_MAX = 500
ORIGINS = ("person", "cc")
FIELDS = ["name", "text", "origin", "created_by", "created_at", "updated_by", "updated_at", "creation"]
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def clean(value) -> str:
    """规整一条说明；为空或超长抛 `cowork.company.note_invalid`。"""
    text = " ".join(_CONTROL.sub(" ", str(value or "")).split())
    if not text or len(text) > TEXT_MAX:
        frappe.throw(_("cowork.company.note_invalid"))
    return text


def listing(company) -> list:
    rows = frappe.get_all(NOTE, filters={"company": company}, fields=FIELDS, order_by="creation asc, name asc",
                          limit_page_length=NOTES_MAX * 2)
    return [{"id": row.name, "text": row.text or "", "origin": row.origin or "person",
             "updated_by": row.updated_by or row.created_by or "",
             "updated_at": str(row.updated_at or row.created_at or row.creation or "")} for row in rows]


def one(company, note) -> dict:
    """这家公司的这一条；不是这家的、不存在的一律回「找不到」。"""
    if not isinstance(note, str) or not note or len(note) > 140:
        frappe.throw(_("cowork.company.note_not_found"), frappe.DoesNotExistError)
    row = frappe.db.get_value(NOTE, {"name": note, "company": company}, ["name", "text"], as_dict=True)
    if not row:
        frappe.throw(_("cowork.company.note_not_found"), frappe.DoesNotExistError)
    return row


def save(company, text, note=None, *, workspace, origin="person", session="") -> dict:
    """没给 `note` 是新增，给了是修改（核对这条属于这家公司）；回存好的那条。"""
    text = clean(text)
    origin = origin if origin in ORIGINS else "person"
    user, now = frappe.session.user, now_datetime()
    if note:
        one(company, note)
        doc = read(NOTE, note, for_update=True)
        doc.update({"text": text, "updated_by": user, "updated_at": now})
        if origin == "cc":
            doc.update({"origin": "cc", "session": str(session or "")[:140]})
    else:
        frappe.db.get_value("Cowork Company", company, "name", for_update=True)  # 同一家公司同时加的排队数上限
        if frappe.db.count(NOTE, {"company": company}) >= NOTES_MAX:
            frappe.throw(_("cowork.company.notes_full"))
        doc = frappe.new_doc(NOTE)
        doc.update({"company": company, "workspace": workspace, "text": text, "origin": origin,
                    "session": str(session or "")[:140] if origin == "cc" else "", "created_by": user,
                    "created_at": now, "updated_by": user, "updated_at": now})
    write(doc)
    return {"id": doc.name, "text": doc.text, "origin": doc.origin, "updated_by": user, "updated_at": str(now)}


def delete(company, note) -> dict:
    row = one(company, note)
    frappe.db.delete(NOTE, {"name": row.name})
    return {"id": row.name, "text": row.text or ""}


__all__ = ["NOTES_MAX", "TEXT_MAX", "clean", "delete", "listing", "one", "save"]
