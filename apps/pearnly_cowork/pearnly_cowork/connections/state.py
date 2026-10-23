"""Current reads and scope keys shared by native connection services."""
import frappe
from frappe import _
from pearnly_core.express_rules.targets.identity import normalize_express_account_key
from pearnly_cowork.targets.projection import normalize_scope
from . import access, model

CONNECTION, SNAPSHOT, REFRESH = access.TYPES


def name(value):
    if not isinstance(value, str) or not value or len(value) > 140:
        frappe.throw(_("connection.not_found"))
    return value


def read(doctype, value, *, lock=True):
    if not frappe.db.get_value(doctype, name(value), "name", for_update=lock):
        frappe.throw(_("connection.not_found"))
    return frappe.get_doc(doctype, value, for_update=lock)


def heads(doc):
    return model.parse(doc.projection_heads, {})


def scope(account, adapter=None):
    if adapter == "express" and account not in (None, "", "@endpoint"):
        account = normalize_express_account_key(account)
    kind, key = normalize_scope(None if account in (None, "", "@endpoint") else account)
    return kind, key, model.fingerprint([kind, key])
