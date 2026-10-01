# -*- coding: utf-8 -*-
"""商品与多单位的存储层。

来源：ec2e5c957e6b ``services/products/units.py`` 的表与行（一个商品多个售卖单位，各自
换算成基础单位、各自条码与售价），以及旧侧 ``products`` 表那六列（基础单位、批量、有效期、
称重、最低库存、默认成本）。

判定复用上一批的纯模块：名称呈现用 `product_names`，单位字段与数值用 `product_units`。
本模块只负责读文档、写文档、过权限，并把旧表的两条硬约束落到原生：

* **单一默认售卖单位**：某一行要当默认时，先把该商品其它行的默认标记清掉（旧侧
  ``_clear_default`` 的行为，落成子表上的明确动作）；
* **同一商品下单位名不重复**：旧侧靠 ``UNIQUE (tenant_id, product_id, unit_name)``，原生
  在写入前检查（子表本身没有这个约束）。
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from pearnly_cowork.access import scope
from pearnly_cowork.masters import product_names, product_units

PRODUCT = "Cowork Product"
# 旧侧 products 表本次新增的六列 + 名称三语（呈现规则在 product_names 里）。
PRODUCT_TEXT_FIELDS = ("name_th", "name_en", "name_zh", "sku", "base_unit")
PRODUCT_FLAG_FIELDS = ("track_batch", "track_expiry", "is_weighed")
PRODUCT_NUMBER_FIELDS = ("min_stock", "default_cost")
DEFAULT_BASE_UNIT = "ชิ้น"


def require_manager():
    scope.require("master_data")
    if not scope.administrator():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def require_reader():
    if not scope.allowed_any((*scope.READ_FEATURES, "master_data")):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def _project(doc):
    row = {field: doc.get(field) for field in
           ("name", "workspace", "display_name", "sku", "base_unit", "enabled",
            *PRODUCT_FLAG_FIELDS, *PRODUCT_NUMBER_FIELDS)}
    row["names"] = product_names.product_name_object(doc)
    row["units"] = [{"unit_name": unit.unit_name,
                     "factor_to_base": flt(unit.factor_to_base, 3),
                     "barcode": unit.barcode, "price": flt(unit.price, 2),
                     "is_default_sell": cint(unit.is_default_sell)}
                    for unit in (doc.get("units") or [])]
    return row


def _clean_text(value, limit=None):
    text = "" if value is None else str(value).strip()
    return text[:limit] if limit else text


def _unit_rows(units):
    """Validate the incoming unit rows: needed fields, no duplicate names, one default.

    The numeric fields go through the deployed conversion (Decimal(str(v))), so a
    value like "0.1" is stored exactly rather than as the nearest float.
    """
    rows = []
    seen = set()
    defaults = 0
    for index, raw in enumerate(units or [], start=1):
        if not isinstance(raw, dict):
            frappe.throw(_("The product units are invalid."))
        name = _clean_text(raw.get("unit_name"), 40)
        if not name:
            frappe.throw(_("Every unit needs a name."))
        key = name.casefold()
        if key in seen:
            frappe.throw(_("This product already has a unit called {0}.").format(name))
        seen.add(key)
        try:
            factor = product_units.coerce_number(raw.get("factor_to_base"))
            price = product_units.coerce_number(raw.get("price"))
        except Exception as error:
            frappe.throw(_("The product units are invalid."))
        if factor is None:
            frappe.throw(_("Every unit needs a factor to the base unit."))
        is_default = 1 if raw.get("is_default_sell") else 0
        defaults += is_default
        rows.append({"unit_name": name, "factor_to_base": float(factor),
                     "barcode": _clean_text(raw.get("barcode"), 80) or None,
                     "price": float(price) if price is not None else None,
                     "is_default_sell": is_default})
    if defaults > 1:
        # The deployed code clears the others whenever one is set, so two rows asking
        # to be the default is a caller mistake rather than something to guess at.
        frappe.throw(_("Only one sell unit can be the default."))
    return rows


def _apply(product, values, *, creating):
    """Apply the incoming fields; a name is required when creating, optional on update."""
    provided = _clean_text(values.get("display_name") or values.get("name"), 200)
    if provided:
        product.display_name = provided
    elif creating or not product.display_name:
        frappe.throw(_("A product needs a name."))
    for field in PRODUCT_TEXT_FIELDS:
        if field in values:
            limit = 40 if field == "base_unit" else 200 if field != "sku" else 80
            product.set(field, _clean_text(values[field], limit))
    if not product.base_unit:
        product.base_unit = DEFAULT_BASE_UNIT
    for field in PRODUCT_FLAG_FIELDS:
        if field in values:
            product.set(field, 1 if values[field] else 0)
    for field in PRODUCT_NUMBER_FIELDS:
        if field in values:
            raw = values[field]
            if raw in (None, ""):
                product.set(field, None)
                continue
            try:
                product.set(field, float(product_units.coerce_number(raw)))
            except Exception:
                frappe.throw(_("The product fields are invalid."))
    if "enabled" in values or "is_active" in values:
        product.enabled = 1 if (values.get("enabled", values.get("is_active"))) else 0
    if "units" in values:
        product.set("units", [])
        for row in _unit_rows(values["units"]):
            product.append("units", row)


def list_products(workspace, start=0, limit=50, include_inactive=False):
    require_reader()
    scope.require_scope(workspace)
    start, limit = max(0, int(start)), max(1, min(int(limit), 500))
    filters = {"workspace": workspace}
    if not include_inactive:
        filters["enabled"] = 1
    return frappe.get_list(PRODUCT, filters=filters,
        fields=["name", "workspace", "display_name", "sku", "base_unit", "enabled",
            "name_th", "name_en", "name_zh"],
        order_by="enabled desc, display_name asc, name asc",
        limit_start=start, limit_page_length=limit)


def get_product(workspace, name):
    require_reader()
    scope.require_scope(workspace)
    doc = frappe.get_doc(PRODUCT, name)
    if doc.workspace != workspace:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return _project(doc)


def save_product(workspace, values, name=None):
    """Create a product, or update the named one (units are replaced when given)."""
    require_manager()
    scope.require_scope(workspace)
    if not isinstance(values, dict):
        frappe.throw(_("The product fields are invalid."))
    if name:
        doc = frappe.get_doc(PRODUCT, name)
        if doc.workspace != workspace:
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
    else:
        doc = frappe.get_doc({"doctype": PRODUCT, "workspace": workspace, "enabled": 1})
    _apply(doc, values, creating=not name)
    if name:
        doc.save()
    else:
        doc.insert()
    return _project(doc)


def delete_product(workspace, name):
    """Delete a product and its unit rows (child rows go with the parent)."""
    require_manager()
    scope.require_scope(workspace)
    doc = frappe.get_doc(PRODUCT, name)
    if doc.workspace != workspace:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    frappe.delete_doc(PRODUCT, name, ignore_permissions=True)
    return {"name": name, "deleted": True}
