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
from frappe.utils import now_datetime

WRITE_TOKEN = object()


def persist(doc, write_flag):
	"""Internal only: no whitelisted caller may supply this object identity."""
	doc.flags[write_flag] = WRITE_TOKEN
	return doc.insert() if doc.is_new() else doc.save()


def bulk_insert_rows(doctype, access, task, values):
	"""一份对账的结果行一次写完（2026-10-02 压测：几百行逐行走完整的文档插入，任务头和行同一个事务开 60–100 多秒）。

	``values``：按行序的字段字典（不含 record_id / workspace / task / row_index，这里按任务补上）。与行控制器
	（``ReconDocument``）同一道把关：只有对账服务调用（不经白名单入口）、工作空间权限 ``access.require`` 查一次（一份对账的行
	同属一个工作空间）；名称照旧取 ``record_id``；写之前按表结构核选项与长度，不合的照旧报错、一行也不写。仍在调用方（任务头）
	的事务里，由调用方提交。"""
	if not values:
		return
	access.require(task.workspace)
	meta = frappe.get_meta(doctype)
	rows = [_checked_row(meta, {"record_id": f"{task.name}-{index + 1}", "workspace": task.workspace, "task": task.name,
		"row_index": index + 1, **row}) for index, row in enumerate(values)]
	now, user = now_datetime(), frappe.session.user
	columns = list(rows[0])
	frappe.db.bulk_insert(doctype, ["name", "owner", "modified_by", "creation", "modified", "docstatus", "idx", *columns],
		[(row["record_id"], user, user, now, now, 0, 0, *(row[column] for column in columns)) for row in rows],
		chunk_size=500)


def _checked_row(meta, values):
	"""与逐行插入时 Frappe 的校验同一口径：选项必须在列表里、文字不超长；空日期存 NULL。"""
	for field, value in values.items():
		df = meta.get_field(field)
		if df is None:
			continue
		if df.fieldtype == "Select" and value not in (df.options or "").split("\n"):
			frappe.throw(_("reconciliation.request_invalid"))
		if df.fieldtype == "Data" and isinstance(value, str) and len(value) > (int(df.length or 0) or 140):
			frappe.throw(_("reconciliation.request_invalid"))
		if df.fieldtype == "Date" and value in ("", None):
			values[field] = None
	return values


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
