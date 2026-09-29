"""对账结果的在线复核：取出两边的输入 → 改 → 用同一套比对重算 → 保存（用户 2026-09-26）。

“结果页就是导出的那张复核表，可以在线修改，改字段后自动在线计算，改完可以保存。”

两边的原始输入按文件原顺序存在任务的 `inputs_json`（建任务、确认复核、每次保存时写），页面打开与重算都从它出发：
- 银行：对账单与总账两张明细原样给页面改；需复核的任务仍用复核冻结的输入；
- 收入：页面改的是税表一侧（每条结果行就是一条税表行）；总账原样取存的行，页面上改了某张的总账金额，
  就按改动差额给这张凭证补一行，改了科目就把这张凭证的行改成新科目。

2026-09-29 排查修正：原来已完成的任务不存原始输入，打开页面时从排好序的结果行倒推 —— 同一天的流水被重排、
期末取错行，什么都不改页面就和导出不一致；税表同一单号几行时总账被算几遍；同一凭证另一边的行（折扣借方）丢了；
多个收入科目只剩第一个。2026-09-29 之前建的任务没有 `inputs_json`：没改过就直接用存档（收入）或不动期末与余额（银行），与导出一致；
改了才按结果行还原（同一单号只还原一次、科目全留）—— 结果行里没有的总账行（如只有冲销的凭证）还原不回来。

重算走的是建任务时同一个比对函数，保存则整份替换结果行与汇总，并记一笔修订（谁、何时、第几版）。
"""
import json
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import (
	bank_matching,
	bank_recon_access,
	bank_recon_contracts,
	bank_recon_service,
	bank_warnings,
	income_recon_access,
	income_recon_contracts,
	income_recon_service,
	income_vat,
	messages,
	recon_contracts,
)
from pearnly_cowork.reconciliation.export import income_workbook
from pearnly_cowork.reconciliation.export import workbook as bank_workbook

BANK_ROW_FIELDS = ("match_status", "stmt_date", "stmt_desc", "stmt_withdrawal", "stmt_deposit", "stmt_balance",
	"stmt_balance_known", "stmt_confidence", "stmt_balance_ok", "stmt_autocorrected", "gl_date", "gl_doc_no",
	"gl_account_code", "gl_desc", "gl_debit", "gl_credit", "gl_balance", "row_index")


def _contract(function, *args):
	try:
		return function(*args)
	except bank_recon_contracts.ReconciliationContractError as error:
		frappe.throw(messages.error_text(error))


def _text(value):
	return "" if value is None else str(value)


# ── 银行 ──────────────────────────────────────────────────────────────────


def stored_inputs(doc):
	"""任务里按文件原顺序存的两边输入；2026-09-29 之前建的任务没有，回 None。"""
	value = bank_recon_contracts.json_loads(doc.get("inputs_json")) if doc.get("inputs_json") else None
	return value if isinstance(value, dict) and value else None


def bank_inputs(doc):
	review = bank_recon_contracts.json_loads(doc.review_json) or {}
	if review.get("statement_rows") is not None and doc.status == "needs_review":
		return {"statement": review["statement_rows"], "gl": review["gl_rows"]}
	stored = stored_inputs(doc)
	if stored is not None:
		return {"statement": stored.get("statement") or [], "gl": stored.get("gl") or []}
	rows = frappe.get_all(bank_recon_service.ROW, filters={"task": doc.name}, fields=list(BANK_ROW_FIELDS),
		order_by="row_index asc", limit_page_length=0)
	statement = [{"date": _text(row.stmt_date), "description": row.stmt_desc or "",
		"withdrawal": row.stmt_withdrawal or 0, "deposit": row.stmt_deposit or 0,
		"balance": row.stmt_balance if row.stmt_balance_known else None,
		"confidence": row.stmt_confidence or "high", "balance_ok": row.stmt_balance_ok or "",
		"autocorrected": int(row.stmt_autocorrected or 0)}
		for row in rows if not str(row.match_status).startswith("gl_")]
	ledger = [{"date": _text(row.gl_date), "doc_no": row.gl_doc_no or "", "account_code": row.gl_account_code or "",
		"description": row.gl_desc or "", "debit": row.gl_debit or 0, "credit": row.gl_credit or 0,
		"balance": row.gl_balance} for row in rows if not str(row.match_status).startswith("stmt_")]
	statement.sort(key=lambda row: row["date"])
	ledger.sort(key=lambda row: row["date"])
	return {"statement": bank_recon_contracts.json_loads(bank_recon_contracts.json_dumps(statement)),
		"gl": bank_recon_contracts.json_loads(bank_recon_contracts.json_dumps(ledger))}


