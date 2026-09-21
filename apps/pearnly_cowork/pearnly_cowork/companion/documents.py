"""Only this domain may persist credentials; generic reads/shares never grant access."""
import json

import frappe
from frappe import _
from frappe.model.document import Document

DOCTYPE = 'Cowork Express Agent Credential'
_WRITE = object()
_IDENTITY = ('record_id', 'site_name', 'connection', 'workspace', 'client', 'issuer',
             'token_generation', 'token_digest', 'token_tail', 'generation_key',
             'issued_request_digest', 'issued_at', 'branch', 'binding_generation')


def deny():
    frappe.throw(_('Use the Cowork Companion credential service to access credentials.'), frappe.PermissionError)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    deny()


def native_query(user=None):
    deny()


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def persist(doc):
    doc.flags.cowork_companion_credential = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    doc.flags.notify_update = False
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True, ignore_version=True)


class CredentialDocument(Document):
    def _guard(self):
        if self.flags.cowork_companion_credential is not _WRITE:
            deny()

    def autoname(self):
        self._guard()
        self.name = self.record_id

    def before_insert(self):
        self._guard()
        if frappe.db.sql('''SELECT name FROM `tabDocShare` WHERE share_doctype=%s AND share_name=%s
            AND (COALESCE(`read`,0)<>0 OR COALESCE(`write`,0)<>0 OR COALESCE(`share`,0)<>0
                 OR COALESCE(`submit`,0)<>0) LIMIT 1''', (self.doctype, self.record_id)):
            deny()

    def before_validate(self):
        self._guard()

    def before_save(self):
        self._guard()

    def validate(self):
        self._guard()
        previous = self.get_doc_before_save()
        if self.name != self.record_id or self.branch != 'legacy' or int(self.binding_generation or 0) != 0:
            deny()
        if previous:
            if any(self.get(key) != previous.get(key) for key in _IDENTITY):
                deny()
            old = json.loads(previous.request_receipts or '{}')
            new = json.loads(self.request_receipts or '{}')
            if any(new.get(key) != value for key, value in old.items()):
                deny()
            if previous.revoked_at and any(self.get(key) != previous.get(key)
                    for key in ('active_key', 'revoked_at', 'revoked_by', 'revocation_reason')):
                deny()

    def check_permission(self, permtype='read', permlevel=None):
        self._guard()
        return True

    def has_permission(self, permtype='read', *, debug=False, user=None):
        return self.flags.cowork_companion_credential is _WRITE

    def on_trash(self):
        deny()

    def before_rename(self, old, new, merge=False):
        deny()
