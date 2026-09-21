"""Shared controller gate for the native reconciliation documents.

Every native reconciliation task and row is written only by its dedicated service:
the service stamps an unforgeable object identity on ``doc.flags`` and the
controller refuses anything else, so no whitelisted caller can write these records
directly (a JSON flag cannot carry object identity). The record identity and its
workspace are immutable after insert.

Subclasses bind ``write_flag`` and the ``access`` module of their domain.
"""
import frappe
from frappe import _
from frappe.model.document import Document

WRITE_TOKEN = object()


def persist(doc, write_flag):
	"""Internal only: no whitelisted caller may supply this object identity."""
	doc.flags[write_flag] = WRITE_TOKEN
	return doc.insert() if doc.is_new() else doc.save()


class ReconDocument(Document):
	write_flag = ""
	access = None

	def autoname(self):
		# Names are generated and validated by the dedicated service before insert.
		self.name = self.record_id

	def _require_service(self):
		self.access.require(self.workspace)
		if self.flags.get(self.write_flag) is not WRITE_TOKEN:
			frappe.throw(_("reconciliation.dedicated_api_required"))

	def _record_identity_is_stable(self):
		previous = self.get_doc_before_save()
		if previous and (self.record_id != previous.record_id or self.workspace != previous.workspace
				or self.get("task") != previous.get("task")):
			frappe.throw(_("reconciliation.request_invalid"))

	def before_insert(self):
		self._require_service()

	def before_validate(self):
		self._require_service()

	def validate(self):
		self._require_service()
		self._record_identity_is_stable()
