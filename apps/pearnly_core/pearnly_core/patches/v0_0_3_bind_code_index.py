"""Keep per-user code regeneration from locking unrelated operators' codes."""
import frappe


def execute():
    frappe.db.add_index("Pearnly LINE Bind Code", ["user", "used"], "operator_active_codes")
