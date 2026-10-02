"""销项税报告核查任务：两边输入（发票记录、报告行）→ 核查工作簿 → 存档（用户 2026-09-26 定迁）。

旧线上 `recon_jobs/handlers.py::run_salesvat` 的后半段：合并后的报告行 + 读出的发票 → `build_excel`
（逐字移植在 `export/vat_check_workbook.py`）→ 存任务。任务上存两边的输入与汇总，核查工作簿随时由输入重建，
所以结果页（复核表）可以改输入、重算、保存，导出的永远是当前版本。

文件读取（报告解析、发票识别）与计费在调用方（`recon_center`），这里不碰文件、不调模型。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import naming, recon_tasks, vat_check_access
from pearnly_cowork.reconciliation.export.vat_check_workbook import build_excel
from pearnly_cowork.reconciliation.vat_check_documents import persist

TASK = vat_check_access.TASK
LANGUAGES = ("th", "en", "zh", "ja")
LIST_FIELDS = ("name", "title", "status", "client_name", "period", "invoice_count", "report_count",
	"matched_count", "diff_count", "unmatched_count", "diff_amount", "created_at")
MAX_ROWS = 20000


def _lang(value):
	return value if value in LANGUAGES else "th"


def _inputs(value):
	if not isinstance(value, dict):
		frappe.throw(_("reconciliation.request_invalid"))
	invoices, rows = value.get("invoices"), value.get("report_rows")
	if not isinstance(invoices, list) or not isinstance(rows, list) or len(invoices) + len(rows) > MAX_ROWS:
		frappe.throw(_("reconciliation.rows_invalid"))
	if any(not isinstance(row, dict) for row in invoices + rows):
		frappe.throw(_("reconciliation.rows_invalid"))
	return {"invoices": invoices, "report_rows": rows, "seller_name": str(value.get("seller_name") or ""),
		"seller_tax_id": str(value.get("seller_tax_id") or ""),
		"period_year": value.get("period_year"), "period_month": value.get("period_month"),
		"failed_files": list(value.get("failed_files") or []),
		# 标题下的提示（识别出的税报哪几页没法自核；2026-09-30 加，旧任务没有）
		"report_notes": [str(note) for note in value.get("report_notes") or []][:10]}


def compute(inputs, lang):
	"""同一个 build_excel：返回 (工作簿字节, 汇总)。汇总里的 rows 是逐张对照的结果。"""
	return build_excel(inputs["invoices"], inputs["report_rows"], inputs["seller_name"], inputs["seller_tax_id"],
		inputs["period_year"], inputs["period_month"], _lang(lang))


def _counts(summary):
	rows = summary.get("rows") or []
	unmatched = sum(1 for row in rows if str(row.get("kind") or row.get("status") or "").endswith("orphan"))
	# 旧线上的 n_diff 把两边缺失的也算作“有差异”；存档与列表拆开：差异不含缺失（与结果页四个数一致）。
	return {"matched_count": int(summary.get("n_ok") or 0),
		"diff_count": max(0, int(summary.get("n_diff") or 0) - unmatched),
		"unmatched_count": unmatched, "diff_amount": float(summary.get("diff_amount_total") or 0)}


def _period(inputs):
	year, month = inputs.get("period_year"), inputs.get("period_month")
	return f"{year}-{int(month):02d}" if year and month else ""


def create(workspace, title, request_id, inputs, lang="th"):
	vat_check_access.require(workspace)
	inputs = _inputs(inputs)
	key = hashlib.sha256(f"{frappe.local.site}|{frappe.session.user}|{request_id}".encode()).hexdigest()
	name = "CVC-" + key[:40]
	if frappe.db.exists(TASK, name):
		return {"name": name, "replayed": True}
	_content, summary = compute(inputs, lang)
	task = frappe.get_doc({"doctype": TASK, "record_id": name, "workspace": workspace,
		"title": (str(title or "").strip() or inputs["seller_name"] or _period(inputs) or name)[:140],
		"status": "completed", "client_name": inputs["seller_name"][:140], "period": _period(inputs),
		"invoice_count": len(inputs["invoices"]), "report_count": len(inputs["report_rows"]),
		"lang": _lang(lang), "request_key": key, "inputs_json": json.dumps(inputs, ensure_ascii=False, default=str),
		"summary_json": json.dumps(summary, ensure_ascii=False, default=str),
		"created_by": frappe.session.user, "created_at": now_datetime(), **_counts(summary)})
	persist(task)
	return {"name": name, "replayed": False}


def task(task_id, *, for_update=False):
	if not task_id or not frappe.db.exists(TASK, task_id):
		frappe.throw(_("reconciliation.task_not_found"), frappe.DoesNotExistError)
	doc = frappe.get_doc(TASK, task_id, for_update=for_update)
	vat_check_access.require(doc.workspace)
	return doc


def stored_inputs(doc):
	return _inputs(json.loads(doc.inputs_json or "{}"))


def save(doc, inputs, lang):
	inputs = _inputs(inputs)
	_content, summary = compute(inputs, lang)
	history = json.loads(doc.get("edit_json") or "[]")
	history.append({"revision": (history[-1]["revision"] if history else 1) + 1,
		"by": frappe.session.user, "at": str(now_datetime())})
	doc.update({"inputs_json": json.dumps(inputs, ensure_ascii=False, default=str),
		"summary_json": json.dumps(summary, ensure_ascii=False, default=str),
		"invoice_count": len(inputs["invoices"]), "report_count": len(inputs["report_rows"]),
		"edit_json": json.dumps(history[-50:], ensure_ascii=False), **_counts(summary)})
	persist(doc)
	return summary


def revision(doc):
	history = json.loads(doc.get("edit_json") or "[]")
	return history[-1]["revision"] if history else 1


def records(workspace, start=0, limit=20, sort_by=None, sort_order=None):
	vat_check_access.require(workspace)
	start, limit = max(0, int(start or 0)), max(1, min(int(limit or 20), 100))
	column = sort_by if sort_by in LIST_FIELDS and sort_by != "name" else "creation"
	order = "asc" if str(sort_order or "").lower() == "asc" else "desc"
	rows = frappe.get_all(TASK, filters={"workspace": workspace}, fields=list(LIST_FIELDS),
		order_by=f"{column} {order}, name desc", limit_start=start, limit_page_length=limit)
	return {"tasks": rows, "total": frappe.db.count(TASK, {"workspace": workspace})}


def delete_many(task_ids):
	names = frappe.parse_json(task_ids) if isinstance(task_ids, str) else task_ids
	if not isinstance(names, list) or len(names) > 200:
		frappe.throw(_("reconciliation.request_invalid"))
	docs = [task(name) for name in names]
	for doc in docs:
		frappe.db.delete(TASK, {"name": doc.name})
		recon_tasks.drop_views(doc.name)  # 结果页存档一起删
	return {"deleted": len(docs), "ids": [doc.name for doc in docs]}


def export_size(task_id, lang=None):
	"""发票与税报两边的行数（同一道权限检查）：``pearnly_core.exports`` 据此决定当场给还是排后台。"""
	inputs = stored_inputs(task(task_id))
	return len(inputs["invoices"] or []) + len(inputs["report_rows"] or [])


def export(task_id, lang=None):
	doc = task(task_id)
	content, _summary = compute(stored_inputs(doc), lang or doc.lang)
	return {"filename": naming.export_filename(doc.get("title"), f"VAT_check_{doc.name}"), "content": content}
