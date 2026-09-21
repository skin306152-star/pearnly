"""公司主档与公司资料。

同一张表两种用法：列表页看登记过哪些公司（公司主档），资料页看默认公司那条的详细资料
（公司资料）。默认公司不存在时自动建一条，否则新租户一来就先看到空页面。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.masters import contracts

DOCTYPE = "ERP Company"
NOT_FOUND = "Company not found"
NAME_REQUIRED = "A company needs a name"
INVALID = "Invalid value"

LIST_FIELDS = ("name", "company_name", "short_name", "tax_id", "branch", "phone",
               "contact_person", "contact_email", "promptpay_id", "doc_prefix",
               "fiscal_year_start_month", "is_default", "is_active", "address", "note")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def ensure_default() -> str:
    """默认公司：没有就在第一次需要时建一条空档，资料页才有东西可填。"""
    existing = frappe.db.get_value(DOCTYPE, {"is_default": 1}, "name")
    if existing:
        return existing
    any_company = frappe.db.get_value(DOCTYPE, {}, "name", order_by="creation asc")
    if any_company:
        frappe.db.set_value(DOCTYPE, any_company, "is_default", 1)
        return any_company
    doc = frappe.get_doc({"doctype": DOCTYPE, "company_name": frappe.conf.get("pearnly_company_name")
                          or "My Company", "is_default": 1, "is_active": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def list_companies(*, include_inactive=False, limit=50) -> list[dict]:
    guard.require("company.read")
    filters = {} if include_inactive else {"is_active": 1}
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="is_default desc, company_name asc",
                           limit_page_length=int(limit))


def create_company(*, company_name, **fields) -> dict:
    guard.require("company.manage")
    name = contracts.clean(company_name, contracts.MAX_NAME)
    if not name:
        frappe.throw(_(NAME_REQUIRED))
    doc = frappe.get_doc({"doctype": DOCTYPE, "company_name": name, "is_active": 1})
    _apply(doc, fields)
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def get_profile(name=None) -> dict:
    """公司资料：不带名字就是默认公司。"""
    guard.require("company.read")
    doc = _get(name or ensure_default())
    return {field: doc.get(field) for field in LIST_FIELDS}


def update_company(name, **fields) -> dict:
    guard.require("company.manage")
    doc = _get(name)
    _apply(doc, fields)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def update_profile(**fields) -> dict:
    """改公司资料（默认公司）。"""
    guard.require("company.manage")
    return update_company(ensure_default(), **fields)


def _apply(doc, fields) -> None:
    for field, limit in (("company_name", contracts.MAX_NAME), ("short_name", contracts.MAX_SHORT),
                         ("tax_id", contracts.MAX_TAX_ID), ("branch", contracts.MAX_CONTACT),
                         ("phone", contracts.MAX_PHONE), ("contact_person", contracts.MAX_CONTACT),
                         ("contact_email", contracts.MAX_EMAIL),
                         ("promptpay_id", contracts.MAX_PROMPTPAY),
                         ("doc_prefix", 20), ("address", contracts.MAX_ADDRESS),
                         ("note", contracts.MAX_NOTE)):
        if field not in fields:
            continue
        try:
            value = contracts.clean(fields[field], limit)
        except ValueError:
            frappe.throw(_(INVALID))
        if field == "company_name" and not value:
            frappe.throw(_(NAME_REQUIRED))
        doc.set(field, value)
    if "fiscal_year_start_month" in fields:
        if not contracts.fiscal_month_ok(fields["fiscal_year_start_month"]):
            frappe.throw(_("The fiscal year start month must be between 1 and 12"))
        doc.fiscal_year_start_month = int(fields["fiscal_year_start_month"])
    for field in ("is_default", "is_active"):
        if field in fields:
            doc.set(field, 1 if fields[field] in (True, 1, "1", "true") else 0)
