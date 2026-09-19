# -*- coding: utf-8 -*-
"""身份证录入的服务层与接口（DMS 第一条纵向切片）。

旧系统这条线的四步，在新架构里对应：

    上传证件 → 识别（recognized）→ 客户匹配（matched）→ 资料确认（confirmed）→ 推送（pushed）

**客户匹配的判据是证件号**：证件号相同默认视为同一客户，避免重复建档
（这是旧系统界面上明写的设计原则）。客户主数据在外部 MR.ERP DMS，
本地用 `DMS Customer Link` 记住"证件号 → DMS 客户引用"的对照。

推送状态的真值仍在 `erp_push_logs`（trigger='id_card'），本表只存引用。

⚠️ 证件号是个人敏感信息：不进列表返回、不写日志。
"""

import frappe
from frappe import _
from frappe.utils import now

INTAKE = "DMS ID Card Intake"
LINK = "DMS Customer Link"

# 列表接口**不返回**证件号，只返回末四位用于人工核对
LIST_FIELDS = [
    "name", "status", "match_result", "endpoint", "external_ref",
    "full_name", "birth_date", "push_log_ref", "creation",
]


def _require_user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return user


def mask_id_number(value: str | None) -> str:
    value = (value or "").strip()
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def create_id_card_intake(
    id_number: str,
    *,
    full_name: str | None = None,
    birth_date: str | None = None,
    address: str | None = None,
    gender: str | None = None,
    endpoint: str | None = None,
    id_card_image: str | None = None,
    recognition_raw: str | None = None,
    status: str = "uploaded",
) -> str:
    _require_user()
    id_number = (id_number or "").strip()
    if not id_number:
        frappe.throw(_("ID number is required"))

    doc = frappe.get_doc({
        "doctype": INTAKE,
        "status": status,
        "match_result": "unknown",
        "endpoint": endpoint,
        "id_number": id_number,
        "full_name": full_name,
        "birth_date": birth_date,
        "address": address,
        "gender": gender,
        "id_card_image": id_card_image,
        "recognition_raw": recognition_raw,
    })
    doc.insert()
    frappe.db.commit()
    return doc.name


def match_customer(name: str, *, external_customer_ref: str | None = None) -> dict:
    """客户匹配。证件号已在本地对照表里 → 视为已有客户，复用其 DMS 引用。

    只有确实匹配不到、且调用方给出了确定的 DMS 引用时，才记为新建客户。
    这一层不做"猜"：拿不准就停在 matched 之前，让人工确认。
    """
    _require_user()
    doc = frappe.get_doc(INTAKE, name)
    if doc.status not in ("uploaded", "recognized"):
        frappe.throw(_("Cannot match while the status is {0}").format(doc.status))

    existing = frappe.db.get_value(
        LINK, {"id_number": doc.id_number}, ["name", "external_customer_ref"], as_dict=True
    )

    if existing:
        doc.external_ref = existing.external_customer_ref
        doc.match_result = "existing"
        frappe.db.set_value(
            LINK, existing.name, "last_verified_at", now(), update_modified=False
        )
    elif external_customer_ref:
        link = frappe.get_doc({
            "doctype": LINK,
            "id_number": doc.id_number,
            "full_name": doc.full_name,
            "external_customer_ref": external_customer_ref,
            "endpoint": doc.endpoint,
            "is_new_customer": 1,
            "last_verified_at": now(),
        })
        link.insert()
        doc.external_ref = external_customer_ref
        doc.match_result = "new"
    else:
        frappe.throw(_("No existing customer matched and no DMS customer reference was given: confirm manually first"))

    doc.status = "matched"
    doc.save()
    frappe.db.commit()
    return {
        "name": doc.name,
        "status": doc.status,
        "match_result": doc.match_result,
        "external_ref": doc.external_ref,
    }


def confirm_intake(name: str, **fields) -> dict:
    """资料确认：这一步之后资料才成为可推送的业务数据。"""
    _require_user()
    doc = frappe.get_doc(INTAKE, name)
    if doc.status != "matched":
        frappe.throw(_("Customer matching must be done before confirming"))

    for key, value in fields.items():
        if hasattr(doc, key) and value is not None:
            setattr(doc, key, value)
    doc.status = "confirmed"
    doc.confirmed_by = frappe.session.user
    doc.confirmed_at = now()
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, "external_ref": doc.external_ref}


def queue_push(name: str) -> dict:
    """标记为待推送。真值仍以 erp_push_logs 为准，这里只落引用位。"""
    _require_user()
    doc = frappe.get_doc(INTAKE, name)
    if doc.status != "confirmed":
        frappe.throw(_("The details are not confirmed yet, so it cannot be pushed to DMS"))
    if not doc.external_ref:
        frappe.throw(_("There is no DMS customer reference, so it cannot be pushed"))
    doc.status = "pushed"
    doc.save()
    frappe.db.commit()
    return {
        "name": doc.name,
        "status": doc.status,
        "external_ref": doc.external_ref,
        "trigger": "id_card",
    }


def list_intake(*, status=None, limit=50) -> list[dict]:
    _require_user()
    filters = {"status": status} if status else {}
    rows = frappe.get_all(
        INTAKE, filters=filters, fields=LIST_FIELDS,
        order_by="creation desc", limit_page_length=int(limit),
    )
    for row in rows:
        row["id_number_masked"] = None  # 列表不返证件号
    return rows


def summary() -> dict:
    _require_user()
    rows = frappe.get_all(INTAKE, fields=["status", "match_result"], limit_page_length=0)
    by_status: dict[str, int] = {}
    new_customers = 0
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        if row.match_result == "new":
            new_customers += 1
    return {
        "total": len(rows),
        "by_status": by_status,
        "new_customers": new_customers,
        "reused_customers": sum(1 for r in rows if r.match_result == "existing"),
    }


# ── 接口层 ──────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_create_intake(**kwargs):
    kwargs.pop("cmd", None)
    return {"name": create_id_card_intake(**kwargs)}


@frappe.whitelist(methods=["POST"])
def api_match_customer(name, **kwargs):
    kwargs.pop("cmd", None)
    return match_customer(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_confirm_intake(name, **kwargs):
    kwargs.pop("cmd", None)
    return confirm_intake(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_queue_push(name, **kwargs):
    kwargs.pop("cmd", None)
    return queue_push(name)


@frappe.whitelist()
def api_list_intake(**kwargs):
    kwargs.pop("cmd", None)
    return list_intake(**kwargs)


@frappe.whitelist()
def api_summary(**kwargs):
    return summary()
