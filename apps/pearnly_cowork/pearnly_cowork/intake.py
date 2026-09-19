# -*- coding: utf-8 -*-
"""录入工作台的服务层与接口（Cowork 第一条纵向切片）。

链路：上传 → 识别（写 draft/recognized）→ 人工确认（confirmed）→
排队（queued）→ 推送（pushed）。推送渠道分两种：

- `companion`：由**小助手**在商家本机出站拉取后落库（云端不主动连入）；
- `cloud`：由云端直接推送。

推送状态**不以本表的字段为准**，真值在 `erp_push_logs`；这里只存引用。
"""

from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import now

DOCTYPE = "Intake Record"


def _require_user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return user


def _q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def create_intake(
    direction: str,
    *,
    source: str = "upload",
    doc_no: str | None = None,
    doc_date: str | None = None,
    counterparty_name: str | None = None,
    counterparty_tax_id: str | None = None,
    company_tax_id: str | None = None,
    net_amount=0,
    vat_amount=0,
    currency: str = "THB",
    category: str | None = None,
    attachment: str | None = None,
    recognition_raw: str | None = None,
    dedupe_key: str | None = None,
    image_sha256: str | None = None,
    status: str = "draft",
) -> str:
    _require_user()

    if dedupe_key and frappe.db.exists(DOCTYPE, {"dedupe_key": dedupe_key}):
        frappe.throw(_("This invoice has already been recorded: {0}").format(
            frappe.db.get_value(DOCTYPE, {"dedupe_key": dedupe_key}, "name")))

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "direction": direction,
        "source": source,
        "status": status,
        "doc_no": doc_no,
        "doc_date": doc_date,
        "counterparty_name": counterparty_name,
        "counterparty_tax_id": counterparty_tax_id,
        "company_tax_id": company_tax_id,
        "net_amount": net_amount,
        "vat_amount": vat_amount,
        "currency": currency,
        "category": category,
        "attachment": attachment,
        "recognition_raw": recognition_raw,
        "dedupe_key": dedupe_key,
        "image_sha256": image_sha256,
    })
    doc.insert()
    frappe.db.commit()
    return doc.name


def confirm_intake(name: str, *, net_amount=None, vat_amount=None, **fields) -> dict:
    """人工确认：这一步才把识别结果变成业务字段。"""
    _require_user()
    doc = frappe.get_doc(DOCTYPE, name)
    if doc.status not in ("draft", "recognized"):
        frappe.throw(_("Cannot confirm while the status is {0}").format(doc.status))

    for key, value in fields.items():
        if hasattr(doc, key) and value is not None:
            setattr(doc, key, value)
    if net_amount is not None:
        doc.net_amount = net_amount
    if vat_amount is not None:
        doc.vat_amount = vat_amount

    doc.status = "confirmed"
    doc.confirmed_by = frappe.session.user
    doc.confirmed_at = now()
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, "grand_total": str(doc.grand_total)}


def queue_for_push(name: str, *, channel: str = "companion") -> dict:
    """排队推送。渠道决定是小助手出站拉取，还是云端直接推。"""
    _require_user()
    if channel not in ("companion", "cloud"):
        frappe.throw(_("The push channel must be companion or cloud"))
    doc = frappe.get_doc(DOCTYPE, name)
    if doc.status != "confirmed":
        frappe.throw(_("Only confirmed documents can be queued for push"))
    doc.push_channel = channel
    doc.status = "queued"
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status, "channel": doc.push_channel}


def list_intake(*, status=None, direction=None, limit=50) -> list[dict]:
    _require_user()
    filters: dict = {}
    if status:
        filters["status"] = status
    if direction:
        filters["direction"] = direction
    return frappe.get_all(
        DOCTYPE, filters=filters,
        fields=["name", "direction", "source", "status", "doc_no", "doc_date",
                "counterparty_name", "grand_total", "currency",
                "push_channel", "push_status"],
        order_by="creation desc",
        limit_page_length=int(limit),
    )


def summary() -> dict:
    """按状态统计，并给进项/销项的金额合计。"""
    _require_user()
    rows = frappe.get_all(
        DOCTYPE, fields=["status", "direction", "grand_total"], limit_page_length=0
    )
    by_status: dict[str, int] = {}
    purchase = Decimal("0")
    sales = Decimal("0")
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        if row.status in ("confirmed", "queued", "pushed"):
            if row.direction == "purchase":
                purchase += _q2(row.grand_total)
            else:
                sales += _q2(row.grand_total)
    return {
        "total": len(rows),
        "by_status": by_status,
        "purchase_confirmed": str(purchase),
        "sales_confirmed": str(sales),
    }


# ── 给前端用的接口 ──────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_create_intake(**kwargs):
    kwargs.pop("cmd", None)
    return {"name": create_intake(**kwargs)}


@frappe.whitelist(methods=["POST"])
def api_confirm_intake(name, **kwargs):
    kwargs.pop("cmd", None)
    return confirm_intake(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_queue_for_push(name, **kwargs):
    kwargs.pop("cmd", None)
    return queue_for_push(name, **kwargs)


@frappe.whitelist()
def api_list_intake(**kwargs):
    kwargs.pop("cmd", None)
    return list_intake(**kwargs)


@frappe.whitelist()
def api_summary(**kwargs):
    kwargs.pop("cmd", None)
    return summary()
