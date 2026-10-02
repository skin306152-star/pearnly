"""Native income reconciliation: store the decoded rows and the pairing result.

One entry point persists one task with its result rows: ``create`` takes the two
sides already decoded by a caller, validates them with the row contract and stores
what the migrated core (``income_vat``) pairs. Reading the ledger and output-VAT
files, running OCR and building the report workbook stay in the legacy project and
are later slices of this family.

The deployed income reconciliation stored two generations of records
(``vat_recon_*`` and ``gl_vat_store``); this platform keeps one native task/row
pair instead, holding the decoded rows and the pairing verdict. Result rows follow
the VAT report, so ``row_count`` counts them and ``gl_row_count`` records how many
ledger rows went in; an absent ledger side is a zero flagged by ``has_gl_match``.
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import (
	naming,
	income_recon_access,
	income_recon_contracts,
	income_parse,
	income_vat,
	preset_store,
	presets,
	recon_contracts,
	recon_tasks,
	vat_parse,
)
from pearnly_cowork.reconciliation.income_recon_access import ROW, TASK
from pearnly_cowork.reconciliation import recon_documents
from pearnly_cowork.reconciliation.income_recon_documents import persist
from pearnly_cowork.reconciliation.export import income_workbook

EXPORT_LANGUAGES = ("th", "en", "zh", "ja")
ROW_EXPORT_FIELDS = ("doc_no", "vat_date", "customer_name", "vat_amount", "has_gl_match",
	"gl_amount", "diff", "account_codes")
LIST_FIELDS = ("name", "title", "status", "row_count", "gl_row_count", "matched_count",
	"diff_count", "unmatched_count", "gl_total", "vat_total", "created_at")
DETAIL_FIELDS = ("name", "title", "status", "gl_account_code", "amount_tolerance", "row_count",
	"gl_filename", "vat_filename", "gl_row_count", "matched_count", "diff_count",
	"unmatched_count", "gl_total", "gl_only_credit", "gl_only_debit", "vat_only_positive",
	"vat_only_negative", "vat_total", "source_format", "source_digest", "created_by",
	"created_at")
TASK_SUMMARY_FIELDS = ("gl_total", "gl_only_credit", "gl_only_debit", "vat_only_positive",
	"vat_only_negative", "vat_total")
TASKS = recon_tasks.ReconTasks(TASK, ROW, income_recon_access, "CIR", LIST_FIELDS, DETAIL_FIELDS)


def create(workspace, title, request_id, gl_rows, vat_rows, header=None, parse_info=None,
		gl_filename="", vat_filename=""):
	"""Validate, pair and store one reconciliation inside a single transaction.

	总账行全部存进 ``inputs_json``；本公司预设了「不参与对账」的收入科目（对账预设）只在核对时去掉，改了预设重算能拿回来。"""
	income_recon_access.require(workspace)
	name, request_key = TASKS.identity(request_id)
	title = income_recon_contracts.title_text(title)
	rows = income_recon_contracts.decode_rows(gl_rows, vat_rows)
	header_values = income_recon_contracts.header(header)
	parse_values = income_recon_contracts.parse_info(parse_info)
	request_digest = income_recon_contracts.digest(header_values, *rows)
	replayed = TASKS.replay(name, request_digest)
	if replayed:
		return replayed
	results, summary = income_vat.reconcile_gl_vat(
		income_recon_contracts.matching_gl_rows(without_excluded(rows[0],
			excluded_accounts(workspace, report_tax_id(parse_values)))),
		income_recon_contracts.matching_vat_rows(rows[1]),
		amount_tolerance=float(header_values["amount_tolerance"]))
	summary_value = {field: getattr(summary, field) for field in summary.__dataclass_fields__}
	matched, findings, unmatched = result_counts(results)
	task = frappe.get_doc({"doctype": TASK, "record_id": name, "workspace": workspace,
		"title": title, "status": "completed",
		"gl_account_code": header_values["gl_account_code"],
		"gl_filename": income_recon_contracts.file_name(gl_filename, "gl_filename"),
		"vat_filename": income_recon_contracts.file_name(vat_filename, "vat_filename"),
		"source_format": income_recon_contracts.SOURCE_FORMAT, "source_digest": request_digest,
		"request_key": request_key, "request_digest": request_digest,
		"row_count": len(results), "gl_row_count": len(rows[0]),
		"matched_count": matched, "diff_count": findings, "unmatched_count": unmatched,
		"amount_tolerance": float(header_values["amount_tolerance"]),
		"summary_json": income_recon_contracts.json_dumps(summary_value),
		"parse_info_json": income_recon_contracts.json_dumps(parse_values),
		"inputs_json": recon_contracts.inputs_json(gl=rows[0]),
		"created_by": frappe.session.user, "created_at": now_datetime(), **{
			field: summary_value[field] for field in TASK_SUMMARY_FIELDS}})
	persist(task)
	_persist_rows(task, results)
	return {"name": name, "replayed": False, "summary": summary_value, "row_count": len(results),
		"parse_info": parse_values}


def _persist_rows(task, results):
	"""结果行一次批量写入（``recon_documents.bulk_insert_rows``，与银行对账同一做法）。"""
	recon_documents.bulk_insert_rows(ROW, income_recon_access, task, [row_values(result) for result in results])


def _tax_field(tax_id):
	digits = "".join(char for char in str(tax_id or "") if char.isdigit())
	return {"tax_id": digits} if len(digits) == 13 else {}


def report_tax_id(parse_values):
	"""税报表头印的本公司税号（建任务时记在诊断里）。"""
	return next((item.get("tax_id") for item in (parse_values or {}).get("vat") or [] if item.get("tax_id")), "")


def excluded_accounts(workspace, tax_id):
	"""这家公司预设了「不参与对账」的收入科目。"""
	prefix = presets.income_prefix(tax_id)
	return {scope[len(prefix):] for scope, rules in preset_store.under(workspace, "income", prefix).items()
		if rules.get(presets.EXCLUDED) == presets.YES}


def without_excluded(gl_rows, excluded):
	return [row for row in gl_rows if str(row.get("account_code") or "").strip() not in excluded] if excluded else gl_rows


def gl_diagnostic(diagnostics, rows, filename):
	"""Keep the reader's own counters under the stored diagnostic keys."""
	record = {"file": filename, "method": diagnostics.get("method") or "", "rows": rows,
		"parser_version": income_parse.PARSER_VERSION,
		"accounts_seen": diagnostics.get("accounts_seen") or []}
	for key in ("candidate_data_lines", "tables_seen", "sheets_seen", "rows_kept"):
		if isinstance(diagnostics.get(key), int) and not isinstance(diagnostics.get(key), bool):
			record[key] = diagnostics[key]
	if "header_found" in diagnostics:
		record["header_found"] = bool(diagnostics["header_found"])
	return record


