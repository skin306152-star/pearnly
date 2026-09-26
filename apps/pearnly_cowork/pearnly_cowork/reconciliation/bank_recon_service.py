"""Native bank reconciliation, PDF row review and stored-result export."""
import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import (
	bank_intake,
	bank_matching,
	bank_parse,
	bank_recon_access,
	bank_recon_contracts,
	bank_review,
	bank_warnings,
	recon_tasks,
)
from pearnly_cowork.reconciliation.bank_recon_access import ROW, TASK
from pearnly_cowork.reconciliation.bank_recon_documents import persist
from pearnly_cowork.reconciliation.export import workbook as export_workbook

EXPORT_LANGUAGES = ("th", "en", "zh", "ja")
ROW_EXPORT_FIELDS = ("match_status", "match_layer", "date_diff_days", "stmt_date", "stmt_desc",
	"stmt_withdrawal", "stmt_deposit", "stmt_balance", "stmt_confidence", "stmt_balance_ok",
	"stmt_autocorrected", "gl_date", "gl_doc_no", "gl_account_code", "gl_desc", "gl_debit",
	"gl_credit", "gl_balance", "source_stmt_file", "source_gl_file")


LIST_FIELDS = ("name", "title", "reconciliation_type", "bank_code", "status", "row_count",
	"matched_count", "formula_diff", "created_at")
DETAIL_FIELDS = ("name", "title", "reconciliation_type", "bank_code", "gl_account_code", "status",
	"row_count", "source_format", "source_digest", "created_by", "created_at")
TASKS = recon_tasks.ReconTasks(TASK, ROW, bank_recon_access, "CBR", LIST_FIELDS, DETAIL_FIELDS)


def create(workspace, title, request_id, statement_rows, gl_rows, header=None,
		reconciliation_type="bank", source_format=bank_recon_contracts.SOURCE_FORMAT,
		parse_info=None, warnings=None, anchors=None, lang="th", *, _review=None):
	"""Validate, match and store one reconciliation inside a single transaction."""
	bank_recon_access.require(workspace)
	name, request_key = TASKS.identity(request_id)
	title = bank_recon_contracts.title_text(title)
	if reconciliation_type not in ("bank", "income"):
		frappe.throw(_("reconciliation.type_invalid"))
	rows = bank_recon_contracts.decode_rows(statement_rows, gl_rows)
	header_values = bank_recon_contracts.header(header)
	header_values, anchor_record = anchor_plan(header_values, anchors)
	parse_values = bank_recon_contracts.parse_info(parse_info)
	request_digest = bank_recon_contracts.digest(header_values, *rows)
	replayed = TASKS.replay(name, request_digest)
	if replayed:
		existing = frappe.get_doc(TASK, name)
		bank_recon_access.require(existing.workspace)
		if existing.workspace != workspace:
			frappe.throw(_("reconciliation.request_conflict"))
		return {**replayed, "status": existing.status}
	results, summary_value = ([], {}) if _review is not None else _match(rows, header_values)
	run_values = run_info(rows, summary_value.get("matched_count", 0), warnings, anchor_record, lang)
	review_value = {} if _review is None else bank_review.serializable({
		"revision": 1, "statement_rows": rows[0], "gl_rows": rows[1], "header": header_values,
		"diagnostics": _review, "lang": lang, "anchor_record": anchor_record,
		"warnings": warnings})
	task = frappe.get_doc({"doctype": TASK, "record_id": name, "workspace": workspace,
		"title": title, "reconciliation_type": reconciliation_type,
		"status": "needs_review" if _review is not None else "completed",
		"bank_code": header_values["bank_code"], "gl_account_code": header_values["gl_account_code"],
		"source_format": source_format, "source_digest": request_digest,
		"request_key": request_key, "request_digest": request_digest,
		"review_json": bank_recon_contracts.json_dumps(review_value),
		"row_count": len(results),
		"summary_json": bank_recon_contracts.json_dumps(summary_value),
		"parse_info_json": bank_recon_contracts.json_dumps(parse_values),
		"run_info_json": bank_recon_contracts.json_dumps(run_values),
		"created_by": frappe.session.user, "created_at": now_datetime(), **{
			field: summary_value.get(field, 0) for field in TASK_SUMMARY_FIELDS}})
	persist(task)
	_persist_rows(task, results)
	return {"name": name, "replayed": False, "status": task.status, "summary": summary_value,
		"row_count": len(results), "run_info": run_values}


