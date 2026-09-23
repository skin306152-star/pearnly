"""Insert-only private delivery mappings; generic framework access is denied."""
from uuid import UUID

import frappe
from frappe import _
from frappe.model.document import Document

DOCTYPE = 'Cowork Companion Delivery'
_INSERT = object()


def deny():
    frappe.throw(_('Use the Cowork Companion service to access deliveries.'), frappe.PermissionError)


def native_permission(doc=None, ptype=None, user=None, **kwargs):
    deny()


def native_query(user=None):
    deny()


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def insert(doc):
    """Caller owns rollback of the lease, mapping and native __Auth insertion."""
    doc.flags.cowork_companion_delivery = _INSERT
    doc.flags.ignore_version = True
    doc.flags.notify_update = False
    try:
        return doc.insert(ignore_permissions=True)
    finally:
        doc.flags.cowork_companion_delivery = None


class DeliveryDocument(Document):
    def _guard(self):
        if self.flags.cowork_companion_delivery is not _INSERT or not self.is_new():
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
        try:
            valid_id = str(UUID(self.record_id)) == self.record_id
        except (ValueError, TypeError, AttributeError):
            valid_id = False
        if (not valid_id or self.name != self.record_id or self.site_name != frappe.local.site
                or frappe.conf.get('pearnly_product') != 'cowork' or self.get_doc_before_save()):
            deny()

    def check_permission(self, permtype='read', permlevel=None):
        if permtype != 'create':
            deny()
        self._guard()
        return True

    def has_permission(self, permtype='read', *, debug=False, user=None):
        if permtype != 'create':
            deny()
        self._guard()
        return True

    def on_trash(self):
        deny()

    def before_rename(self, old, new, merge=False):
        deny()

    def __repr__(self):
        return f'<CoworkCompanionDelivery: {self.name or "unsaved"}>'

    def __str__(self):
        return self.__repr__()
