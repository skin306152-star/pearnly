"""Resolve/create an item inside an authorized purchase transaction. No commit."""
from decimal import Decimal

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.products import contracts, service


def resolve(line, *, create=True, currency_rate=1):
    guard.require("purchase.write")
    if line.get("item_type", "goods") == "service":
        return line
    name = line.get("product")
    if not name:
        for field in ("barcode", "code"):
            if line.get(field):
                name = frappe.db.get_value("ERP Product", {field: line[field]}, "name")
                if name:
                    break
    if not name:
        matches = frappe.get_all("ERP Product", filters={"product_name": line["description"],
                                 "is_active": 1}, pluck="name", limit_page_length=2)
        if len(matches) > 1:
            frappe.throw(_("Please select an Item"))
        name = matches[0] if matches else None
    if not name and create:
        product_name = contracts.require_name(line["description"])
        # 编码一律由本系统续号发（用户 2026-09-26：采购与销售同一条规则）。票面上印的是供应商
        # 自己的货号，只拿来在上面匹配已有商品，不当成我们的商品编码。
        code = service._allocate_code()
        barcode = service._text(line.get("barcode"), contracts.MAX_BARCODE)
        service._assert_unique(code, barcode)
        item = frappe.get_doc({"doctype": "ERP Product", "product_name": product_name,
            "code": code, "barcode": barcode, "unit": line.get("unit"),
            "base_unit": line.get("unit"), "unit_price": 0, "price_set": 0,
            "default_cost": Decimal(str(line.get("line_total", 0))) / Decimal(str(line["qty"])) * Decimal(str(currency_rate)),
            "cost_set": 1, "vat_applicable": int(float(line.get("vat_rate", 0)) > 0),
            "default_location": line.get("location"),
            "is_active": 1, "track_batch": int(bool(line.get("batch_no"))),
            "track_expiry": int(bool(line.get("expiry_date")))})
        # This capability is deliberately limited to creating a purchase's item;
        # ERP Purchase retains read-only permission on the general item master.
        item.insert(ignore_permissions=True)
        name = item.name
    if name:
        item = frappe.get_doc("ERP Product", name)
        if not item.is_active:
            frappe.throw(_("Item is disabled"))
        line.update(product=name, code=item.code, barcode=item.barcode,
                    unit=line.get("unit") or item.base_unit or item.unit)
    return line
