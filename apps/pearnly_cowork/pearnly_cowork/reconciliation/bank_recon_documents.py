"""Native controllers share an unforgeable capability with the reconciliation service."""
import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_cowork.reconciliation import bank_recon_access

_WRITE_TOKEN = object()


def persist(doc):
	"""Internal only: no whitelisted caller may supply this object identity."""
	doc.flags.bank_recon_write = _WRITE_TOKEN
	return doc.insert() if doc.is_new() else doc.save()


class BankReconciliationDocument(Document):
	def autoname(self):
		# Names are generated and validated by the dedicated service before insert.
		self.name = self.record_id

	def _require_service(self):
		bank_recon_access.require(self.workspace)
		if self.flags.bank_recon_write is not _WRITE_TOKEN:
			frappe.throw(_("reconciliation.dedicated_api_required"))

	def before_insert(self):
		self._require_service()

	def before_validate(self):
		self._require_service()

	def validate(self):
		self._require_service()
		previous = self.get_doc_before_save()
		if previous and (self.record_id != previous.record_id or self.workspace != previous.workspace
				or self.get("task") != previous.get("task")):
			frappe.throw(_("reconciliation.request_invalid"))
