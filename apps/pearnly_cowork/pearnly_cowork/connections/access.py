"""Dedicated service authorization; generic native CRUD never grants access."""
import frappe
from frappe import _
from pearnly_core.shell import entitlements
from . import model

TYPES = ("Cowork ERP Connection", "Cowork ERP Snapshot", "Cowork ERP Refresh")
_TOKEN = object()


def deny():
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    deny()


def native_query(user=None):
    # All/Guest read ensures this is reached before the legacy shares-only path.
    deny()


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES:
        deny()


def guard(doc):
    if doc.flags.cowork_connection_write is not _TOKEN:
        deny()


def write(doc):
    doc.flags.cowork_connection_write = _TOKEN
    doc.flags.ignore_permissions = True
    doc.flags.notify_update = False
    doc.flags.ignore_version = True
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def _current(query, values):
    # Current reads, including when the caller already established an RR view.
    return frappe.db.sql(query + " LOCK IN SHARE MODE", values, as_dict=True)


def roles(actor):
    return {r.role for r in _current("SELECT role FROM `tabHas Role` WHERE parent=%s AND parenttype='User'", (actor,))}


def require_actor(actor=None, *, management=False):
    actor = actor or frappe.session.user
    feature = "master_data" if management else "intake"
    role = "Cowork Admin" if management else "Cowork Intake"
    found = _current("SELECT enabled FROM `tabUser` WHERE name=%s", (actor,))
    if (not actor or actor == "Guest" or frappe.conf.get("pearnly_product") != "cowork"
            or feature not in entitlements() or not found or not found[0].enabled):
        deny()
    granted = roles(actor)
    if role not in granted and (management or "Cowork Admin" not in granted):
        deny()
    return actor


def require_scope(workspace, client=None, *, actor=None):
    actor = actor or frappe.session.user
    rows = _current("SELECT enabled FROM `tabCowork Workspace` WHERE name=%s", (workspace,))
    if not rows or not rows[0].enabled:
        deny()
    if client:
        rows = _current("SELECT enabled FROM `tabCowork Client` WHERE name=%s AND workspace=%s", (client, workspace))
        if not rows or not rows[0].enabled:
            deny()
    if "Cowork Admin" not in roles(actor):
        grants = {(r.allow, r.for_value) for r in _current(
            "SELECT `allow`,for_value FROM `tabUser Permission` WHERE user=%s AND apply_to_all_doctypes=1", (actor,))}
        if ("Cowork Workspace", workspace) not in grants or (client and ("Cowork Client", client) not in grants):
            deny()


def require_connection(doc, *, actor=None, management=False, enabled=True):
    actor = require_actor(actor, management=management)
    require_scope(doc.workspace, doc.client, actor=actor)
    if enabled and not doc.enabled:
        frappe.throw(_("connection.disabled"), frappe.PermissionError)
    if not management:
        if actor not in model.parse(doc.allowed_users, []):
            deny()
    return actor


def require_assignee(actor, workspace, client=None):
    found = _current("SELECT enabled FROM `tabUser` WHERE name=%s", (actor,))
    if (not actor or actor == "Guest" or not found or not found[0].enabled
            or not {"Cowork Intake", "Cowork Push Log", "Cowork Admin"}.intersection(roles(actor))):
        deny()
    require_scope(workspace, client, actor=actor)
