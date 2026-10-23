"""Bounded transaction lookups; selecting a master does not grant its management page."""
import frappe
from frappe import _

from pearnly_erp_portal.access import guard, rules

KINDS = {
    "product": ("ERP Product", "product_name", ("code", "barcode", "qr_payload")),
    "supplier": ("ERP Supplier", "supplier_name", ("express_code", "tax_id", "name")),
    "customer": ("ERP Customer", "customer_name", ("express_code", "tax_id", "name")),
}
FIELDS = {
    "product": ("name", "product_name", "code", "barcode", "qr_payload", "base_unit", "unit",
                "unit_price", "price_set", "default_cost", "vat_applicable", "default_location",
                "track_batch", "track_expiry"),
    "supplier": ("name", "supplier_name", "express_code", "tax_id", "branch_type", "branch_no", "branch_name", "address"),
    "customer": ("name", "customer_name", "express_code", "tax_id", "branch_type", "branch_no", "party_type", "address"),
}


def match_party(kind, name, *, tax_id=None, branch_type=None, branch_no=None):
    """Resolve only an unambiguous active master; a tax identity outranks spelling."""
    doctype, title, _codes = KINDS[kind]
    filters = {"is_active": 1}
    if tax_id:
        filters["tax_id"] = str(tax_id).strip()
        if branch_type == "branch":
            filters.update(branch_type="branch", branch_no=str(branch_no or "").strip())
        elif branch_type in ("head", "head_office"):
            filters["branch_type"] = ["in", ["head", "head_office"]]
    elif name:
        filters[title] = name
    else:
        return None
    rows = frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=2)
    if len(rows) > 1:
        frappe.throw(_("Several matches found. Please select a record."))
    return rows[0] if rows else None


@frappe.whitelist()
def api_choices(kind, context, txt="", scan=False):
    if context not in ("purchase", "sales") or kind not in KINDS:
        frappe.throw(_("Invalid value"))
    if (kind == "supplier" and context != "purchase") or (kind == "customer" and context != "sales"):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    guard.require(context + ".read")
    doctype, title, codes = KINDS[kind]
    text = str(txt or "").strip()[:500]
    exact = scan in (True, 1, "1", "true")
    if exact and not text:
        return []
    fields = list(FIELDS[kind])
    if kind == "product" and not guard.cost_visible():
        fields.remove("default_cost")
    filters = {"is_active": 1}
    matches = [[field, "=" if exact else "like", text if exact else f"%{text}%"]
               for field in (title, *codes)] if text else None
    # This endpoint intentionally grants only a bounded selection projection, under
    # transaction capability. Generic master CRUD still requires its own module role.
    rows = frappe.get_all(doctype, filters=filters, or_filters=matches, fields=fields,
                          order_by=f"{title} asc, name asc", limit_page_length=20)
    if exact and kind == "product":
        parents = frappe.get_all("ERP Product Unit", filters={"barcode": text}, pluck="parent", limit_page_length=20)
        if parents:
            extra = frappe.get_all(doctype, filters={**filters, "name": ["in", parents]}, fields=fields,
                                   limit_page_length=20)
            rows = list({row.name: row for row in [*rows, *extra]}.values())
    return rows