def bank_notes(doc):
	"""这份对账为什么要人看一眼：一句话，页面标题下显示（用户 2026-09-28：只在页面写，Excel 不加）。

	「待检查」来自 PDF 对账单的复核诊断（读得不确定的行、按余额推算改过的金额、笔数 / 合计对不上）；
	期间不重叠、GL 行数太少、一笔都没匹配上等警告建任务时已算好存在运行记录里。"""
	notes = []
	if doc.status == "needs_review":
		diagnostics = (bank_recon_contracts.json_loads(doc.review_json) or {}).get("diagnostics") or {}
		if int(diagnostics.get("low_conf_count") or 0):
			notes.append(_("cowork.recon.note.low_conf").format(int(diagnostics["low_conf_count"])))
		if int(diagnostics.get("amount_fixed_count") or 0):
			notes.append(_("cowork.recon.note.amount_fixed").format(int(diagnostics["amount_fixed_count"])))
		if (diagnostics.get("completeness") or {}).get("ok") is False:
			notes.append(_("cowork.recon.note.completeness"))
	# 期间 / 行数 / 零匹配按当前的行、用看的人的语言现算（改过行也跟着准；存的是建任务时那种语言的原文）。
	inputs = bank_inputs(doc)
	statement, ledger = _contract(bank_recon_contracts.decode_rows, inputs["statement"], inputs["gl"])
	matched = int((bank_recon_contracts.json_loads(doc.summary_json) or {}).get("matched_count") or 0)
	lang = (frappe.local.lang or "th")[:2]
	notes += [text.replace("⚠️", "").strip() for text in bank_warnings.detect_recon_mismatch(
		bank_recon_contracts.matching_statement_rows(statement), bank_recon_contracts.matching_gl_rows(ledger),
		matched, lang if lang in ("th", "zh", "en", "ja") else "th")]
	return notes


def bank_header(doc):
	return bank_recon_contracts.header({"stmt_opening": doc.stmt_opening, "stmt_closing": doc.stmt_closing,
		"gl_opening": doc.gl_opening, "gl_closing": doc.gl_closing, "bank_code": doc.bank_code or "",
		"gl_account_code": doc.gl_account_code or ""})


def _ledger_amounts(rows):
	"""按行位置比借贷金额（页面按行改输入，位置不变）：只改单号 / 日期 / 摘要不算改了金额（2026-09-29 排查：
	原来连日期单号一起比，改个单号整列余额就被重算）。"""
	return [(row.get("debit") or Decimal(0), row.get("credit") or Decimal(0)) for row in rows]


def _statement_values(rows):
	return [(str(row.get("date") or ""), str(row.get("description") or ""), row.get("withdrawal") or Decimal(0),
		row.get("deposit") or Decimal(0), row.get("balance")) for row in rows]


def _ledger_net(rows):
	return sum(((row.get("debit") or Decimal(0)) - (row.get("credit") or Decimal(0)) for row in rows), Decimal(0))


def _follow_ledger_edits(header, ledger, stored):
	"""总账金额改过：期末按改动的借贷差跟着走，各行余额从期末倒推（银行科目借增贷减）；没改金额一律不动。

	2026-09-28 录制：把一笔借方 12,480 改成 12,840，匹配重算了，GL 期末 / 余额列仍是文件里的 252,716.14，
	「差额（应为 0）」变成 360.00。对账单那边早已「期末跟着最后一行余额走」，这里补上总账这边。"""
	if _ledger_amounts(stored) == _ledger_amounts(ledger):
		return
	header["gl_closing"] = header["gl_closing"] + _ledger_net(ledger) - _ledger_net(stored)
	balance = header["gl_closing"]
	for row in reversed(ledger):
		row["balance"] = balance
		balance -= (row.get("debit") or Decimal(0)) - (row.get("credit") or Decimal(0))


