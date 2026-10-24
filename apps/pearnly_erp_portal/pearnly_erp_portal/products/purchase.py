"""Resolve/create an item inside an authorized purchase transaction. No commit."""
from decimal import Decimal

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.products import contracts, service


def resolve(line, *, create=True, currency_rate=1, company=None):
    guard.require("purchase.write")
    if line.get("item_type", "goods") == "service":
        return line
    name = line.get("product")
    auto = not name  # 按品名 / 编码自动认的：单位跟商品走（`selection.own_unit`）
    if not name:
        for field in ("barcode", "code"):
            if line.get(field):
                name = service.find(field, line[field], company)  # 别家公司 Express 的同码商品不认
                if name:
                    break
    if not name:
        from pearnly_erp_portal.masters.selection import match_product

        name, found = match_product(line["description"], company)  # 这家公司该用的商品里认（别家 Express 的不认）
        if found > 1:
            frappe.throw(_("Please select an Item"))
    if not name and create:
        product_name = contracts.require_name(line["description"])
        # 编码一律由本系统续号发（用户 2026-09-26：采购与销售同一条规则）。票面上印的是供应商
        # 自己的货号，只拿来在上面匹配已有商品，不当成我们的商品编码。
        code = service._allocate_code()
        barcode = service._text(line.get("barcode"), contracts.MAX_BARCODE)
        if barcode and service.taken("barcode", barcode):
            # 自动建的是本地档（各家公司都看得到）：同一条码别家公司已有就不带条码建，不让整张单交不上（10-24 审查）
            barcode = None
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
        if getattr(item, "express_company", None) and company and item.express_company != company:
            frappe.throw(_("Product not found"))  # 别家公司 Express 的商品不能用在这家的单上（10-24 验收）
        if not item.is_active:
            frappe.throw(_("Item is disabled"))
        line.update(product=name, code=item.code, barcode=item.barcode,
                    unit=line.get("unit") or item.base_unit or item.unit)
        if auto:
            from pearnly_erp_portal.masters.selection import own_unit

            line["unit"] = own_unit(item, line["unit"])
    return line
