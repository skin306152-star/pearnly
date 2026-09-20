"""Product-only native User layout extensions; no parallel account form or widget."""
import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def ensure_profile_fields():
    if not frappe.conf.get("pearnly_product"):
        return
    create_custom_fields({"User": [
        {"fieldname": "pearnly_company", "label": "Company Name", "fieldtype": "Data",
         "read_only": 1, "is_virtual": 1, "insert_after": "first_name"},
        {"fieldname": "pearnly_domain", "label": "Your access domain", "fieldtype": "Data",
         "read_only": 1, "is_virtual": 1, "insert_after": "pearnly_company"},
        {"fieldname": "pearnly_change_password", "label": "account.changePassword", "fieldtype": "Button",
         "insert_after": "mute_sounds"},
    ]}, update=True)
    # Native Customize Form uses this DocType-level Property Setter for order.
    # Preserve every existing field, moving only mute/password into User Details.
    meta = frappe.get_meta("User", cached=False)
    moved = ["mute_sounds", "pearnly_change_password"]
    names = [df.fieldname for df in meta.fields if df.fieldname not in moved]
    anchor = names.index("time_zone") + 1
    names[anchor:anchor] = moved
    make_property_setter("User", None, "field_order", json.dumps(names), "Text", for_doctype=True)
    make_property_setter("User", "first_name", "label", "Username", "Data")