def _match(rows, header_values):
	results, summary = bank_matching.reconcile(
		bank_recon_contracts.matching_statement_rows(rows[0]),
		bank_recon_contracts.matching_gl_rows(rows[1]),
		stmt_opening=float(header_values["stmt_opening"]), gl_opening=float(header_values["gl_opening"]),
		stmt_closing=float(header_values["stmt_closing"]), gl_closing=float(header_values["gl_closing"]),
		bank_code=header_values["bank_code"], gl_account_code=header_values["gl_account_code"])
	return results, {field: getattr(summary, field) for field in summary.__dataclass_fields__}


def _persist_rows(task, results):
	for index, result in enumerate(results):
		persist(frappe.get_doc({"doctype": ROW, "record_id": f"{task.name}-{index + 1}",
			"workspace": task.workspace, "task": task.name, "row_index": index + 1,
			**row_values(result)}))


def create_from_parsed(workspace, title, request_id, statement_pages, gl_pages, header=None,
		filename="", gl_account_code="", reconciliation_type="bank", gl_filename="",
		warnings=None, anchors=None, lang="th"):
	"""Store one reconciliation from the decoded pages of the legacy parser.

	The file readers, the OCR call and model extraction stay in the legacy
	project; this entry converts their decoded pages with the migrated
	conversions (``bank_parse``) and then stores the rows through the same
	validated path as ``create``. A caller header overrides the opening,
	closing and bank code read from the statement document. The stored archive
	keeps the deployed 2-decimal amounts, and a parsed row that the decoded-row
	contract refuses is an error, not a silent drop.
	"""
	if not isinstance(statement_pages, (dict, type(None))) or not isinstance(
			gl_pages, (dict, type(None))):
		frappe.throw(_("reconciliation.request_invalid"))
	statement = bank_parse.statement_from_pipeline(statement_pages, filename)
	if not statement.get("ok"):
		frappe.throw(_("reconciliation.statement_unparsed"))
	gl = bank_parse.gl_from_pipeline(gl_pages, gl_account_code)
	overrides = {} if header in (None, "") else header
	if not isinstance(overrides, dict):
		frappe.throw(_("reconciliation.header_invalid"))
	values = {"stmt_opening": statement["opening"], "stmt_closing": statement["closing"],
		"bank_code": statement["bank_code"]}
	values.update(overrides)
	stage("matching")
	result = create(workspace, title, request_id,
		bank_parse.decoded_statement_rows(statement["rows"]),
		bank_parse.decoded_gl_rows(gl["rows"]), values, reconciliation_type,
		source_format=bank_parse.SOURCE_FORMAT,
		# The deployed run saved this per-file section so the workbook's File Info
		# shows real numbers; one admission is one successful file per side.
		parse_info={
			"stmt_files": [{"file": filename, "rows": statement["row_count"], "ok": True,
				"error": None, "error_code": None, "bank_code": statement["bank_code"]}],
			"gl_files": [{"file": gl_filename, "rows": gl["row_count"], "ok": True,
				"error": None, "error_code": None, "accounts": gl["accounts"]}],
		}, warnings=warnings, anchors=anchors, lang=lang)
	return {**result, "parsed": {
		"statement_rows": statement["row_count"],
		"gl_rows": gl["row_count"],
		"opening": statement["opening"],
		"closing": statement["closing"],
		"bank_code": statement["bank_code"],
		"balance_warn_count": statement["balance_warn_count"],
		"amount_fixed_count": statement["amount_fixed_count"],
		"parser_version": statement["parser_version"],
		"needs_review": bool(statement["needs_review"] or gl["needs_review"]),
	}}


