"""Native bank reconciliation, PDF row review and stored-result export."""
import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import (
	bank_statement,
	naming,
	bank_intake,
	bank_matching,
	bank_recon_merge,
	bank_parse,
	bank_recon_access,
	bank_recon_contracts,
	bank_review,
	bank_sheets,
	bank_warnings,
	bank_groups,
	bank_card_fee,
	preset_store,
	presets,
	recon_contracts,
	recon_tasks,
)
from pearnly_cowork.reconciliation.bank_recon_access import ROW, TASK
from pearnly_cowork.reconciliation.bank_recon_documents import persist
from pearnly_cowork.reconciliation.export import workbook as export_workbook

EXPORT_LANGUAGES = ("th", "en", "zh", "ja")
ROW_EXPORT_FIELDS = ("match_status", "match_layer", "date_diff_days", "stmt_date", "stmt_desc",
	"stmt_withdrawal", "stmt_deposit", "stmt_balance", "stmt_confidence", "stmt_balance_ok",
	"stmt_autocorrected", "gl_date", "gl_doc_no", "gl_account_code", "gl_desc", "gl_debit",
	"gl_credit", "gl_balance", "source_stmt_file", "source_gl_file", "group_no")


LIST_FIELDS = ("name", "title", "reconciliation_type", "bank_code", "status", "row_count",
	"matched_count", "formula_diff", "created_at")
DETAIL_FIELDS = ("name", "title", "reconciliation_type", "bank_code", "gl_account_code", "status",
	"row_count", "source_format", "source_digest", "created_by", "created_at")
TASKS = recon_tasks.ReconTasks(TASK, ROW, bank_recon_access, "CBR", LIST_FIELDS, DETAIL_FIELDS)


def create(workspace, title, request_id, statement_rows, gl_rows, header=None,
		reconciliation_type="bank", source_format=bank_recon_contracts.SOURCE_FORMAT,
		parse_info=None, warnings=None, anchors=None, lang="th", *, _review=None, replace_task=""):
	"""Validate, match and store one reconciliation inside a single transaction.

	``replace_task``：答了要重跑的预设问题后，用原文件重跑的新结果替换原来那份（同一事务里先删后建，建失败原来的还在）。"""
	bank_recon_access.require(workspace)
	name, request_key = TASKS.identity(request_id)
	if replace_task and frappe.db.exists(TASK, replace_task):
		previous = frappe.get_doc(TASK, replace_task, for_update=True)
		if previous.workspace != workspace:
			frappe.throw(_("reconciliation.request_conflict"))
		TASKS._remove(previous)
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
	results, summary_value = ([], {}) if _review is not None else _match(rows, header_values,
		preset_rules(workspace, preset_scope(header_values["bank_code"], parse_values,
			header_values["gl_account_code"], rows[1])))
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
		"inputs_json": recon_contracts.inputs_json(statement=rows[0], gl=rows[1]),
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


def preset_scope(bank_code, parse_values, gl_account_code, gl_rows):
	"""这份对账按哪个账户记预设（``presets.bank_scope``）：账号末四位取读成功的几份账单里最常见的，
	总账科目取任务上的，没有就取总账行里最常见的。"""
	tail = presets.account_tail(item.get("account") for item in (parse_values or {}).get("stmt_files") or []
		if item.get("ok"))
	account = gl_account_code or presets.ledger_account(
		row.get("account_code") if isinstance(row, dict) else getattr(row, "account_code", "") for row in gl_rows)
	return presets.bank_scope(bank_code, tail, account)


def task_scope(doc, gl_rows):
	return preset_scope(doc.get("bank_code"), bank_recon_contracts.json_loads(doc.get("parse_info_json")) or {},
		doc.get("gl_account_code"), gl_rows)


def preset_rules(workspace, scope):
	return preset_store.rules(workspace, "bank", scope)


