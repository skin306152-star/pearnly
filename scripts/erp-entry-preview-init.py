"""Seed only the dedicated, synthetic ERP entry lab; no external calls or cleanup."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("erp-purchase-preview-init.py")))
lab = runpy.run_path(str(Path(__file__).with_name("erp-purchase-lab-verify.py")))
lab["connect"]()
import frappe
from pearnly_erp_portal.access.setup import sync_roles
from pearnly_erp_portal.products.service import create_product
from pearnly_erp_portal.masters import company

sync_roles()
for index, label in enumerate(("ERP 预览一店", "ERP 预览二店")):
    name = frappe.db.get_value("ERP Company", {"company_name": label}, "name")
    if not name:
        result = company.create_company(company_name=label, warehouse_name=label + "仓库", pos_enabled=True)
        name = result["name"]
    company.update_company(name, pos_enabled=True)
    if index == 0:
        frappe.defaults.set_user_default("company", name, "purchase-preview@example.test")
for title, barcode, price in (("测试咖啡豆", "8850000000001", 120), ("测试茶叶", "8850000000002", 80),
                             ("尚未入库商品", "8850000000003", 30)):
    if not frappe.db.exists("ERP Product", {"product_name": title}):
        create_product(product_name=title, barcode=barcode, unit="件", unit_price=price, default_cost=price / 2)
for doctype, field, title, tax in (("ERP Supplier", "supplier_name", "M C Winner Co., Ltd.", "0125549007435"),
                                  ("ERP Customer", "customer_name", "测试客户", "0100000000001")):
    if not frappe.db.exists(doctype, {field: title}):
        frappe.get_doc({"doctype": doctype, field: title, "tax_id": tax, "is_active": 1,
                        "branch_type": "branch", "branch_no": "0001", "party_type": "company",
                        "address": "Bangkok · 本地演示资料"}).insert(ignore_permissions=True)
from pearnly_erp_portal.access import menu
from pearnly_erp_portal.team.service import add_member
staff = "menu-preview@example.test"
if not frappe.db.exists("User", staff):
    add_member(username="ERPMenuPreview", email=staff, password="erp-pos-lab-only",
               roles=[menu.role("purchase")],
               companies=[frappe.db.get_value("ERP Company", {"company_name": "ERP 预览一店"}, "name")])
    frappe.db.set_value("User", staff, "language", "zh")
frappe.db.commit()
frappe.clear_cache()
frappe.destroy()
print("ERP entry preview fixtures ready; two synthetic companies, no production data")
