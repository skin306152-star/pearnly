"""Unforgeable service writes; generic Frappe resources expose no ledger data."""
import frappe
from frappe import _
from frappe.model.document import Document

TYPES = ('Cowork Push Record', 'Cowork Push Attempt', 'Cowork Push Writer Lock')
_CAPABILITY = object()
_IDENTITY = {
    TYPES[0]: ('record_id', 'workspace', 'client', 'actor', 'history', 'connection', 'account_key',
               'request_digest', 'request_fingerprint', 'document_key', 'business_key', 'invoice_no', 'seller_name', 'history_version',
               'connection_revision', 'branch', 'binding_generation', 'target_json', 'payload_json', 'checks_json'),
    TYPES[1]: ('record_id', 'push_record', 'workspace', 'client', 'connection', 'fence', 'owner_id',
               'token_digest', 'binding_generation', 'started_at', 'lease_expires_at'),
    TYPES[2]: ('record_id', 'lock_key', 'lock_kind', 'workspace', 'client', 'connection', 'account_key'),
}


def denied():
    frappe.throw(_('Use the Cowork push service to access push records.'), frappe.PermissionError)


def persist(doc):
    doc.flags.cowork_push_native = _CAPABILITY
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    denied()  # Never False: Frappe DocShare must not restore access.


def native_query(user=None):
    denied()  # Also blocks pre-existing shares OR-ed by the native list query.


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES:
        denied()


class PushDocument(Document):
    def _guard(self):
        if self.flags.cowork_push_native is not _CAPABILITY:
            denied()

    def autoname(self):
        self._guard()
        self.name = self.record_id

    def before_insert(self):
        self._guard()
        if frappe.db.sql('''SELECT name FROM `tabDocShare` WHERE share_doctype=%s
            AND share_name=%s AND (COALESCE(`read`,0)<>0 OR COALESCE(`write`,0)<>0
            OR COALESCE(`share`,0)<>0 OR COALESCE(`submit`,0)<>0) LIMIT 1''',
            (self.doctype, self.record_id)):
            denied()

    def before_validate(self):
        self._guard()

    def before_save(self):
        self._guard()

    def validate(self):
        self._guard()
        previous = self.get_doc_before_save()
        if self.name != self.record_id:
            denied()
        if previous and any(self.get(key) != previous.get(key) for key in _IDENTITY[self.doctype]):
            denied()

    def check_permission(self, permtype='read', permlevel=None):
        self._guard()
        return True

    def has_permission(self, permtype='read', *, debug=False, user=None):
        return self.flags.cowork_push_native is _CAPABILITY

    def on_trash(self):
        denied()

    def before_rename(self, old, new, merge=False):
        denied()
