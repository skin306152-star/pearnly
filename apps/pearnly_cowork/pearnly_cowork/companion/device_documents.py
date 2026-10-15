"""Only this domain may persist CC Desktop computers; generic reads/shares never grant access (ADR-021)."""
from uuid import uuid4

import frappe
from frappe import _
from frappe.model.document import Document

DOCTYPE = 'Cowork CC Desktop Device'
_WRITE = object()
_IDENTITY = ('workspace',)
_RETIREMENT = ('retired_at', 'retired_by')


def deny():
    frappe.throw(_('Not Permitted'), frappe.PermissionError)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    deny()


def native_query(user=None):
    deny()


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def persist(doc):
    doc.flags.cowork_cc_desktop_device = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    doc.flags.notify_update = False
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True, ignore_version=True)


class DeviceDocument(Document):
    def _guard(self):
        if self.flags.cowork_cc_desktop_device is not _WRITE:
            deny()

    def autoname(self):
        self._guard()
        self.name = 'CCD-' + uuid4().hex[:20]

    def before_insert(self):
        self._guard()

    def before_validate(self):
        self._guard()

    def before_save(self):
        self._guard()

    def validate(self):
        self._guard()
        previous = self.get_doc_before_save()
        if not previous:
            return
        if any(self.get(key) != previous.get(key) for key in _IDENTITY):
            deny()
        if previous.retired_at and any(self.get(key) != previous.get(key) for key in _RETIREMENT):
            deny()
        # A claimed computer keeps its key digest; only a retired one gives it up, so the same computer can
        # come back as a new record.
        if previous.secret_digest and self.secret_digest != previous.secret_digest and not self.retired_at:
            deny()

    def check_permission(self, permtype='read', permlevel=None):
        self._guard()
        return True

    def has_permission(self, permtype='read', *, debug=False, user=None):
        return self.flags.cowork_cc_desktop_device is _WRITE

    def on_trash(self):
        deny()

    def before_rename(self, old, new, merge=False):
        deny()