def bank_compute(doc, inputs):
	rows = _contract(bank_recon_contracts.decode_rows, inputs.get("statement") or [], inputs.get("gl") or [])
	given = bank_inputs(doc)
	before = _contract(bank_recon_contracts.decode_rows, given.get("statement") or [], given.get("gl") or [])
	header = bank_header(doc)
	# 对账单期末跟着改过的最后一行余额走（与复核确认同一口径），期初与总账两端不动；
	# 对账单一行没改（只是打开页面、或只改了总账）就沿用存的期末，不取最后一行（2026-09-29 排查）。
	changed = doc.status == "needs_review" or _statement_values(rows[0]) != _statement_values(before[0])
	if changed and rows[0] and rows[0][-1].get("balance") is not None:
		header["stmt_closing"] = rows[0][-1]["balance"]
	_follow_ledger_edits(header, rows[1], before[1])
	results, summary = bank_recon_service._match(rows, header)
	return rows, header, results, summary


def bank_workbook_bytes(doc, results, summary, lang):
	return bank_workbook(results, bank_matching.BankReconSummary(**summary), lang=lang,
		parse_info=bank_recon_contracts.json_loads(doc.parse_info_json) or None,
		**bank_recon_service._stored_run_records(doc))


def bank_save(doc, inputs):
	rows, header, results, summary = bank_compute(doc, inputs)
	if doc.status == "needs_review":
		# 需复核的任务照原复核口径：余额链要连得上才可保存。
		from pearnly_cowork.reconciliation import bank_review

		checks = bank_review.balance_chain(rows[0], header["stmt_opening"])
		if not all(check["ok"] for check in checks):
			frappe.throw(_("reconciliation.review_balance_mismatch"))
	frappe.db.delete(bank_recon_service.ROW, {"task": doc.name})
	doc.update({"status": "completed", "row_count": len(results),
		"inputs_json": recon_contracts.inputs_json(statement=rows[0], gl=rows[1]),
		"summary_json": bank_recon_contracts.json_dumps(summary),
		**{field: summary.get(field, 0) for field in bank_recon_service.TASK_SUMMARY_FIELDS}})
	_record(doc)
	bank_recon_service.persist(doc)
	bank_recon_service._persist_rows(doc, results)
	return results, summary


# ── 收入 ──────────────────────────────────────────────────────────────────


def income_inputs(doc):
	rows = frappe.get_all(income_recon_service.ROW, filters={"task": doc.name},
		fields=["doc_no", "vat_date", "customer_name", "vat_amount", "has_gl_match", "gl_amount", "account_codes"],
		order_by="row_index asc", limit_page_length=0)
	# 科目全留（一张凭证分几个收入科目时原来只剩第一个，页面与导出对不上 —— 2026-09-29 排查）
	vat = [{"doc_no": row.doc_no or "", "date": row.vat_date or "", "customer_name": row.customer_name or "",
		"vat_amount": row.vat_amount or 0, "has_gl": int(bool(row.has_gl_match)),
		"gl_amount": row.gl_amount if row.has_gl_match else None,
		"account_code": (row.account_codes or "").strip()} for row in rows]
	summary = income_recon_contracts.json_loads(doc.summary_json) or {}
	extra = ([{**item, "side": "credit"} for item in summary.get("gl_only_credit_items") or []]
		+ [{**item, "side": "debit"} for item in summary.get("gl_only_debit_items") or []])
	return {"vat": vat, "gl_only": extra}


def _number(value):
	if value in (None, ""):
		return None
	try:
		return round(float(value), 2)
	except (TypeError, ValueError):
		return None


