# -*- coding: utf-8 -*-
"""采购单据的服务层与接口（ERP 产品第一条纵向切片）。

这一层做的事，就是旧系统 `services/purchase/*` 的业务含义在新架构里的落点：
建单、列单、汇总。差别在于：

- 数据访问走 Site 内的 DocType，不再持有全局连接池；
- 金额在 DocType 的 validate 里统一重算，接口不接受前端传来的合计；
- 权限先按"登录 + 角色"收口，正式权限码等角色模型落地后接入
  （清单里已经写明 ERP 产品**不得**使用 accounting / tax / pos / workorder 的码）。

金额一律 Decimal；时间由框架按 UTC 存储。
"""

from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import flt

DOCTYPE = "Purchase Document"


def _require_user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return user


def _q2(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def create_purchase(
    supplier_name: str,
    doc_date: str,
    lines: list[dict],
    *,
    doc_kind: str = "goods",
    doc_no: str | None = None,
    due_date: str | None = None,
    currency: str = "THB",
    fx_rate=1,
    has_vat: bool = True,
    discount_total=0,
    wht_amount=0,
    rounding=0,
    supplier_tax_id: str | None = None,
    external_ref: str | None = None,
    dedupe_key: str | None = None,
    image_sha256: str | None = None,
    notes: str | None = None,
    status: str = "draft",
) -> str:
    """建一张采购单。金额由 DocType 重算，这里只管组装与校验。"""
    _require_user()

    if not isinstance(lines, list) or not lines:
        frappe.throw(_("Provide at least one line item"))

    if dedupe_key and frappe.db.exists(DOCTYPE, {"dedupe_key": dedupe_key}):
        existing = frappe.db.get_value(DOCTYPE, {"dedupe_key": dedupe_key}, "name")
        frappe.throw(_("This document has already been recorded: {0}").format(existing))

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "doc_kind": doc_kind,
        "supplier_name": supplier_name,
        "supplier_tax_id": supplier_tax_id,
        "doc_no": doc_no,
        "doc_date": doc_date,
        "due_date": due_date,
        "status": status,
        "currency": currency,
        "fx_rate": fx_rate,
        "has_vat": 1 if has_vat else 0,
        "discount_total": discount_total,
        "wht_amount": wht_amount,
        "rounding": rounding,
        "external_ref": external_ref,
        "dedupe_key": dedupe_key,
        "image_sha256": image_sha256,
        "notes": notes,
        "lines": [
            {
                "line_no": idx,
                "item_type": line.get("item_type", "goods"),
                "description": line["description"],
                "product_ref": line.get("product_ref"),
                "qty": line.get("qty", 1),
                "unit": line.get("unit"),
                "unit_price": line.get("unit_price", 0),
                "discount": line.get("discount", 0),
                "vat_rate": line.get("vat_rate", 7),
            }
            for idx, line in enumerate(lines, start=1)
        ],
    })
    doc.insert()
    frappe.db.commit()
    return doc.name


def list_purchases(*, supplier_name=None, status=None, from_date=None, to_date=None, limit=50) -> list[dict]:
    _require_user()
    filters: dict = {}
    if supplier_name:
        filters["supplier_name"] = supplier_name
    if status:
        filters["status"] = status
    if from_date and to_date:
        filters["doc_date"] = ("between", [from_date, to_date])
    return frappe.get_all(
        DOCTYPE,
        filters=filters,
        fields=[
            "name", "doc_kind", "supplier_name", "doc_no", "doc_date", "due_date",
            "status", "currency", "grand_total", "net_payable", "payment_status",
        ],
        order_by="doc_date desc, creation desc",
        limit_page_length=int(limit),
    )


def summary(*, from_date=None, to_date=None) -> dict:
    """期间汇总。金额求和用 Decimal，避免浮点误差累积。"""
    _require_user()
    filters: dict = {"status": ("!=", "void")}
    if from_date and to_date:
        filters["doc_date"] = ("between", [from_date, to_date])

    rows = frappe.get_all(
        DOCTYPE, filters=filters,
        fields=["grand_total", "vat_amount", "net_payable", "paid_amount"],
        limit_page_length=0,
    )
    total = sum((_q2(r.grand_total) for r in rows), Decimal("0"))
    vat = sum((_q2(r.vat_amount) for r in rows), Decimal("0"))
    payable = sum((_q2(r.net_payable) for r in rows), Decimal("0"))
    paid = sum((_q2(r.paid_amount) for r in rows), Decimal("0"))
    return {
        "count": len(rows),
        "grand_total": str(total),
        "vat_amount": str(vat),
        "net_payable": str(payable),
        "paid_amount": str(paid),
        "outstanding": str(payable - paid),
    }


# ── 接口层（产品前端调这里）────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_create_purchase(**kwargs):
    # 框架会把 cmd 一起塞进 kwargs，必须摘掉再传给业务函数
    kwargs.pop("cmd", None)
    if isinstance(kwargs.get("lines"), str):
        kwargs["lines"] = frappe.parse_json(kwargs["lines"])
    return {"name": create_purchase(**kwargs)}


@frappe.whitelist()
def api_list_purchases(**kwargs):
    kwargs.pop("cmd", None)
    return list_purchases(**kwargs)


@frappe.whitelist()
def api_summary(**kwargs):
    kwargs.pop("cmd", None)
    return summary(**kwargs)
