"""Cowork-only guards for native Core Work document reads and mutations.

Frappe grants Administrator every role permission before has_permission hooks.
Document lifecycle guards therefore protect paid checkpoints independently of
role checks. Core's internal SQL/checkpoint updates remain unchanged.
"""
from contextlib import contextmanager

import frappe
from frappe import _

from pearnly_cowork.ocr.execution.access import WORK

_CREATE_CAPABILITY = object()
_CREATE_FLAG = "_cowork_work_creation"


def _cowork(values):
    return bool(values and (values.get("product") == "cowork"
                or str(values.get("kind") or "").startswith("cowork.")))


def _previous(doc):
    # Consult the stored identity even when a submitted native document claims
    # to be new or changes its product/kind to escape Cowork's write guard.
    return frappe.db.get_value(WORK, doc.name, ["product", "kind"], as_dict=True) if doc.name else None


def _protected(doc, previous=None):
    return _cowork(doc) or _cowork(previous)


def _creation_allowed(doc, previous):
    allowed = (not previous and doc.is_new()
               and frappe.flags.get(_CREATE_FLAG) is _CREATE_CAPABILITY)
    if allowed and frappe.db.sql("""SELECT name FROM `tabDocShare`
        WHERE share_doctype=%s AND share_name=%s
          AND (COALESCE(`read`,0)<>0 OR COALESCE(`write`,0)<>0
               OR COALESCE(`share`,0)<>0 OR COALESCE(`submit`,0)<>0)
        LIMIT 1""", (WORK, doc.name)):
        # A previously inserted share must not become an active grant when this
        # deterministic Work identity is created. Revoked audit rows are safe.
        frappe.throw(_("Recognition tasks cannot be shared separately from their originals."), frappe.PermissionError)
    return allowed


@contextmanager
def work_creation():
    """Internal admission capability: wrap only Core work.submit, never an RPC."""
    previous = frappe.flags.get(_CREATE_FLAG)
    frappe.flags[_CREATE_FLAG] = _CREATE_CAPABILITY
    try:
        yield
    finally:
        if previous is None:
            frappe.flags.pop(_CREATE_FLAG, None)
        else:
            frappe.flags[_CREATE_FLAG] = previous


def mutation(doc, method=None):
    previous = _previous(doc)
    if _protected(doc, previous) and not _creation_allowed(doc, previous):
        frappe.throw(_("Use the Cowork OCR service to change recognition tasks."), frappe.PermissionError)


def deny_delete(doc, method=None):
    if _protected(doc, _previous(doc)):
        frappe.throw(_("Recognition tasks cannot be deleted through the document API."), frappe.PermissionError)


class CoworkWorkGuard:
    def check_permission(self, permtype="read", permlevel=None):
        previous = _previous(self)
        if _protected(self, previous) and not (permtype == "create" and _creation_allowed(self, previous)):
            frappe.throw(_("Use the Cowork OCR service to access recognition tasks."), frappe.PermissionError)
        return super().check_permission(permtype=permtype, permlevel=permlevel)

    def has_permission(self, permtype="read", *, debug=False, user=None):
        previous = _previous(self)
        if _protected(self, previous) and not (permtype == "create" and _creation_allowed(self, previous)):
            return False
        return super().has_permission(permtype=permtype, debug=debug, user=user)
