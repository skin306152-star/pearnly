"""由 Express 同步来的主档在 ERP 里不能改（ERP 当前台、Express 当账本，2026-10-22）。

用户 10-22：「只要是映射回来的都不应该可以改写主档信息，避免改后和 Express 那边不同步」。带 Express 编码的客户、供应商、商品、
仓库，凡是 Express 里有的字段（名称、税号、分店、地址、电话、编码、单位与换算、售价、条码、启用状态、归属）一律只认 Express：
要改在 Express 里改，下次同步带过来。Express 里没有的 ERP 自有字段（商品图片、分类、最低库存、备注之类）照常可改，不存在
两边不一致。主档同步自己写入时带 ``flags.express_sync`` 放行。各主档控制器在 ``validate`` 里调 :func:`guard`；
页面把同一批栏位设成只读（`public/desk` 四个主档页）。
"""
import frappe
from frappe import _

LOCKED = {
    "ERP Customer": ("customer_name", "party_type", "tax_id", "branch_type", "branch_no", "address", "contact_phone",
                     "is_active", "express_code", "express_company", "express_book"),
    "ERP Supplier": ("supplier_name", "tax_id", "branch_type", "branch_no", "address", "phone", "is_active",
                     "express_code", "express_company", "express_book"),
    "ERP Product": ("product_name", "code", "barcode", "base_unit", "unit", "unit_price", "price_set", "is_active",
                    "express_code", "express_stock", "express_company", "express_book"),
    "ERP Warehouse": ("warehouse_name", "company", "is_active", "express_code", "express_book"),
}
MESSAGE = "Maintained in Express"


def _same(left, right) -> bool:
    return (left or "") == (right or "") or (str(left or "") == str(right or ""))


def _units(doc):
    return [(row.unit_name, float(row.factor_to_base or 0)) for row in doc.get("units") or []]


def guard(doc):
    if doc.is_new() or doc.flags.express_sync:
        return
    before = doc.get_doc_before_save()
    if before is None or not before.get("express_code"):
        return
    changed = [field for field in LOCKED.get(doc.doctype, ()) if not _same(before.get(field), doc.get(field))]
    if doc.doctype == "ERP Product" and _units(before) != _units(doc):
        changed.append("units")
    if changed:
        frappe.throw(_(MESSAGE))