def create_from_documents(workspace, title, request_id, statement_bytes, statement_filename,
		gl_bytes, gl_filename, header=None, gl_account_code="", reconciliation_type="bank",
		model_rows=None, read_page=None, page_count=None, warnings=None, anchors=None, lang="th",
		on_stage=None):
	"""Store one reconciliation from **files**, through the native readers.

	The bytes are handed in by the caller (the file store stays with the caller's
	domain); ``bank_intake`` reads them with the native deterministic readers first
	and falls back to the injected ``model_rows`` only when they cannot decode the
	file. Everything after the decode is the same validated path as ``create``. A
	file the readers and the model both refuse is an error, not an empty run;
	per-file diagnostics are stored so the workbook's File Info section shows the
	same numbers the deployed run recorded.
	"""
	bank_recon_access.require(workspace)
	if not isinstance(statement_bytes, bytes) or not isinstance(gl_bytes, bytes):
		frappe.throw(_("reconciliation.request_invalid"))
	# 对账中心的后台任务据此显示走到哪一步（读账单 / 读总账 / 核对）；不传就是原来的行为。
	stage = on_stage or (lambda _name: None)
	stage("statement")
	statement = bank_intake.read_statement_file(statement_bytes, statement_filename,
		model_rows=model_rows, read_page=read_page, page_count=page_count)
	if not statement.get("ok"):
		frappe.throw(_("reconciliation.statement_unparsed"))
	stage("ledger")
	ledger = bank_intake.read_ledger_file(gl_bytes, gl_filename,
		account_code=gl_account_code, model_rows=model_rows)
	if not ledger.get("ok"):
		frappe.throw(_("reconciliation.ledger_unparsed"))
	overrides = {} if header in (None, "") else header
	if not isinstance(overrides, dict):
		frappe.throw(_("reconciliation.header_invalid"))
	values = {"stmt_opening": statement["opening"], "stmt_closing": statement["closing"],
		"bank_code": statement["bank_code"], "gl_opening": ledger["opening"],
		"gl_closing": ledger["closing"], "gl_account_code": gl_account_code}
	values.update(overrides)
	stage("matching")
	result = create(workspace, title, request_id,
		bank_parse.decoded_statement_rows(statement["rows"]),
		bank_parse.decoded_gl_rows(ledger["rows"]), values, reconciliation_type,
		source_format=bank_parse.SOURCE_FORMAT,
		parse_info={
			"stmt_files": [{"file": statement_filename, "rows": statement["row_count"],
				"ok": True, "error": None, "error_code": None,
				"bank_code": statement["bank_code"]}],
			"gl_files": [{"file": gl_filename, "rows": ledger["row_count"], "ok": True,
				"error": None, "error_code": None, "accounts": ledger["accounts"]}],
		}, warnings=warnings, anchors=anchors, lang=lang,
		_review=_review_diagnostics(statement, statement_filename))
	return {**result, "parsed": {
		"statement_rows": statement["row_count"],
		"gl_rows": ledger["row_count"],
		"opening": statement["opening"],
		"closing": statement["closing"],
		"bank_code": statement["bank_code"],
		"statement_source": statement["source"],
		"gl_source": ledger["source"],
		"balance_warn_count": statement["balance_warn_count"],
		"amount_fixed_count": statement["amount_fixed_count"],
		"completeness_ok": statement["completeness"]["ok"],
		"escalations": statement.get("escalations") or [],
		"parser_version": statement["parser_version"],
		# Parse-quality warning is the existing diagnostic, not task state. Only
		# top-level status says whether confirmation is blocking this task.
		"needs_review": bool(statement["needs_review"] or ledger["needs_review"]),
	}}


def _review_diagnostics(statement, filename):
	if not filename.lower().endswith(".pdf") or not bank_review.needs_review(statement):
		return None
	return {"file": filename, "low_conf_count": statement.get("low_conf_count", 0),
		"amount_fixed_count": statement.get("amount_fixed_count", 0),
		"completeness": statement.get("completeness") or {},
		"row_metadata": [{"confidence": row.confidence, "balance_ok": row.balance_ok,
			"direction_autocorrected": bool(row.direction_autocorrected),
			"amount_autocorrected": bool(row.amount_autocorrected),
			"account_no": row.account_no} for row in statement["rows"]]}


def review(task_id):
	doc = frappe.get_doc(TASK, task_id)
	bank_recon_access.require(doc.workspace)
	value = _review_state(doc)
	return {"name": doc.name, "status": doc.status, "revision": value["revision"],
		"rows": bank_review.editable_rows(value["statement_rows"]),
		"opening": value["header"]["stmt_opening"], "diagnostics": value["diagnostics"],
		"confirmation": value.get("confirmation")}