def _gl_line(doc_no, date, account_code, amount, description=""):
	"""一行总账：净额为正记贷方、为负记借方（收入科目贷增借减）。"""
	return {"doc_no": doc_no, "norm_doc_no": "", "date": date, "account_code": account_code,
		"description": description, "debit": max(-amount, 0.0), "credit": max(amount, 0.0)}


def _stored_gl(doc, edited, before, account):
	"""存的原始总账 + 页面上的改动：改了某张的总账金额按差额补一行，改了科目把这张凭证的行改成新科目。"""
	gl_rows = [dict(row) for row in (stored_inputs(doc) or {}).get("gl") or []]
	for index, row in enumerate(edited):
		old = before[index] if index < len(before) else {}
		doc_no = _text(row.get("doc_no")).strip()
		new_amount, old_amount = _number(row.get("gl_amount")), _number(old.get("gl_amount"))
		if row.get("has_gl") and new_amount is not None and new_amount != old_amount:
			gl_rows.append(_gl_line(doc_no, _text(row.get("date")), _text(row.get("account_code")).split(",")[0].strip()
				or account, round(new_amount - (old_amount or 0.0), 2)))
		new_account, old_account = _text(row.get("account_code")).strip(), _text(old.get("account_code")).strip()
		if new_account and new_account != old_account:
			key = income_vat.normalize_doc_no(doc_no)
			for line in gl_rows:
				if income_vat.normalize_doc_no(_text(line.get("doc_no"))) == key:
					line["account_code"] = new_account
	return gl_rows


def _rebuilt_gl(edited, before, gl_only, account):
	"""2026-09-29 之前建的任务：按结果行还原总账。同一单号几行只还原一次（原来几行就算几遍），
	哪一行改过总账金额就用那一行的；科目按存的全部科目。"""
	gl_rows, groups = [], {}
	for index, row in enumerate(edited):
		if row.get("has_gl") and _number(row.get("gl_amount")) is not None:
			groups.setdefault(income_vat.normalize_doc_no(_text(row.get("doc_no")).strip()), []).append(index)
	for indices in groups.values():
		pick = next((index for index in indices if index < len(before)
			and _number(edited[index].get("gl_amount")) != _number(before[index].get("gl_amount"))), indices[0])
		row = edited[pick]
		gl_rows.append(_gl_line(_text(row.get("doc_no")).strip(), _text(row.get("date")),
			_text(row.get("account_code")).strip() or account, _number(row.get("gl_amount"))))
	for item in gl_only or []:
		amount = abs(float(item.get("amount") or 0))
		gl_rows.append(_gl_line(_text(item.get("doc_no")), _text(item.get("date")), account,
			-amount if item.get("side") == "debit" else amount, _text(item.get("name"))))
	return gl_rows


def _income_rows(doc, inputs):
	account = doc.gl_account_code or "4"
	edited = inputs.get("vat") or []
	vat_rows = []
	for row in edited:
		doc_no = _text(row.get("doc_no")).strip()
		vat_rows.append({"report_ref_no": doc_no, "report_reference_no": doc_no, "report_invoice_no": doc_no,
			"report_date": _text(row.get("date")), "report_buyer_name": _text(row.get("customer_name")),
			"report_amount_pre_vat": float(row.get("vat_amount") or 0)})
	before = income_inputs(doc)
	if stored_inputs(doc) is not None:
		gl_rows = _stored_gl(doc, edited, before["vat"], account)
	else:
		gl_rows = _rebuilt_gl(edited, before["vat"], before["gl_only"], account)
	return _contract(income_recon_contracts.decode_rows, gl_rows, vat_rows)


def _view_key(rows):
	return [(_text(row.get("doc_no")).strip(), _text(row.get("date")), _text(row.get("customer_name")),
		_number(row.get("vat_amount")), int(bool(row.get("has_gl"))), _number(row.get("gl_amount")),
		_text(row.get("account_code")).strip()) for row in rows or []]