def _match(rows, header_values, rules=None):
	"""``rules``：这个账户的对账预设（``{规则: 值}``，见 ``presets``）；预设了汇总入账就把唯一解的合计组当作匹配。"""
	statement = bank_recon_contracts.matching_statement_rows(rows[0])
	ledger = bank_recon_contracts.matching_gl_rows(rows[1])
	amounts = dict(stmt_opening=float(header_values["stmt_opening"]), gl_opening=float(header_values["gl_opening"]),
		stmt_closing=float(header_values["stmt_closing"]), gl_closing=float(header_values["gl_closing"]),
		bank_code=header_values["bank_code"], gl_account_code=header_values["gl_account_code"])
	results, summary = bank_matching.reconcile(statement, ledger, **amounts)
	if (rules or {}).get(presets.GROUPED) == presets.YES:
		results, count = bank_groups.accept(results)
		if count:
			summary = bank_matching.summarize(results, statement, ledger, **amounts)
	rate = (rules or {}).get(presets.CARD_FEE)
	if rate and rate != presets.NO:
		# 刷卡净额（预设了手续费率）：到账净额配刷卡毛额，手续费单列一行「银行已扣、总账未记」，公式照样平
		results, count = bank_card_fee.accept(results, rate, _("reconciliation.preset.card_fee_row").format(rate))
		if count:
			summary = bank_matching.summarize(results, statement, ledger, **amounts)
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
		on_stage=None, statement_files=None, gl_files=None, replace_task=""):
	"""Store one reconciliation from **files**, through the native readers.

	The bytes are handed in by the caller (the file store stays with the caller's
	domain); ``bank_intake`` reads them with the native deterministic readers first
	and falls back to the injected ``model_rows`` only when they cannot decode the
	file. Everything after the decode is the same validated path as ``create``. A
	file the readers and the model both refuse is an error, not an empty run;
	per-file diagnostics are stored so the workbook's File Info section shows the
	same numbers the deployed run recorded.

	``statement_files`` / ``gl_files`` (``[(bytes, filename), ...]``) take several files per side, as the
	deployed ``bank_v2_submit`` did (a passbook photographed page by page, a ledger split in parts): each
	file is read on its own, a side fails only when none of its files reads, and the sides are merged by
	the deployed ``merge_statements`` / ``merge_gl_files`` (2026-09-29, found preparing the scan stress run).
	"""
	bank_recon_access.require(workspace)
	statement_files = list(statement_files or [(statement_bytes, statement_filename)])
	gl_files = list(gl_files or [(gl_bytes, gl_filename)])
	if (not statement_files or not gl_files
			or any(not isinstance(content, bytes) for content, _name in statement_files + gl_files)):
		frappe.throw(_("reconciliation.request_invalid"))
	# 对账中心的后台任务据此显示走到哪一步（读账单 / 读总账 / 核对）；不传就是原来的行为。
	stage = on_stage or (lambda _name: None)
	stage("statement")
	statements = _quiet_pages_skipped([(name, bank_intake.read_statement_file(content, name, model_rows=model_rows,
		read_page=read_page, page_count=page_count)) for content, name in statement_files])
	if not any(result.get("ok") for _name, result in statements):
		# 都读成功了、只是没有流水也没有余额（封面、只有汇总页）：说清楚缺的是明细页（2026-09-30 压测 BBL-CA-01）。
		codes = {result.get("error_code") for _name, result in statements}
		frappe.throw(_("reconciliation.statement_no_movement" if codes == {bank_intake.NO_MOVEMENT}
			else "reconciliation.statement_not_a_statement" if codes == {bank_sheets.NOT_A_STATEMENT}
			else "reconciliation.statement_is_ledger" if codes == {bank_sheets.LEDGER_NOT_STATEMENT}
			else "reconciliation.statement_unparsed"))
	stage("ledger")
	ledgers = [(name, bank_intake.read_ledger_file(content, name, account_code=gl_account_code,
		model_rows=model_rows)) for content, name in gl_files]
	if not any(result.get("ok") for _name, result in ledgers):
		frappe.throw(_("reconciliation.ledger_unparsed"))
	# 总账期间（整月）：几份账单怎么接，按期间里的余额链断得少来选（B08-01 的存折还带着前几个月的行）。
	mixed = other_accounts(statements)
	if mixed and any(result.get("ok") for name, result in statements if name not in mixed):
		# 判成别的账户的那几份不计入本次对账、结果里点名（2026-09-30 压测 B05-02：KBank 存折 2 月没有流水，两张 BBL
		# 存折照片成了整条账单，期初是 BBL 的数、还显示全通过）。其余文件都读不出时照旧全部保留。
		statements = [(name, {**result, "ok": False, "error_code": OTHER_ACCOUNT} if name in mixed else result)
			for name, result in statements]
		warnings = list(warnings or []) + [_("reconciliation.statement_other_account_left_out",
			lang=_warning_lang(lang)).format("、".join(mixed))]
	elif mixed:
		warnings = list(warnings or []) + [_("reconciliation.statement_other_account", lang=_warning_lang(lang)).format("、".join(mixed))]
	period = _months(row.date for result in (result for _name, result in ledgers if result.get("ok"))
		for row in result.get("rows") or [])
	statement = _merged_statement([result for _name, result in statements], period, mixed)
	# 当作读错的重复去掉的行逐条列出，方便核对（压测窗口 2026-09-30：不悄悄丢数据）；期间外的行本来就筛掉、另有提示。
	dropped = [row for row in statement.get("dropped") or []
		if not period or not row.date or period[0] <= row.date <= period[1]]
	if dropped:
		warnings = list(warnings or []) + [_("reconciliation.statement_lines_dropped", lang=_warning_lang(lang)).format(
			len(dropped), dropped_note(dropped))]
	skipped = [name for name, result in statements if result.get("error_code") == bank_intake.NO_MOVEMENT]
	if skipped:
		warnings = list(warnings or []) + [_("reconciliation.files_no_movement", lang=_warning_lang(lang)).format("、".join(skipped))]
	# 不是网银导出的表格（没有余额列）单独说，不叫用户「重新上传」同一份（2026-09-30 压测 B08-06）。
	listed = [name for name, result in statements if result.get("error_code") == bank_sheets.NOT_A_STATEMENT]
	if listed:
		warnings = list(warnings or []) + [_("reconciliation.files_not_a_statement", lang=_warning_lang(lang)).format(
			"、".join(listed))]
	# 总账放进了账单口：说是总账、该放右边（2026-09-30 压测 05-银行导出样本）。
	ledgers_left = [name for name, result in statements if result.get("error_code") == bank_sheets.LEDGER_NOT_STATEMENT]
	if ledgers_left:
		warnings = list(warnings or []) + [_("reconciliation.files_ledger_not_statement", lang=_warning_lang(lang)).format(
			"、".join(ledgers_left))]
	# 同一份表格里复制出来的工作表（逐行相同）只算了一次：点名，免得用户以为少读了（2026-09-30 压测 SISTER 6.69）。
	duplicated = [f"{name} [{copy} = {original}]" for name, result in statements if result.get("ok")
		for copy, original in result.get("duplicate_sheets") or []]
	if duplicated:
		warnings = list(warnings or []) + [_("reconciliation.files_duplicate_sheets", lang=_warning_lang(lang)).format(
			"、".join(duplicated))]
	unread = [name for name, result in statements + ledgers
		if not result.get("ok") and result.get("error_code") not in (bank_intake.NO_MOVEMENT, OTHER_ACCOUNT,
			bank_sheets.NOT_A_STATEMENT, bank_sheets.LEDGER_NOT_STATEMENT)]
	if unread:
		# 多份里有读不出的照旧对账（整侧全读不出才失败），结果里点名是哪几份（2026-09-29 压测：一张存折照片
		# 识别结果不明，用户看不出少了哪一页）。
		warnings = list(warnings or []) + [_("reconciliation.files_unread", lang=_warning_lang(lang)).format("、".join(unread))]
	# 这个账户（银行 + 账号尾号）定过对总账哪个科目（对账预设）：总账里真有这个科目就按它，不再猜；没有这个科目
	# （换了总账 / 预设不适用）照旧自己挑，挑不出再问。
	account_scope = presets.account_scope(statement["bank_code"], presets.account_tail(
		_account_of(result).get("account", "") for _name, result in statements if result.get("ok")))
	codes = {row.account_code for _name, result in ledgers if result.get("ok") for row in result.get("rows") or []
		if row.account_code}
	# 只有一个科目的总账不用选（也就不查预设）。
	preset = ("" if gl_account_code or not account_scope or len(codes) < 2
		else preset_rules(workspace, account_scope).get(presets.GL_ACCOUNT, ""))
	preset = preset if preset and any(code == preset or code.startswith(preset) for code in codes) else ""
	scores = [] if gl_account_code or preset else account_scores(statement["rows"], [result for _n, result in ledgers])
	chosen = preset or ("" if gl_account_code else likely_account(statement["rows"], [result for _n, result in ledgers]))
	questions = []
	if chosen:
		# 全科目总账、没指定科目：用与银行流水最吻合的那个科目（旧线上把所有科目的行都拿来对，Mr.erp 全科目
		# 总账 1 万多行，结果没法看；2026-09-29 压测 B01）。按这个科目重读：期初按它的科目标题行。
		gl_account_code = chosen
		ledgers = [(name, _only_account(bank_intake.read_ledger_file(content, name, account_code=chosen,
			model_rows=model_rows), chosen)) for content, name in gl_files]
		warnings = list(warnings or []) + [_("reconciliation.preset.gl_account_applied" if preset
			else "reconciliation.gl_account_auto", lang=_warning_lang(lang)).format(chosen)]
	elif account_scope and len([hits for hits, _code in scores if hits]) >= 2:
		# 几个科目都像、分不出：照旧全部拿来对，结果页问一次这个账户对哪个科目，答了用原文件重跑（识别结果复用）。
		questions.append({"rule": presets.GL_ACCOUNT, "scope": account_scope,
			"candidates": [{"code": code, "hits": hits} for hits, code in scores[:QUESTION_ACCOUNTS] if hits]})
	ledger = _merged_ledger([result for _name, result in ledgers], gl_account_code)
	statement, outside = within_ledger_period(statement, ledger)
	if outside:
		warnings = list(warnings or []) + [_(
			"reconciliation.statement_rows_outside" if outside[0] else "reconciliation.statement_all_outside",
			lang=_warning_lang(lang)).format(*outside)]
	ledger, period = within_statement_period(statement["rows"], ledger)
	if period:
		warnings = list(warnings or []) + [_("reconciliation.gl_period_trimmed", lang=_warning_lang(lang)).format(*period)]
	statement, notes = without_running_balance(statement, ledger)
	warnings = list(warnings or []) + [_(key, lang=_warning_lang(lang)) for key in notes]
	statement_names = "; ".join(name for name, _result in statements)
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
			"stmt_files": [{"file": name, "rows": len(parsed.get("rows") or []), "ok": bool(parsed.get("ok")),
				"error": parsed.get("error"), "error_code": parsed.get("error_code"),
				"bank_code": parsed.get("bank_code", ""), **_read_by_recognition(parsed), **_account_of(parsed),
				**({"unread_pages": parsed["unread_pages"]} if parsed.get("unread_pages") else {}),
				**({"duplicate_sheets": parsed["duplicate_sheets"]} if parsed.get("duplicate_sheets") else {})}
				for name, parsed in statements],
			# 诊断里的科目只记前 MAX_ACCOUNTS 个：全科目总账有上千个科目，记全了整单被契约拒掉（B01）。
			"gl_files": [{"file": name, "rows": len(parsed.get("rows") or []), "ok": bool(parsed.get("ok")),
				"error": parsed.get("error"), "error_code": parsed.get("error_code"),
				"accounts": (parsed.get("accounts") or [])[:bank_recon_contracts.MAX_ACCOUNTS]}
				for name, parsed in ledgers],
		}, warnings=warnings, anchors=anchors, lang=lang,
		_review=_review_diagnostics(statement, statement_names, [name for name, _r in statements]),
		replace_task=replace_task)
	return {**result, "questions": questions, "parsed": {
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


def _line_key(row):
	return (row.date, round(row.deposit or 0, 2), round(row.withdrawal or 0, 2), round(row.balance or 0, 2))


SAME_LINE_DAYS = 3


class _Lines:
	"""已收下的行：存、取、余额都相同、日期相差不超过 SAME_LINE_DAYS 天的算同一笔（同一页读成 03-26 / 03-27，
	余额相同已足够证明；B09-03，2026-09-30）。没有余额的行不算证据。"""

	def __init__(self):
		self.by_amount = {}

	def seen(self, row, other_than=None):
		if not row.balance:
			return False
		for day, source in self.by_amount.get(_line_key(row)[1:], ()):
			if other_than is not None and source == other_than:
				continue
			if day is None or row.date is None or abs((row.date - day).days) <= SAME_LINE_DAYS:
				return True
		return False

	def add(self, row):
		self.by_amount.setdefault(_line_key(row)[1:], []).append((row.date, getattr(row, "source_file", "")))


def _read_by_recognition(parsed):
	"""照片 / 扫描件是识别读出的（结果页据此在断点旁建议改传电子版）；电子版（表格、带文字层的 PDF）不记。"""
	return {"recognized": True} if (parsed.get("ok") and parsed.get("source") in ("enterprise", "model")
		and not parsed.get("text_layer")) else {}


def _account_of(parsed):
	"""读出的账号末四位（识别读出的账户身份，或表格账单每行的账号）；对账预设按账户记。"""
	identity = parsed.get("identity") or {}
	tail = presets.account_tail([identity.get("last4") or identity.get("account") or ""]
		+ [getattr(row, "account_no", "") for row in parsed.get("rows") or []])
	return {"account": tail} if tail else {}


def _warning_lang(lang):
	"""提示按提交时选的语言写（后台任务里的默认语言是站点语言；2026-09-29 压测：lang=zh 提交，新加的提示是泰文）。"""
	return lang if lang in ("th", "zh", "en", "ja") else None


def other_accounts(statements):
	"""同一侧几份账单读出的银行（或账号尾号）不一致：少数那几份多半是混进来的别的账户，点名给用户。"""
	from collections import Counter

	keys = {}
	for name, result in statements:
		identity = result.get("identity") or {} if result.get("ok") else {}
		bank = bank_parse.detect_bank(identity.get("bank") or "")
		keys[name] = (bank if bank != "generic" else "", identity.get("last4") or "")
	lines = {name: {_line_key(row) for row in (result.get("rows") or []) if row.balance}
		for name, result in statements if result.get("ok")}

	def overlaps(name):
		# 与别的文件有相同的行（日期、存、取、余额都一样）：同一账户，只是账号读法不同（B09-03，2026-09-30）。
		return any(lines.get(name, set()) & keys_ for other, keys_ in lines.items() if other != name)

	def near(first, second):
		# 尾号只差一位是读错，不是别的账户。
		return len(first) == len(second) == 4 and sum(a != b for a, b in zip(first, second)) <= 1

	for index in (0, 1):
		values = Counter(key[index] for key in keys.values() if key[index])
		if len(values) > 1:
			common = values.most_common(1)[0][0]
			return [name for name, key in keys.items() if key[index] and key[index] != common
				and not overlaps(name) and not (index == 1 and near(key[index], common))]
	return []


def same_lines_once(rows):
	"""不同文件里日期、存、取、余额都相同的行是同一笔（照片和 PDF 拍的是同一页，描述读法不同）：只留第一次出现的。
	同一份文件里的相同行照旧保留（同日同额的两笔）；没有余额的行不算证据。

	接不成余额链时退回旧线上的合并，它的去重键带着描述，B09-01 的 jpg 与 pdf 每笔都出现两次（2026-09-30 压测）。"""
	lines, kept = _Lines(), []
	for row in rows:
		if lines.seen(row, other_than=getattr(row, "source_file", "")):
			continue
		lines.add(row)
		kept.append(row)
	return kept


def chain_breaks(rows, period=None):
	"""余额链断几处（有余额的相邻两行对不上）；给了期间（起、止）就只数期间里的行（没有日期的也算）。"""
	if period:
		rows = [row for row in rows if not row.date or period[0] <= row.date <= period[1]]
	count, previous = 0, None
	for row in rows:
		if row.balance is None:
			continue
		if previous is not None and abs(previous + (row.deposit or 0) - (row.withdrawal or 0) - row.balance) > 0.01:
			count += 1
		previous = row.balance
	return count


def primary_merge(files, period=None, excluded=()):
	"""每一份都试着当主份，取余额链断得最少的（同样少取行多的）：行数最多的不一定最可信（B08-01 覆盖四个月的存折
	读错多，整月 PDF 才该当主份）。"""
	files = [list(rows) for rows in files if rows]
	if len(files) < 2:
		return []
	# 判成混入别的账户的那几份不当主份（B05-02 含他行：BBL 两张被选成主链，结果显示全通过，2026-09-30）。
	tried = [_merge_on(files[index], files[:index] + files[index + 1:]) for index in range(len(files))
		if not {getattr(row, "source_file", "") for row in files[index]} & set(excluded)]
	tried = [rows for rows in tried if rows]
	return min(tried, key=lambda rows: (chain_breaks(rows, period), -len(rows))) if tried else []


def _chained_copy(rows):
	"""同一份里同一笔出现几次（存、取、余额相同、日期差 ≤SAME_LINE_DAYS 天；一页读了两遍）：整体二选一 —— 各笔都留第一次
	出现的、或都留最后一次出现的，取余额链断得少的（一样少留第一次的）。逐笔各自挑会前后不一致（B08-01 第 15 轮：7,200 留
	了后一份、-2,000,000 留了前一份）。返回要留的下标。"""
	groups = []
	for index, row in enumerate(rows):
		for group in groups:
			first = rows[group[0]]
			if (row.balance and _line_key(row)[1:] == _line_key(first)[1:] and (not row.date or not first.date
					or abs((row.date - first.date).days) <= SAME_LINE_DAYS)):
				group.append(index)
				break
		else:
			groups.append([index])
	if all(len(group) == 1 for group in groups):
		return set(range(len(rows)))
	first = {group[0] for group in groups}
	last = {group[-1] for group in groups}
	breaks = lambda keep: chain_breaks([row for index, row in enumerate(rows) if index in keep])  # noqa: E731
	return last if breaks(last) < breaks(first) else first


class _Merged(list):
	"""合并好的行，另记下当作读错的重复去掉的行（结果里逐条列出，不悄悄丢数据）。"""

	def __init__(self, rows=()):
		super().__init__(rows)
		self.dropped = []


def dropped_note(rows, room=360):
	"""去掉的行：「6.jpg 2026-01-02 +21,588.50」这样一条条列，总长不超过 ``room``（单条警告最长 500 字）。"""
	parts = []
	for row in rows:
		amount = f"+{row.deposit:,.2f}" if row.deposit else f"-{row.withdrawal or 0:,.2f}"
		part = f"{getattr(row, 'source_file', '')} {row.date or ''} {amount}".strip()
		if len("；".join(parts + [part])) > room:
			return "；".join(parts) + "…"
		parts.append(part)
	return "；".join(parts)


def _merge_on(primary, others):
	"""以 ``primary`` 为主，别的份里的行：同一笔（_Lines）不要；前后都接得上的插进余额链的缺口；接不上的落在主份的
	日期范围内时，主链已经完整就当读错的重复不要（存折照片把 3,472.15 读成 30,472.15），主链还断着就按日期留下交人看；
	范围外的按日期放。两份以上才做；主份没有余额返回空。"""
	if not any(row.balance for row in primary):
		return []
	merged, lines = _Merged(), _Lines()
	# 主份自己把同一段读了两遍（B08-01 的 11.68-2.2.69.pdf：01-12 那两笔一遍排在最前、一遍在正确位置，2026-09-30
	# 压测第 15 轮）：同一笔留在余额链上接得上前一行的那一份，别的当重复去掉、同样列出。
	keep = _chained_copy(primary)
	for index, row in enumerate(primary):
		if index not in keep:
			merged.dropped.append(row)
			continue
		lines.add(row)
		merged.append(row)
	days = [row.date for row in merged if row.date]
	span = (min(days), max(days)) if days else None

	def step(first, second):
		# 与 chain_files 同一口径：方向可能读反（手续费读成存入，B05 KBank8229），两个方向都试，合并后再统一定案。
		amount = (second.deposit or 0) + (second.withdrawal or 0)
		return first.balance is not None and abs(abs(second.balance - first.balance) - amount) <= 0.01

	def fits(before, row, after):
		ok_before = before is None or step(before, row)
		ok_after = after is None or (after.balance is not None and step(row, after))
		return ok_before and ok_after and not (before is None and after is None)

	leftovers = []
	for rows in others:
		for row in rows:
			if lines.seen(row):
				continue
			spot = None
			if row.balance is not None:
				for index in range(len(merged) + 1):
					before = merged[index - 1] if index else None
					after = merged[index] if index < len(merged) else None
					# 只插进断开的地方或两端：已经接好的两行之间不再插。
					if before is not None and after is not None and before.balance is not None and fits(
							before, after, None):
						continue
					if fits(before, row, after):
						spot = index
						break
			if spot is None:
				leftovers.append(row)
				continue
			merged.insert(spot, row)
			lines.add(row)
	whole = chain_breaks(merged) == 0

	def one_digit_apart(first, second):
		# 读错一位：同样位数只差一位（4,612,513.06 / 4,612,713.06），或多读出一位（30,472.15 / 3,472.15）。
		a, b = f"{abs(first):.2f}", f"{abs(second):.2f}"
		if len(a) == len(b):
			return sum(x != y for x, y in zip(a, b)) == 1
		longer, shorter = (a, b) if len(a) > len(b) else (b, a)
		return len(longer) == len(shorter) + 1 and any(longer[:i] + longer[i + 1:] == shorter for i in range(len(longer)))

	def echoed(row):
		# 读错的重复：主链完整、日期在主份范围内，而且附近几天有一行余额或金额与它相同、或只差一位数字（存折把 1,326
		# 读成 1,328、3,472.15 读成 30,472.15）。没有这种证据的行不丢，按日期留下交人看。
		for other in merged:
			if not (other.date and row.date and abs((other.date - row.date).days) <= SAME_LINE_DAYS):
				continue
			amounts = ((other.deposit or 0) + (other.withdrawal or 0), (row.deposit or 0) + (row.withdrawal or 0))
			balances = (other.balance, row.balance) if other.balance is not None and row.balance is not None else None
			if (balances and (abs(balances[0] - balances[1]) <= 0.01 or one_digit_apart(*balances))) or (
					abs(amounts[0] - amounts[1]) <= 0.05 or one_digit_apart(*amounts)):
				return True
		return False

	for row in leftovers:
		# 主链从头到尾都接得上时，首尾两天之间不可能漏掉真实的交易（漏了余额就对不上）：严格落在这中间、又接不进链的
		# 行只能是读错的重复（B08-01 的 6.jpg 把 01-06 / 01-07 几笔读成 01-02、金额余额也对不上，2026-09-30 压测）。
		# 首尾当天的行可能排在主链前后，仍要证据。
		if span and row.date and whole and (span[0] < row.date < span[1] or (
				span[0] <= row.date <= span[1] and echoed(row))):
			merged.dropped.append(row)
			continue
		index = next((i for i, other in enumerate(merged) if other.date and row.date and other.date > row.date),
			len(merged))
		merged.insert(index, row)
	return merged


def chain_files(files, boundaries=None, excluded=()):
	"""几张照片 / 几份账单接成一条余额链：每份内部保持原顺序，份与份按「这份（去掉已有的重叠行后）第一行
	倒推的期初 = 已接好的最后一行余额」衔接，从日期最早的一份开始；接不上的按日期接在后面。

	2026-09-29 压测 B01（18 张 KBank 照片）：旧线上按日期稳定排序，同一天分在几张照片上的行被打乱（余额
	287,574.99 → 562,264.36 → 451,134.99 → 290,074.99），期初也推错。重叠拍到的同一笔（日期、存、取、余额都相同）
	只留一次。少于两份、或有一份首末行没有余额，返回空（照旧线上）。
	"""
	from datetime import date as date_type

	files = [list(rows) for rows in files if rows]
	if len(files) < 2 or any(not rows[0].balance or not rows[-1].balance for rows in files):
		return []
	def joins(first_row, end):
		# 第一行的方向可能读反（手续费读成存入）：存入、支出两个方向都试。
		amount = (first_row.deposit or 0) + (first_row.withdrawal or 0)
		return bool(first_row.balance) and abs(abs(first_row.balance - end) - amount) <= 0.01

	# 链头：第一行接不到别的任何一份末尾的那份；同样的按最早日期。
	remaining = sorted(files, key=lambda rows: (
		bool({getattr(row, "source_file", "") for row in rows} & set(excluded)),
		any(joins(rows[0], other[-1].balance) for other in files if other is not rows),
		min((row.date for row in rows if row.date), default=date_type.max)))
	merged, seen = [], _Lines()

	def append(rows):
		for row in rows:
			if seen.seen(row):
				continue
			seen.add(row)
			merged.append(row)

	append(remaining.pop(0))
	while remaining:
		end = merged[-1].balance
		chosen, joined = remaining[0], False
		for rows in remaining:
			fresh = [row for row in rows if not seen.seen(row)]
			if fresh and joins(fresh[0], end):
				chosen, joined = rows, True
				break
		if not joined:
			fresh = [row for row in chosen if not seen.seen(row)]
			_mend_last_line(merged, fresh)
			if boundaries is not None and fresh:
				boundaries.append(fresh[0])
		remaining.remove(chosen)
		append(chosen)
	return merged


def _mend_last_line(merged, following):
	"""接不上时，照片最后一行的余额可能读错了（「362,264.99」读成 562,264.36）：它后面没有下一行可比，读取时的
	余额链修正按错的余额把金额「补」成了 20 万。金额是补出来的（标了已自动更正）、且下一张照片倒推的期初能用一笔
	比它小的金额接上倒数第二行时，改这一行的余额与金额（2026-09-29 压测 B01 IMG_2485 / IMG_2497）。"""
	if len(merged) < 2 or not following or not following[0].balance:
		return
	last, before = merged[-1], merged[-2]
	if not getattr(last, "amount_autocorrected", False) or not before.balance:
		return
	first = following[0]
	opening = round(first.balance - (first.deposit or 0) + (first.withdrawal or 0), 2)
	amount = round(opening - before.balance, 2)
	imputed = (last.deposit or 0) + (last.withdrawal or 0)
	if not amount or abs(amount) >= imputed:
		return
	last.balance = opening
	last.deposit, last.withdrawal = (amount, 0.0) if amount > 0 else (0.0, -amount)


def without_running_balance(statement, ledger):
	"""账单没有逐笔余额（网银查询截图、SCB「No Data Available」）：没有余额链可核。读出的期初 + 发生额 = 期末就照用；
	对不上或没印余额 —— 截图上的「ยอดเงินคงเหลือ」是查询当时的余额，不一定是期初 —— 就按总账期初计，期末 = 期初 + 发生额
	（2026-09-30 压测 GIZE BBL-SA-01：读出的「期初」其实是期末）。返回 ``(账单, 提示词条)``。"""
	rows = statement.get("rows") or []
	if any(row.balance is not None for row in rows) or not (rows or statement.get("balances_missing")):
		return statement, []
	notes = ["reconciliation.statement_no_running_balance"] if rows else []
	movement = round(sum((row.deposit or 0) - (row.withdrawal or 0) for row in rows), 2)
	opening, closing = float(statement.get("opening") or 0), float(statement.get("closing") or 0)
	if opening and closing and abs(opening + movement - closing) <= 0.01 and not statement.get("balances_missing"):
		return statement, notes
	opening = float(ledger.get("opening") or 0)
	closing = round(opening + movement, 2)
	completeness = bank_statement.audit_completeness(rows, opening, closing, None)
	return {**statement, "opening": opening, "closing": closing, "completeness": completeness,
		"needs_review": bool(statement.get("low_conf_count") or statement.get("amount_fixed_count"))
			or not completeness["ok"]}, notes + ["reconciliation.statement_balances_from_ledger"]


OTHER_ACCOUNT = "other_account"


def _quiet_pages_skipped(statements):
	"""只有余额、没有流水的那几份：同一侧还有带流水的文件时不参与合并，按“没有流水，已跳过”列出；全都没有流水时
	只留第一份按 0 行对账（几份余额页没有日期，排不出先后）。"""
	quiet = [index for index, (_name, result) in enumerate(statements) if result.get("no_movement")]
	moving = any(result.get("ok") and result.get("rows") for _name, result in statements)
	skip = set(quiet if moving else quiet[1:])
	return [(name, {**result, "ok": False, "error_code": bank_intake.NO_MOVEMENT} if index in skip else result)
		for index, (name, result) in enumerate(statements)]


def _merged_statement(results, period=None, excluded=()):
	"""One file stays exactly as read; several are merged by the deployed ``merge_statements``."""
	read = [result for result in results if result.get("ok")]
	if len(results) == 1:
		return read[0]
	rows, opening, closing, bank_code = bank_recon_merge.merge_statements(list(results))
	boundaries = []
	chained = chain_files([result.get("rows") or [] for result in read], boundaries, excluded)
	if chained:
		rows = chained
	else:
		rows = same_lines_once(rows)
	# 期间重叠的几份（整月 PDF + 覆盖几个月的存折 + 照片）：首尾接不上，改以最全的一份为主、别的行按余额链插进缺口；
	# 两种接法取余额链断得少的（2026-09-30 压测 B08-01）。
	primary = primary_merge([result.get("rows") or [] for result in read], period, excluded)
	dropped = []
	if primary and chain_breaks(primary, period) < chain_breaks(rows, period):
		rows, boundaries, dropped = primary, [], list(primary.dropped)
	if rows and (chained or rows is primary) and rows[0].balance is not None:
		first = rows[0]
		opening = round(first.balance - (first.deposit or 0) + (first.withdrawal or 0), 2)
		closing = rows[-1].balance or closing
	if len(read) > 1 and rows:
		# 接好（或按日期合并）之后整条再定案一次：每张照片的第一行单独定案时没有前一行可比（B05 KBank8229：
		# 手续费 685 读成存入；有一张首末行没余额、接链退回按日期合并时也要做）。
		from pearnly_cowork.reconciliation.bank_intake import repair_balance_from_neighbours

		# 接不上的交界行（前后两张照片之间缺了东西，或混进了别的账户）不按跨照片的余额差补金额：金额反推允许 30%
		# 的差，B05-02 把 BBL 存折首行补成了 2,678,117.31（接的是 KBank 的余额）。定案后金额数值变了的还原成印出的；
		# 只翻方向（数值不变）的保留纠正。
		printed = [(row, row.deposit, row.withdrawal, row.direction_autocorrected) for row in boundaries]
		rows = bank_statement.finalize_rows(rows, opening)
		for row, deposit, withdrawal, flipped in printed:
			if abs((row.deposit or 0) + (row.withdrawal or 0) - (deposit or 0) - (withdrawal or 0)) > 0.01:
				row.deposit, row.withdrawal, row.amount_autocorrected = deposit, withdrawal, False
				row.direction_autocorrected = flipped
		repair_balance_from_neighbours(rows, opening)
	completeness = [result.get("completeness") or {} for result in read]
	return {"ok": True, "rows": list(rows), "row_count": len(rows), "opening": opening, "closing": closing, "dropped": dropped,
		"bank_code": bank_code if bank_code != "generic" else (read[0].get("bank_code") or ""),
		"source": read[0].get("source") or "", "parser_version": read[0].get("parser_version") or "",
		"balance_warn_count": sum(int(result.get("balance_warn_count") or 0) for result in read),
		"amount_fixed_count": sum(int(result.get("amount_fixed_count") or 0) for result in read),
		"low_conf_count": sum(int(result.get("low_conf_count") or 0) for result in read),
		"completeness": {**(completeness[0] if completeness else {}),
			"ok": all(item.get("ok", True) is not False for item in completeness)},
		"escalations": [item for result in read for item in result.get("escalations") or []],
		"needs_review": any(result.get("needs_review") for result in read)
			or len(read) != len(results)}


MATCH_DAYS = 5


def likely_account(statement_rows, ledgers):
	"""几个科目里与银行流水最吻合的那个：同金额（存入对借方、支出对贷方）、日期相差不超过 MATCH_DAYS 天的笔数最多，
	且至少吻合流水的五分之一（最少 2 笔）；只有一个科目或都对不上返回空（照旧全部拿来对）。"""
	scores = account_scores(statement_rows, ledgers)
	if len(scores) < 2:
		return ""
	best, code = scores[0]
	if best < max(2, len(statement_rows) / 5) or (len(scores) > 1 and scores[1][0] == best):
		return ""
	return code


def account_scores(statement_rows, ledgers):
	"""每个科目与银行流水吻合的笔数，多的在前：``[(笔数, 科目)]``；不到两个科目或没有流水回空。"""
	by_account = {}
	for result in ledgers:
		for row in (result.get("rows") or []) if result.get("ok") else []:
			if row.account_code and any(char.isdigit() for char in row.account_code):
				by_account.setdefault(row.account_code, []).append(row)
	if len(by_account) < 2 or not statement_rows:
		return []
	def hits(rows):
		index = {}
		for row in rows:
			for side, amount in (("in", row.debit), ("out", row.credit)):
				if amount:
					index.setdefault((side, round(amount, 2)), []).append(row.date)
		count = 0
		for row in statement_rows:
			side, amount = ("in", row.deposit) if row.deposit else ("out", row.withdrawal)
			dates = index.get((side, round(amount or 0, 2))) or []
			if any(row.date and day and abs((row.date - day).days) <= MATCH_DAYS for day in dates):
				count += 1
		return count
	return sorted(((hits(rows), code) for code, rows in by_account.items()), reverse=True)


QUESTION_ACCOUNTS = 6


def _months(days):
	"""日期所在的整月（起、止）；离中位日期一年以上的是读错的日期，不参与。"""
	import calendar
	from datetime import date as date_type

	days = sorted(day for day in days if day)
	if not days:
		return None
	middle = days[len(days) // 2]
	days = [day for day in days if abs((day - middle).days) <= 366]
	first, last = days[0], days[-1]
	return first.replace(day=1), date_type(last.year, last.month, calendar.monthrange(last.year, last.month)[1])


def within_ledger_period(statement, ledger):
	"""账单只留总账期间（总账行所在的整月）内的行：存折一页带着前几个月的旧行，旧线上全部列成「账单独有」。

	用户 2026-09-29 定：「旧行按总账期间筛掉吧」（有意偏离旧线上）。期初取期间开始前最后一行的余额，没有这一行
	就用留下的第一行倒推；期末取留下的最后一行。总账期间读不出（当月无发生额、没有带日期的行）不筛；账单全部落在
	期间外也不筛，照旧全部对账并提示。返回 ``(账单, 提示参数)``：筛了是 ``(行数, 起, 止)``，全在期间外是
	``(0, 起, 止)``，没筛是 ``None``。
	"""
	from datetime import date as date_type

	period = _months(row.date for row in ledger.get("rows") or [])
	if not period and ledger.get("period_start"):
		start = date_type.fromisoformat(ledger["period_start"])
		period = _months([start])  # 当月无发生额：表头印的起始日期所在的月
	rows = statement.get("rows") or []
	if not period or not rows:
		return statement, None
	start, end = period
	kept = [row for row in rows if not row.date or start <= row.date <= end]
	if len(kept) == len(rows):
		return statement, None
	before = [row for row in rows if row.date and row.date < start]
	after = [row for row in rows if row.date and row.date > end]
	if not any(row.date for row in kept) and not (before and after):
		# 账单全在期间的一侧（全早于或全晚于）：多半传错了期间，照旧全部对账并提示。
		return statement, (0, start.isoformat(), end.isoformat())
	# 账单跨过期间、期间里一笔都没有（B05-02：存折有 1/5 也有 3/5，2 月 0 笔）：就是这个月没有流水，
	# 筛成 0 行，期初期末都是期间开始前最后一行的余额。
	kept = [row for row in kept if row.date]
	if all(row.balance is None for row in rows):
		# 没有逐笔余额：期初期末由 without_running_balance 定。
		return {**statement, "rows": kept, "row_count": len(kept)}, (len(rows) - len(kept), start.isoformat(),
			end.isoformat())
	# 期初按期间内第一行倒推（它在余额链上的前一行就是期初）；期间前的行里可能混着旧存折页、年份读错的行（B08-01 的
	# 2016 年旧页、B08-02 读成 2025 年的 3 月行，2026-09-30 压测），按日期挑「期间前最后一行」会挑错。期间里一行都没有
	# 时才用期间前最后一行。
	first = next((row for row in kept if row.balance), None)
	if first is not None:
		opening = round(first.balance - (first.deposit or 0) + (first.withdrawal or 0), 2)
	elif before and before[-1].balance:
		opening = before[-1].balance
	else:
		opening = statement.get("opening")
	closing = next((row.balance for row in reversed(kept) if row.balance), opening)
	trimmed = {**statement, "rows": kept, "row_count": len(kept), "opening": opening, "closing": closing}
	return trimmed, (len(rows) - len(kept), start.isoformat(), end.isoformat())


def within_statement_period(statement_rows, ledger):
	"""总账期间比银行流水长（年初至今导出）：只留流水所在整月的行，之前的发生额滚进期初，之后的不要。

	返回 ``(总账, (起, 止, 新期初))``；不需要截取返回 ``(总账, None)``。流水没有日期或跨度超过一年（日期读错）不截。
	"""
	import calendar
	from datetime import date as date_type

	from pearnly_cowork.reconciliation.gl_sheets import attach_running_balance

	days = sorted(row.date for row in statement_rows if row.date)
	rows = ledger.get("rows") or []
	if not days or not rows:
		return ledger, None
	# 离中位日期一年以上的是读错的日期（1982 年这种），不参与定期间。
	middle = days[len(days) // 2]
	days = [day for day in days if abs((day - middle).days) <= 366]
	first, last = days[0], days[-1]
	if (last - first).days > 400:
		return ledger, None
	start = first.replace(day=1)
	end = date_type(last.year, last.month, calendar.monthrange(last.year, last.month)[1])
	before = [row for row in rows if row.date and row.date < start]
	after = [row for row in rows if row.date and row.date > end]
	if not before and not after:
		return ledger, None
	kept = [row for row in rows if not row.date or start <= row.date <= end]
	opening = round(float(ledger.get("opening") or 0) + sum(row.debit - row.credit for row in before), 2)
	attach_running_balance(kept, opening)
	closing = round(opening + sum(row.debit - row.credit for row in kept), 2)
	trimmed = {**ledger, "rows": kept, "row_count": len(kept), "opening": opening, "closing": closing}
	return trimmed, (start.isoformat(), end.isoformat(), f"{opening:,.2f}")


def _only_account(result, code):
	"""模型读出的总账行不经科目过滤（读取层只认表格的科目列），这里补上。"""
	if result.get("ok") and result.get("rows"):
		kept = [row for row in result["rows"] if not row.account_code or row.account_code.startswith(code)]
		result = {**result, "rows": kept, "row_count": len(kept), "accounts": [code]}
	return result


def _merged_ledger(results, account_code):
	read = [result for result in results if result.get("ok")]
	if len(results) == 1:
		return read[0]
	rows, accounts, opening, closing = bank_recon_merge.merge_gl_files(list(results), account_code)
	return {"ok": True, "rows": rows, "row_count": len(rows), "accounts": accounts, "opening": opening,
		"closing": closing, "source": read[0].get("source") or "",
		"needs_review": any(result.get("needs_review") for result in read) or len(read) != len(results)}


REVIEWABLE = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp")


def _review_diagnostics(statement, filename, names=None):
	# PDF 与照片读出的账单（旧线上 S8：「PDF / 图片账单 OCR 低信心或完整性不过 → 暂停等用户逐行核对」）。
	if (not any(name.lower().endswith(REVIEWABLE) for name in (names or [filename]))
			or not bank_review.needs_review(statement)):
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
		# 没有逐笔余额的账单：期末 = 期初 + 发生额（BBL-SA-01/06）。
		header["stmt_closing"] = validated[-1]["balance"] if validated[-1]["balance"] is not None else (
			(header["stmt_opening"] or 0) + sum(row["deposit"] - row["withdrawal"] for row in validated))
	decoded = (validated, [bank_recon_contracts.gl_row(row) for row in value["gl_rows"]])
	results, summary = _match(decoded, header, preset_rules(doc.workspace, task_scope(doc, decoded[1])))
	run_values = run_info(decoded, summary["matched_count"], value["warnings"],
		value["anchor_record"], value["lang"])
	value.update(revision=2, confirmation={"digest": fingerprint, "rows": canonical,
		"by": frappe.session.user, "at": str(now_datetime()), "checks": checks})
	doc.update({"status": "completed", "row_count": len(results),
		"summary_json": bank_recon_contracts.json_dumps(summary),
		"run_info_json": bank_recon_contracts.json_dumps(run_values),
		"review_json": bank_recon_contracts.json_dumps(value),
		"inputs_json": recon_contracts.inputs_json(statement=decoded[0], gl=decoded[1]),
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
		"group_no": int(result.group_no or 0),
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
		stmt_autocorrected=bool(row.stmt_autocorrected), group_no=int(getattr(row, "group_no", 0) or 0))


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
	return {"filename": naming.export_filename(doc.title,
		f"BankRecon_v2_{doc.name}_{(doc.bank_code or 'bank').upper()}"), "content": content}


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