def _review_state(doc):
	value = bank_recon_contracts.json_loads(doc.review_json)
	if not value:
		frappe.throw(_("reconciliation.review_unavailable"))
	return value


def _check_review(value, rows):
	try:
		validated, canonical = bank_review.validate_rows(rows, value["statement_rows"], value["gl_rows"])
	except bank_recon_contracts.ReconciliationContractError as error:
		frappe.throw(_(str(error)))
	header = bank_recon_contracts.header(value["header"])
	checks = bank_review.balance_chain(validated, header["stmt_opening"])
	return validated, canonical, header, checks


def check_review(task_id, rows):
	doc = frappe.get_doc(TASK, task_id)
	bank_recon_access.require(doc.workspace)
	value = _review_state(doc)
	_, _, _, checks = _check_review(value, rows)
	return {"revision": value["revision"], "checks": checks, "ok": all(row["ok"] for row in checks)}


def confirm_review(task_id, rows, expected_revision):
	# Lock the same task that deletion locks. No parser, file store, queue or OCR
	# is consulted: both sides of the original parse are frozen in review_json.
	bank_recon_access.require(frappe.get_doc(TASK, task_id).workspace)
	doc = frappe.get_doc(TASK, task_id, for_update=True)
	bank_recon_access.require(doc.workspace)
	value = _review_state(doc)
	if type(expected_revision) is not int or expected_revision != 1:
		frappe.throw(_("reconciliation.request_conflict"))
	validated, canonical, header, checks = _check_review(value, rows)
	fingerprint = bank_recon_contracts.canonical_digest(canonical)
	if doc.status == "completed":
		if (value.get("confirmation") or {}).get("digest") != fingerprint:
			frappe.throw(_("reconciliation.request_conflict"))
		return {"name": doc.name, "status": doc.status, "revision": value["revision"],
			"replayed": True, "summary": bank_recon_contracts.json_loads(doc.summary_json)}
	if doc.status != "needs_review" or value["revision"] != expected_revision:
		frappe.throw(_("reconciliation.request_conflict"))
	if not all(row["ok"] for row in checks):
		frappe.throw(_("reconciliation.review_balance_mismatch"))
	for row in validated:
		row["balance_ok"] = "ok"
	if "stmt_closing" not in (value["anchor_record"].get("anchor_overrides") or {}):
		header["stmt_closing"] = validated[-1]["balance"]
	decoded = (validated, [bank_recon_contracts.gl_row(row) for row in value["gl_rows"]])
	results, summary = _match(decoded, header)
	run_values = run_info(decoded, summary["matched_count"], value["warnings"],
		value["anchor_record"], value["lang"])
	value.update(revision=2, confirmation={"digest": fingerprint, "rows": canonical,
		"by": frappe.session.user, "at": str(now_datetime()), "checks": checks})
	doc.update({"status": "completed", "row_count": len(results),
		"summary_json": bank_recon_contracts.json_dumps(summary),
		"run_info_json": bank_recon_contracts.json_dumps(run_values),
		"review_json": bank_recon_contracts.json_dumps(value),
		"source_digest": bank_recon_contracts.digest(header, *decoded),
		**{field: summary[field] for field in TASK_SUMMARY_FIELDS}})
	persist(doc)
	_persist_rows(doc, results)
	return {"name": doc.name, "status": doc.status, "revision": 2, "replayed": False,
		"row_count": len(results), "summary": summary}


TASK_SUMMARY_FIELDS = ("stmt_opening", "gl_opening", "stmt_closing", "gl_closing",
	"matched_count", "gl_debit_only_count", "gl_credit_only_count",
	"stmt_withdrawal_only_count", "stmt_deposit_only_count", "stmt_total_deposit",
	"stmt_total_withdrawal", "gl_total_credit", "gl_total_debit", "gl_debit_only_amount",
	"gl_credit_only_amount", "stmt_withdrawal_only_amount", "stmt_deposit_only_amount",
	"opening_diff", "formula_stmt_closing", "formula_diff")


