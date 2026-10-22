"""由 Express 同步来的主档：身份类字段只认 Express（ERP 当前台、Express 当账本，2026-10-22）。

带 Express 编码的客户、供应商、商品、仓库，名称、税号、分店、编码、单位、启用状态这些字段在 ERP 里不能改（要改在 Express 里改，
下次同步带过来）；售价、地址、电话 Express 里常常是空的，不锁（同步只在 Express 有值时覆盖）。主档同步自己写入时带
``flags.express_sync`` 放行。各主档控制器在 ``validate`` 里调 :func:`guard`；页面同时把这些栏位设成只读。
"""
import frappe
from frappe import _

LOCKED = {
    "ERP Customer": ("customer_name", "party_type", "tax_id", "branch_type", "branch_no", "is_active",
                     "express_code", "express_company"),
    "ERP Supplier": ("supplier_name", "tax_id", "branch_type", "branch_no", "is_active", "express_code",
                     "express_company"),
    "ERP Product": ("product_name", "code", "base_unit", "unit", "is_active", "express_code", "express_stock",
                    "express_company"),
    "ERP Warehouse": ("warehouse_name", "company", "is_active", "express_code"),
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
