"""Give the existing counters their company (one tenant, several companies).

`ERP POS Terminal` gained a required `company`: a counter belongs to a company and sells from that
company's warehouse. Old counters have no company — take it from their warehouse, and if that
warehouse has none either, fall back to the tenant's default company. Idempotent: only fills blanks.
"""
import frappe

from pearnly_erp_portal.masters import company

DOCTYPE = "ERP POS Terminal"


def execute():
    default = company.ensure_default()
    for name, warehouse in frappe.get_all(DOCTYPE, fields=["name", "warehouse"],
                                          filters={"company": ["in", ("", None)]}, as_list=True):
        owner = frappe.db.get_value("ERP Warehouse", warehouse, "company") if warehouse else None
        frappe.db.set_value(DOCTYPE, name, "company", owner or default, update_modified=False)
    frappe.db.commit()
    frappe.clear_cache()