def anchor_plan(header_values, anchors):
	"""Apply the caller's anchor overrides and describe them for storage.

	The deployed run replaced the amounts the reader saw with the user's values
	before matching, and stored both sides so the workbook can show OCR against
	user. A caller that supplies ``anchors`` gets the same: the snapshot comes from
	the values it passes (falling back to the header amount it also sent), the
	overrides win for matching, and both records are stored. An override may be the
	replacement amount alone; the stored record then states the reader's value too.
	"""
	if anchors in (None, ""):
		return header_values, {}
	if not isinstance(anchors, dict) or set(anchors) - (set(bank_recon_contracts.ANCHOR_KEYS)
			| {"overrides"}):
		raise bank_recon_contracts.ReconciliationContractError("reconciliation.run_info_invalid")
	raw = anchors.get("overrides") or {}
	if not isinstance(raw, dict):
		raise bank_recon_contracts.ReconciliationContractError("reconciliation.run_info_invalid")
	read = {key: anchors.get(key, header_values[key])
		for key in bank_recon_contracts.ANCHOR_KEYS}
	# A caller may name the replacement amount alone; its reader value then comes
	# from the snapshot, so the stored record keeps the deployed two-sided shape.
	normalized = {key: (value if isinstance(value, dict)
		else {"ocr": read.get(key), "user": value}) for key, value in raw.items()}
	# The stored record is validated before it is applied, so an unknown anchor or a
	# value that is not a number is refused instead of reaching the arithmetic.
	checked = bank_recon_contracts.run_info({"anchor_ocr": read,
		"anchor_overrides": normalized})
	read = [checked["anchor_ocr"].get(key) for key in bank_recon_contracts.ANCHOR_KEYS]
	overrides = checked.get("anchor_overrides") or {}
	effective = bank_warnings.apply_anchor_overrides(*read,
		*(overrides.get(key, {}).get("user") for key in bank_recon_contracts.ANCHOR_KEYS))
	values = dict(header_values)
	for key, amount in zip(bank_recon_contracts.ANCHOR_KEYS, effective[:4]):
		values[key] = amount
	record = {"anchor_ocr": checked["anchor_ocr"]}
	if overrides:
		record["anchor_overrides"] = overrides
	return values, record


def run_info(rows, matched_count, warnings, anchors, lang):
	"""Build the stored run record: the mismatch warnings and the anchor records.

	The mismatch warnings are the deployed rule applied to the rows this admission
	stored, in the language the caller runs in; a caller may add the run-level ones
	(a statement covering several accounts, a completeness finding) through
	``warnings``. ``anchors`` is the record the anchor plan already produced.
	"""
	values = dict(anchors or {})
	detected = bank_warnings.detect_recon_mismatch(
		bank_recon_contracts.matching_statement_rows(rows[0]),
		bank_recon_contracts.matching_gl_rows(rows[1]), matched_count, lang)
	extra = [] if warnings in (None, "") else warnings
	if not isinstance(extra, list):
		raise bank_recon_contracts.ReconciliationContractError("reconciliation.run_info_invalid")
	combined = detected + list(extra)
	if combined:
		values["warnings"] = combined
	return bank_recon_contracts.run_info(values)


def row_values(result):
	"""Store one matching result; -1 marks an absent layer or day distance."""
	return {
		"match_status": result.match_status,
		"match_layer": 0 if result.match_layer is None else int(result.match_layer),
		"date_diff_days": -1 if result.date_diff_days is None else int(result.date_diff_days),
		"stmt_date": result.stmt_date or "",
		"stmt_desc": result.stmt_desc or "",
		"stmt_withdrawal": result.stmt_withdrawal or 0.0,
		"stmt_deposit": result.stmt_deposit or 0.0,
		"stmt_balance": result.stmt_balance or 0.0,
		"stmt_balance_known": 0 if result.stmt_balance is None else 1,
		"stmt_confidence": result.stmt_confidence or "high",
		"stmt_balance_ok": "" if result.stmt_balance_ok is None else ("ok" if result.stmt_balance_ok else "mismatch"),
		"stmt_autocorrected": 1 if result.stmt_autocorrected else 0,
		"gl_date": result.gl_date or "",
		"gl_doc_no": result.gl_doc_no or "",
		"gl_account_code": result.gl_account_code or "",
		"gl_desc": result.gl_desc or "",
		"gl_debit": result.gl_debit or 0.0,
		"gl_credit": result.gl_credit or 0.0,
		"gl_balance": result.gl_balance or 0.0,
		"source_stmt_file": result.source_stmt_file or "",
		"source_gl_file": result.source_gl_file or "",
	}


