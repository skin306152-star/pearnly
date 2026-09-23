"""Shared mechanics of the native reconciliation task stores.

Naming, exact replay, listing, detail and deletion are the same for the bank and
income stores; a domain service supplies its DocType names, its access module, the
task-name prefix and the field lists it exposes to callers.
"""
import hashlib

import frappe
from frappe import _

from pearnly_cowork.reconciliation.recon_contracts import json_loads

ROW_LIMIT = 200
LIST_LIMIT = 100
MAX_BATCH_DELETE = 200


def _window(start, limit, cap):
	"""Coerce one pagination window; refuses anything the contract cannot serve."""
	try:
		return max(0, int(start)), max(1, min(int(limit), cap))
	except (TypeError, ValueError):
		frappe.throw(_("reconciliation.request_invalid"))


def _batch_names(task_ids):
	"""Validate one batch delete into the exact list of task names to remove.

	A name the caller repeated is removed once; an entry that is not a name, an
	empty batch and a batch over the cap are refused rather than silently dropped.
	"""
	if task_ids in (None, ""):
		return []
	if not isinstance(task_ids, (list, tuple)):
		frappe.throw(_("reconciliation.request_invalid"))
	if len(task_ids) > MAX_BATCH_DELETE:
		frappe.throw(_("reconciliation.request_invalid"))
	names = []
	for value in task_ids:
		if not isinstance(value, str) or not value.strip() or len(value) > 140:
			frappe.throw(_("reconciliation.request_invalid"))
		if value not in names:
			names.append(value)
	return names


class ReconTasks:
	def __init__(self, task, row, access, name_prefix, list_fields, detail_fields):
		self.task = task
		self.row = row
		self.access = access
		self.name_prefix = name_prefix
		self.list_fields = list(list_fields)
		self.detail_fields = list(detail_fields)

	def identity(self, request_id):
		"""Derive the task name and request key of one caller request id."""
		if (not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 120):
			frappe.throw(_("reconciliation.request_invalid"))
		key = hashlib.sha256(
			f"{frappe.local.site}|{frappe.session.user}|{request_id}".encode()).hexdigest()
		return f"{self.name_prefix}-" + key[:40], key

	def replay(self, name, request_digest):
		"""Return the stored result when the same request already ran, else ``None``.

		The same request id with different content is a conflict, never a silent
		overwrite of an existing reconciliation.
		"""
		existing = frappe.db.get_value(self.task, name, ["request_digest", "summary_json"],
			as_dict=True)
		if not existing:
			return None
		if existing.request_digest != request_digest:
			frappe.throw(_("reconciliation.request_conflict"))
		return {"name": name, "replayed": True, "summary": json_loads(existing.summary_json)}

	def records(self, workspace, start=0, limit=50):
		self.access.require(workspace)
		start, limit = _window(start, limit, LIST_LIMIT)
		rows = frappe.get_all(self.task, filters={"workspace": workspace}, fields=self.list_fields,
			order_by="creation desc, name desc", limit_start=start, limit_page_length=limit)
		return {"tasks": rows, "total": frappe.db.count(self.task, {"workspace": workspace})}

	def detail(self, task_id, start=0, limit=100):
		doc = frappe.get_doc(self.task, task_id)
		self.access.require(doc.workspace)
		start, limit = _window(start, limit, ROW_LIMIT)
		rows = frappe.get_all(self.row, filters={"task": doc.name}, fields=["*"],
			order_by="row_index asc", limit_start=start, limit_page_length=limit)
		return {"task": {field: doc.get(field) for field in self.detail_fields},
			"summary": json_loads(doc.summary_json),
			"rows": rows, "total": frappe.db.count(self.row, {"task": doc.name})}

	def delete(self, task_id):
		doc = frappe.get_doc(self.task, task_id)
		self.access.require(doc.workspace)
		return self._remove(doc)

	def delete_many(self, task_ids):
		"""Remove a batch of tasks of the caller's own scope, or none of them.

		Every task is checked before anything is deleted, so a batch that names a
		task outside the caller's scope removes nothing instead of leaving a partial
		delete behind.
		"""
		names = _batch_names(task_ids)
		if not names:
			return {"deleted": 0, "rows_deleted": 0, "ids": []}
		documents = []
		for name in names:
			if not frappe.db.exists(self.task, name):
				frappe.throw(_("reconciliation.task_not_found"), frappe.DoesNotExistError)
			doc = frappe.get_doc(self.task, name)
			self.access.require(doc.workspace)
			documents.append(doc)
		rows_deleted = 0
		for doc in documents:
			rows_deleted += self._remove(doc)["rows_deleted"]
		return {"deleted": len(documents), "rows_deleted": rows_deleted,
			"ids": [doc.name for doc in documents]}

	def _remove(self, doc):
		removed = frappe.db.count(self.row, {"task": doc.name})
		frappe.db.delete(self.row, {"task": doc.name})
		frappe.db.delete(self.task, {"name": doc.name})
		return {"ok": True, "rows_deleted": removed}
