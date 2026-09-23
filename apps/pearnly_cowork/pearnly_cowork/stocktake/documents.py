"""Native controllers share an unforgeable capability with the stocktake service."""
import frappe
from frappe import _
from frappe.model.document import Document
from pearnly_cowork.stocktake import access

_WRITE_TOKEN = object()


def persist(doc):
    """Internal only: no whitelisted caller may supply this object identity."""
    doc.flags.stocktake_write = _WRITE_TOKEN
    return doc.insert() if doc.is_new() else doc.save()


class StocktakeDocument(Document):
    def autoname(self):
        # IDs are generated/validated by the dedicated service before insert.
        self.name = self.record_id

    def _require_service(self):
        access.require(self.workspace)
        if self.flags.stocktake_write is not _WRITE_TOKEN:
            frappe.throw(_("stocktake.dedicated_api_required"))

    def before_insert(self):
        self._require_service()

    def before_validate(self):
        # Frappe can skip validate for trusted internal flags. A JSON flags
        # object is never sufficient authority for stocktake persistence.
        self._require_service()

    def validate(self):
        self._require_service()
        previous = self.get_doc_before_save()
        if previous and (self.record_id != previous.record_id or self.workspace != previous.workspace
                         or self.get("task") != previous.get("task")
                         or self.get("item") != previous.get("item")):
            frappe.throw(_("stocktake.request_invalid"))
        if self.name != self.record_id:
            frappe.throw(_("stocktake.request_invalid"))
        if self.get("task") and not frappe.db.exists("Cowork Stocktake Task", {
                "name": self.task, "workspace": self.workspace}):
            frappe.throw(_("stocktake.not_found"))
        if self.get("item") and not frappe.db.exists("Cowork Stocktake Item", {
                "name": self.item, "task": self.task, "workspace": self.workspace}):
            frappe.throw(_("stocktake.not_found"))
        if self.doctype == "Cowork Stocktake Operation" and previous:
            frappe.throw(_("stocktake.operation_conflict"))

    def on_trash(self):
        frappe.throw(_("stocktake.dedicated_api_required"))

    def before_rename(self, old, new, merge=False):
        frappe.throw(_("stocktake.dedicated_api_required"))
