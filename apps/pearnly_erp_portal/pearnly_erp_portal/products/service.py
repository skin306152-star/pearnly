"""商品主数据的服务端实现。

取数一律走 `frappe.get_list`（跟随角色权限），成本按岗位遮蔽后再返回；停用是软删除。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.products import contracts

DOCTYPE = "ERP Product"
UNIT_DOCTYPE = "ERP Product Unit"

NAME_REQUIRED = "A product needs a name"
CODE_EXISTS = "This product code already exists"
BARCODE_EXISTS = "This barcode already exists"
NOT_FOUND = "Product not found"
INVALID_VALUE = "Invalid value"

# 列表与详情一起用的字段。成本字段由 `_mask` 决定是否清空。
LIST_FIELDS = (
    "name", "product_name", "code", "barcode", "unit", "unit_price",
    "price_set", "vat_applicable", "is_active", "min_stock", "default_cost", "avg_cost",
    "image_url",
)


def _mask(rows):
    visible = rules.cost_visible(guard.current_roles())
    if isinstance(rows, dict):
        return contracts.mask_costs(rows, visible)
    return [contracts.mask_costs(dict(row), visible) for row in rows]


def _optional(value):
    try:
        return contracts.decimal_or_none(value)
    except ValueError:
        frappe.throw(_(INVALID_VALUE))


def _price_fields(value) -> dict:
    """价格与"是否设过价"必须一起写。

    Frappe 的 Currency 列不能存 NULL，所以"没设价"和"真的 ฿0"在库里是同一个数字。
    旧线上这点被当成事故处理过（扫码就地建品建出来的商品全部 ฿0 可售），所以这里
    用一个显式标记把两者区分开，收银台的零元闸读它而不是猜数字。
    """
    price = _optional(value)
    return {"unit_price": price or 0, "price_set": 1 if price is not None else 0}


def _text(value, limit):
    try:
        return contracts.clean_text(value, limit)
    except ValueError:
        frappe.throw(_(INVALID_VALUE))


def _assert_unique(code, barcode, *, exclude: str | None = None) -> None:
    """同租户内编码/条码唯一。站点本身就是租户边界，所以只需本站点内查重。"""
    for field, value, message in (
        ("code", code, CODE_EXISTS),
        ("barcode", barcode, BARCODE_EXISTS),
    ):
        if not value:
            continue
        filters = {field: value}
        if exclude:
            filters["name"] = ["!=", exclude]
        if frappe.db.exists(DOCTYPE, filters):
            frappe.throw(_(message))


def _get(name: str):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


def create_product(*, product_name, code=None, barcode=None, unit=None, unit_price=None,
                   vat_applicable=True, image_url=None, min_stock=None, default_cost=None,
                   qr_payload=None, note=None):
    guard.require("products.manage")
    try:
        product_name = contracts.require_name(product_name)
    except ValueError:
        frappe.throw(_(NAME_REQUIRED))
    code = _text(code, contracts.MAX_CODE)
    barcode = _text(barcode, contracts.MAX_BARCODE)
    _assert_unique(code, barcode)

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "product_name": product_name,
        "code": code,
        "barcode": barcode,
        "qr_payload": _text(qr_payload, contracts.MAX_QR),
        "unit": _text(unit, contracts.MAX_UNIT),
        **_price_fields(unit_price),
        "vat_applicable": 1 if vat_applicable in (True, 1, "1", "true") else 0,
        "image_url": _text(image_url, contracts.MAX_IMAGE_URL),
        "min_stock": _optional(min_stock),
        "default_cost": _optional(default_cost),
        "note": note,
        "is_active": 1,
    })
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name}


def list_products(*, search=None, include_inactive=False, limit=50):
    guard.require("products.view")
    filters: list = []
    if not include_inactive:
        filters.append(["is_active", "=", 1])
    if search:
        # 名称与条码都要能搜到：扫码后就地找品是收银台的主路径。
        filters.append(["product_name", "like", f"%{search}%"])
    return _mask(frappe.get_list(
        DOCTYPE, filters=filters, or_filters=(
            [["barcode", "like", f"%{search}%"]] if search else None),
        fields=list(LIST_FIELDS), order_by="modified desc", limit_page_length=int(limit),
    ))


def get_product(name: str):
    guard.require("products.view")
    doc = _get(name)
    data = {field: doc.get(field) for field in LIST_FIELDS}
    data["units"] = [
        {"unit_name": row.unit_name, "factor_to_base": row.factor_to_base,
         "barcode": row.barcode, "price": row.price, "is_default_sell": bool(row.is_default_sell)}
        for row in doc.get("units") or []
    ]
    return _mask(data)


def update_product(name: str, **changes):
    guard.require("products.manage")
    doc = _get(name)
    code = _text(changes.get("code"), contracts.MAX_CODE) if "code" in changes else doc.code
    barcode = (_text(changes.get("barcode"), contracts.MAX_BARCODE)
               if "barcode" in changes else doc.barcode)
    _assert_unique(code, barcode, exclude=doc.name)

    for field in ("code", "barcode", "qr_payload", "unit", "image_url"):
        if field in changes:
            doc.set(field, _text(changes[field], {
                "code": contracts.MAX_CODE, "barcode": contracts.MAX_BARCODE,
                "qr_payload": contracts.MAX_QR, "unit": contracts.MAX_UNIT,
                "image_url": contracts.MAX_IMAGE_URL,
            }[field]))
    if "product_name" in changes:
        try:
            doc.product_name = contracts.require_name(changes["product_name"])
        except ValueError:
            frappe.throw(_(NAME_REQUIRED))
    if "unit_price" in changes:
        for field, value in _price_fields(changes["unit_price"]).items():
            doc.set(field, value)
    for field in ("min_stock", "default_cost"):
        if field in changes:
            doc.set(field, _optional(changes[field]))
    if "vat_applicable" in changes:
        doc.vat_applicable = 1 if changes["vat_applicable"] in (True, 1, "1", "true") else 0
    if "is_active" in changes:
        doc.is_active = 1 if changes["is_active"] in (True, 1, "1", "true") else 0
    doc.save()
    frappe.db.commit()
    return {"name": doc.name}


def set_active(name: str, active):
    """停用/重新上架。旧线上商品是软删除，历史单据继续指向它。"""
    guard.require("products.manage")
    doc = _get(name)
    doc.is_active = 1 if active in (True, 1, "1", "true") else 0
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "is_active": bool(doc.is_active)}


def add_unit(name: str, *, unit_name, factor_to_base, barcode=None, price=None,
             is_default_sell=False):
    guard.require("products.manage")
    doc = _get(name)
    try:
        factor = contracts.unit_factor(factor_to_base)
    except ValueError:
        frappe.throw(_(INVALID_VALUE))
    unit_name = _text(unit_name, contracts.MAX_UNIT)
    barcode = _text(barcode, contracts.MAX_BARCODE)
    if barcode:
        for row in doc.get("units") or []:
            if row.barcode == barcode:
                frappe.throw(_(BARCODE_EXISTS))
    if is_default_sell in (True, 1, "1", "true"):
        for row in doc.get("units") or []:
            row.is_default_sell = 0
    doc.append("units", {
        "unit_name": unit_name,
        "factor_to_base": factor,
        "barcode": barcode,
        "price": _optional(price),
        "is_default_sell": 1 if is_default_sell in (True, 1, "1", "true") else 0,
    })
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "units": len(doc.units)}


@frappe.whitelist()
def api_list_products(**kwargs):
    kwargs.pop("cmd", None)
    return list_products(**kwargs)


@frappe.whitelist()
def api_get_product(name=None, **kwargs):
    kwargs.pop("cmd", None)
    return get_product(name)


@frappe.whitelist(methods=["POST"])
def api_create_product(**kwargs):
    kwargs.pop("cmd", None)
    return create_product(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_update_product(name=None, **kwargs):
    kwargs.pop("cmd", None)
    return update_product(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_active(name=None, active=None, **kwargs):
    kwargs.pop("cmd", None)
    return set_active(name, active)


@frappe.whitelist(methods=["POST"])
def api_add_unit(name=None, **kwargs):
    kwargs.pop("cmd", None)
    return add_unit(name, **kwargs)