def _stored_income(doc):
	"""旧任务没改过：页面就用存档的结果行与汇总（与导出同一份）。

	旧任务没存原始总账，按结果行倒推会丢掉结果行里没有的总账行（例如一张只有冲销的凭证），
	测试机实测一张旧任务页面总账比导出多 50,000（2026-09-29）；没改过就不倒推。"""
	rows = frappe.get_all(income_recon_service.ROW, filters={"task": doc.name},
		fields=["doc_no", "vat_date", "customer_name", "vat_amount", "has_gl_match", "gl_amount", "diff",
			"account_codes"], order_by="row_index asc", limit_page_length=0)
	results = [income_vat.ReconRow(doc_no=row.doc_no or "", date=row.vat_date or "",
		customer_name=row.customer_name or "", vat_amount=float(row.vat_amount or 0),
		gl_amount=float(row.gl_amount or 0) if row.has_gl_match else None,
		diff=float(row.diff or 0) if row.has_gl_match else None, account_codes=row.account_codes or "")
		for row in rows]
	stored = income_recon_contracts.json_loads(doc.summary_json) or {}
	summary = income_vat.GlVatSummary(**{field: stored.get(field, [] if field.endswith("_items") else 0.0)
		for field in income_vat.GlVatSummary.__dataclass_fields__})
	return None, results, summary


def income_compute(doc, inputs):
	if stored_inputs(doc) is None and _view_key(inputs.get("vat")) == _view_key(income_inputs(doc)["vat"]):
		return _stored_income(doc)
	rows = _income_rows(doc, inputs)
	results, summary = income_vat.reconcile_gl_vat(
		income_recon_contracts.matching_gl_rows(rows[0]), income_recon_contracts.matching_vat_rows(rows[1]),
		amount_tolerance=float(doc.amount_tolerance or income_recon_contracts.AMOUNT_TOLERANCE))
	return rows, results, summary


def income_workbook_bytes(results, summary, lang):
	return income_workbook.export_gl_vat_excel(results, summary, lang=lang)


def income_save(doc, inputs):
	rows, results, summary = income_compute(doc, inputs)
	if rows is None:
		return results, summary  # 旧任务什么都没改：存档原样，不写倒推出来的总账
	summary_value = {field: getattr(summary, field) for field in summary.__dataclass_fields__}
	matched, findings, unmatched = income_recon_service.result_counts(results)
	frappe.db.delete(income_recon_service.ROW, {"task": doc.name})
	doc.update({"row_count": len(results), "gl_row_count": len(rows[0]), "matched_count": matched,
		"inputs_json": recon_contracts.inputs_json(gl=rows[0]),
		"diff_count": findings, "unmatched_count": unmatched,
		"summary_json": income_recon_contracts.json_dumps(summary_value),
		**{field: summary_value[field] for field in income_recon_service.TASK_SUMMARY_FIELDS}})
	_record(doc)
	income_recon_service.persist(doc)
	for index, result in enumerate(results):
		income_recon_service.persist(frappe.get_doc({"doctype": income_recon_service.ROW,
			"record_id": f"{doc.name}-{index + 1}", "workspace": doc.workspace, "task": doc.name,
			"row_index": index + 1, **income_recon_service.row_values(result)}))
	return results, summary


# ── 共用 ──────────────────────────────────────────────────────────────────

KINDS = {
	"bank": (bank_recon_access, bank_recon_service.TASK),
	"income": (income_recon_access, income_recon_service.TASK),
}


def task(kind, task_id, *, for_update=False):
	if kind not in KINDS:
		frappe.throw(_("reconciliation.type_invalid"))
	access, doctype = KINDS[kind]
	if not task_id or not frappe.db.exists(doctype, task_id):
		frappe.throw(_("reconciliation.task_not_found"), frappe.DoesNotExistError)
	doc = frappe.get_doc(doctype, task_id, for_update=for_update)
	access.require(doc.workspace)
	return doc


def revision(doc):
	history = json.loads(doc.get("edit_json") or "[]")
	return history[-1]["revision"] if history else 1


def _record(doc):
	history = json.loads(doc.get("edit_json") or "[]")
	history.append({"revision": (history[-1]["revision"] if history else 1) + 1,
		"by": frappe.session.user, "at": str(now_datetime())})
	doc.edit_json = json.dumps(history[-50:], ensure_ascii=False)