def within_report_period(gl_rows, vat_rows):
	"""总账期间比税报长（全年导出）：只留税报所在整月的总账行，其余不参加核对（2026-09-30 压测 I01：1–12 月的总账
	配 5 月的税报，其他月份 2,890 行全列成「只在总账」；银行对账同样按期间截）。没有日期的行照留；税报日期跨度超过
	一年（日期读错）不截。返回 ``(总账行, (截掉的行数, 起, 止))``，不需要截返回 ``(总账行, None)``。"""
	import calendar
	from datetime import date as date_type

	from pearnly_cowork.reconciliation.normalization import parse_date

	def day(value):
		try:
			return parse_date(value)
		except Exception:
			return None

	days = sorted(filter(None, (day(row.get("report_date")) for row in vat_rows)))
	if not days:
		return gl_rows, None
	middle = days[len(days) // 2]
	days = [value for value in days if abs((value - middle).days) <= 366]
	first, last = days[0], days[-1]
	if (last - first).days > 400:
		return gl_rows, None
	start = first.replace(day=1)
	end = date_type(last.year, last.month, calendar.monthrange(last.year, last.month)[1])
	kept = [row for row in gl_rows if not day(row.date) or start <= day(row.date) <= end]
	if len(kept) == len(gl_rows) or not kept:
		return gl_rows, None
	return kept, (len(gl_rows) - len(kept), start.isoformat(), end.isoformat())


def create_from_parsed(workspace, title, request_id, gl_source, vat_rows=None, header=None,
		revenue_prefix="4", vat_source=None, gl_filename="", vat_filename="", report_warnings=None, tax_id="",
		photo_pages=None):
	"""Store one reconciliation from the extracted content of the two report files.

	Opening the ledger workbook or PDF, opening the output-VAT report, running OCR
	and calling a model all stay in the legacy project; this entry converts what
	that path extracted with the migrated conversions (``income_parse`` and
	``vat_parse``) and then stores the rows through the same validated path as
	``create``. Either side may also arrive already decoded: ``vat_rows`` are stored
	as given, and a report that kept no row is an error rather than an empty
	reconciliation.
	"""
	income_recon_access.require(workspace)
	vat_parsed = None
	# 多份税报由调用方合并好传 vat_rows，读不出的文件、重复行的提示随 report_warnings 带进来（2026-09-30）。
	report_method, report_rows, report_version = "", 0, ""
	report_warnings = list(report_warnings or [])
	if vat_source is not None:
		report = vat_parse.decode(vat_source)
		if not report or not report.get("ok"):
			frappe.throw(_("reconciliation.vat_report_unparsed"))
		vat_rows = vat_parse.decoded_rows(report["rows"])
		if not vat_rows:
			frappe.throw(_("reconciliation.vat_no_rows"))
		vat_parsed = {"rows": report["row_count"], "method": report.get("method"),
			"parser_version": report.get("parser_version")}
		report_method = report.get("method") or ""
		report_rows = report["row_count"]
		report_version = report.get("parser_version") or ""
		report_warnings += report.get("warnings") or []
		tax_id = tax_id or report.get("issuer_hint") or ""
	elif vat_rows is None:
		frappe.throw(_("reconciliation.request_invalid"))
	parsed = income_parse.decode(gl_source, revenue_prefix)
	if not parsed["ok"]:
		frappe.throw(_("reconciliation.gl_parse_failed"))
	if not parsed["rows"]:
		frappe.throw(_("reconciliation.gl_trial_balance") if income_parse.is_trial_balance(gl_source)
			else _("reconciliation.gl_no_revenue_rows"))
	gl_rows, trimmed = within_report_period(parsed["rows"], vat_rows)
	if trimmed:
		report_warnings = list(report_warnings) + [_("reconciliation.gl_rows_outside_report").format(*trimmed)]
	# The stored diagnostics keep the file each side came from and the reader that
	# served it, so a later export or review can say what a task was read from.
	parse_values = {"gl": [gl_diagnostic(parsed["diag"], parsed["row_count"], gl_filename)],
		"vat": [{"file": vat_filename, "method": report_method, "rows": report_rows,
			"parser_version": report_version, "warnings": report_warnings,
			**_tax_field(tax_id), **({"photo_pages": [[label, list(refs)[:income_recon_contracts.MAX_PAGE_REFS]]
				for label, refs in photo_pages[:income_recon_contracts.MAX_PHOTO_PAGES]]} if photo_pages else {})}]}
	result = create(workspace, title, request_id, income_parse.decoded_rows(gl_rows),
		vat_rows, header, parse_values, gl_filename, vat_filename)
	payload = {**result, "parsed": {
		"rows": parsed["row_count"],
		"method": parsed["diag"].get("method"),
		"accounts_seen": parsed["diag"].get("accounts_seen") or [],
		"parser_version": income_parse.PARSER_VERSION,
	}}
	if vat_parsed is not None:
		payload["vat_parsed"] = vat_parsed
	return payload


def result_counts(results):
	"""The three counters the deployed run showed beside the detail rows.

	The deployed ``/recon/gl-vat/run`` derived them outside its migrated core: a
	row without a ledger counterpart is unmatched, and of the rows that have one a
	zero difference is a match while any other difference is a finding.
	"""
	matched = sum(1 for row in results if row.gl_amount is not None and (row.diff or 0) == 0)
	findings = sum(1 for row in results if row.gl_amount is not None and (row.diff or 0) != 0)
	unmatched = sum(1 for row in results if row.gl_amount is None)
	return matched, findings, unmatched


def row_values(result):
	"""Store one pairing result; an absent ledger side is a zero the Check explains."""
	return {
		"doc_no": result.doc_no or "",
		"vat_date": result.date or "",
		"customer_name": result.customer_name or "",
		"vat_amount": result.vat_amount or 0.0,
		"has_gl_match": 0 if result.gl_amount is None else 1,
		"gl_amount": 0.0 if result.gl_amount is None else result.gl_amount,
		"diff": 0.0 if result.diff is None else result.diff,
		"account_codes": result.account_codes or "",
	}


def _stored_summary(doc):
	"""Rebuild the summary dataclass from the stored JSON, like the deployed reader."""
	value = income_recon_contracts.json_loads(doc.summary_json)
	if not isinstance(value, dict):
		return income_vat.GlVatSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
	try:
		return income_vat.GlVatSummary(**{
			key: item for key, item in value.items()
			if key in income_vat.GlVatSummary.__dataclass_fields__})
	except (TypeError, ValueError):
		return income_vat.GlVatSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _export_row(row):
	"""Invert one stored result row into the deployed detail-row shape.

	The store keeps a zero where the deployed run kept the absent side, so the
	Check decides whether a difference is stored at all.
	"""
	matched = bool(row.has_gl_match)
	return income_vat.ReconRow(
		doc_no=row.doc_no or "", date=row.vat_date or "", customer_name=row.customer_name or "",
		vat_amount=row.vat_amount or 0.0,
		gl_amount=row.gl_amount if matched else None,
		diff=row.diff if matched else None,
		account_codes=row.account_codes or "")


def export_size(task_id, lang="th"):
	"""Rows the export of this task holds, behind the same access check as ``export``
	(``pearnly_core.exports`` decides from it whether to answer now or in the background)."""
	doc = frappe.get_doc(TASK, task_id)
	income_recon_access.require(doc.workspace)
	return frappe.db.count(ROW, {"task": doc.name})


def export(task_id, lang="th"):
	"""Render the review workbook of one stored task; no ERP, model or file access.

	The workbook sees exactly what the task stored: its result rows and its summary,
	including the itemized adjustment rows. The two stored file names and the reader
	diagnostics are not shown by the deployed layout, so they stay out of the sheet
	until a later slice reads them.
	"""
	doc = frappe.get_doc(TASK, task_id)
	income_recon_access.require(doc.workspace)
	if lang not in EXPORT_LANGUAGES:
		lang = "th"
	rows = frappe.get_all(ROW, filters={"task": doc.name}, fields=list(ROW_EXPORT_FIELDS),
		order_by="row_index asc", limit_page_length=0)
	content = income_workbook.export_gl_vat_excel([_export_row(row) for row in rows],
		_stored_summary(doc), lang=lang)
	return {"filename": naming.export_filename(doc.title,
		f"GL_VAT_recon_{doc.name}_{file_stem(doc.vat_filename)}"), "content": content}


def file_stem(name):
	"""ASCII-safe base of a stored report file name; the deployed name builder."""
	base = str(name or "vat").rsplit(".", 1)[0]
	safe = "".join(character if character.isascii() and (character.isalnum()
		or character in "._-") else "_" for character in base)[:40].strip("_")
	return safe or "vat"


def records(workspace, start=0, limit=50):
	return TASKS.records(workspace, start, limit)


def detail(task_id, start=0, limit=100):
	return TASKS.detail(task_id, start, limit)


def delete(task_id):
	return TASKS.delete(task_id)


def delete_many(task_ids):
	return TASKS.delete_many(task_ids)