def _stored_summary(doc):
	"""Rebuild the summary dataclass from the stored JSON, like the deployed reader."""
	value = bank_recon_contracts.json_loads(doc.summary_json)
	if not isinstance(value, dict):
		return bank_matching.BankReconSummary()
	try:
		return bank_matching.BankReconSummary(**{
			key: item for key, item in value.items()
			if key in bank_matching.BankReconSummary.__dataclass_fields__})
	except (TypeError, ValueError):
		return bank_matching.BankReconSummary()


def _export_row(row):
	"""Invert one stored result row into the deployed workbook row shape."""
	return bank_matching.BankReconRow(
		match_status=row.match_status,
		match_layer=None if row.match_layer == 0 else row.match_layer,
		stmt_date=row.stmt_date or None, stmt_desc=row.stmt_desc or "",
		stmt_withdrawal=row.stmt_withdrawal or 0.0, stmt_deposit=row.stmt_deposit or 0.0,
		stmt_balance=row.stmt_balance or 0.0, gl_date=row.gl_date or None,
		gl_doc_no=row.gl_doc_no or "", gl_account_code=row.gl_account_code or "",
		gl_desc=row.gl_desc or "", gl_debit=row.gl_debit or 0.0, gl_credit=row.gl_credit or 0.0,
		gl_balance=row.gl_balance or 0.0,
		date_diff_days=None if row.date_diff_days == -1 else row.date_diff_days,
		source_stmt_file=row.source_stmt_file or "", source_gl_file=row.source_gl_file or "",
		stmt_confidence=row.stmt_confidence or "high",
		stmt_balance_ok=None if not row.stmt_balance_ok else row.stmt_balance_ok == "ok",
		stmt_autocorrected=bool(row.stmt_autocorrected))


def export(task_id, lang="th"):
	"""Render the review workbook of one stored task; no ERP, model or file access.

	The workbook sees exactly what the task stored: its result rows, its summary and
	the per-file diagnostics and the run records recorded at admission. The run
	warnings and the anchor snapshot/overrides render only when the task carries
	them, exactly like the deployed workbook.
	"""
	doc = frappe.get_doc(TASK, task_id)
	bank_recon_access.require(doc.workspace)
	if doc.status != "completed":
		frappe.throw(_("reconciliation.review_required"))
	if lang not in EXPORT_LANGUAGES:
		lang = "th"
	rows = frappe.get_all(ROW, filters={"task": doc.name}, fields=list(ROW_EXPORT_FIELDS),
		order_by="row_index asc", limit_page_length=0)
	content = export_workbook([_export_row(row) for row in rows], _stored_summary(doc), lang=lang,
		parse_info=bank_recon_contracts.json_loads(doc.parse_info_json) or None,
		**_stored_run_records(doc))
	return {"filename": f"BankRecon_v2_{doc.name}_{(doc.bank_code or 'bank').upper()}.xlsx",
		"content": content}


def _stored_run_records(doc):
	"""The workbook's warning banner and manual-trace sections read these three keys."""
	stored = bank_recon_contracts.json_loads(doc.run_info_json)
	if not isinstance(stored, dict):
		return {}
	return {"warnings": stored.get("warnings"),
		"anchor_ocr": stored.get("anchor_ocr"),
		"anchor_overrides": stored.get("anchor_overrides")}


def records(workspace, start=0, limit=50):
	return TASKS.records(workspace, start, limit)


def detail(task_id, start=0, limit=100):
	return TASKS.detail(task_id, start, limit)


def delete(task_id):
	bank_recon_access.require(frappe.get_doc(TASK, task_id).workspace)
	frappe.get_doc(TASK, task_id, for_update=True)
	return TASKS.delete(task_id)


def delete_many(task_ids):
	for name in sorted(recon_tasks._batch_names(task_ids)):
		bank_recon_access.require(frappe.get_doc(TASK, name).workspace)
		frappe.get_doc(TASK, name, for_update=True)
	return TASKS.delete_many(task_ids)
