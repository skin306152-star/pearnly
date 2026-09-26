"""对账结果的在线复核：取出两边的输入 → 改 → 用同一套比对重算 → 保存（用户 2026-09-26）。

“结果页就是导出的那张复核表，可以在线修改，改字段后自动在线计算，改完可以保存。”

已完成的任务不单独存原始输入，但结果里带着两边的全部内容：
- 银行：每条结果行都有对账单一侧和 / 或总账一侧，按侧拆回两张明细（需复核的任务直接用冻结的输入）；
- 收入：每条结果行就是一条税表行，匹配到的总账金额按符号还原成贷方 / 借方
  （旧线上口径：税表正数取总账贷方、负数取总账借方转负）；总账有、税表没有的那几笔在汇总里逐条保存，一并还原。
重算走的是建任务时同一个比对函数，保存则整份替换结果行与汇总，并记一笔修订（谁、何时、第几版）。
"""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.reconciliation import (
	bank_matching,
	bank_recon_access,
	bank_recon_contracts,
	bank_recon_service,
	income_recon_access,
	income_recon_contracts,
	income_recon_service,
	income_vat,
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
		frappe.throw(_(str(error)))


def _text(value):
	return "" if value is None else str(value)


# ── 银行 ──────────────────────────────────────────────────────────────────


def bank_inputs(doc):
	review = bank_recon_contracts.json_loads(doc.review_json) or {}
	if review.get("statement_rows") is not None and doc.status == "needs_review":
		return {"statement": review["statement_rows"], "gl": review["gl_rows"]}
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


def bank_header(doc):
	return bank_recon_contracts.header({"stmt_opening": doc.stmt_opening, "stmt_closing": doc.stmt_closing,
		"gl_opening": doc.gl_opening, "gl_closing": doc.gl_closing, "bank_code": doc.bank_code or "",
		"gl_account_code": doc.gl_account_code or ""})


def bank_compute(doc, inputs):
	rows = _contract(bank_recon_contracts.decode_rows, inputs.get("statement") or [], inputs.get("gl") or [])
	header = bank_header(doc)
	# 对账单期末跟着改过的最后一行余额走（与复核确认同一口径），期初与总账两端不动。
	if rows[0] and rows[0][-1].get("balance") is not None:
		header["stmt_closing"] = rows[0][-1]["balance"]
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
	vat = [{"doc_no": row.doc_no or "", "date": row.vat_date or "", "customer_name": row.customer_name or "",
		"vat_amount": row.vat_amount or 0, "has_gl": int(bool(row.has_gl_match)),
		"gl_amount": row.gl_amount if row.has_gl_match else None,
		"account_code": (row.account_codes or "").split(",")[0].strip()} for row in rows]
	summary = income_recon_contracts.json_loads(doc.summary_json) or {}
	extra = ([{**item, "side": "credit"} for item in summary.get("gl_only_credit_items") or []]
		+ [{**item, "side": "debit"} for item in summary.get("gl_only_debit_items") or []])
	return {"vat": vat, "gl_only": extra}


def _income_rows(doc, inputs):
	account = doc.gl_account_code or "4"
	vat_rows, gl_rows = [], []
	for row in inputs.get("vat") or []:
		doc_no = _text(row.get("doc_no")).strip()
		amount = float(row.get("vat_amount") or 0)
		vat_rows.append({"report_ref_no": doc_no, "report_reference_no": doc_no, "report_invoice_no": doc_no,
			"report_date": _text(row.get("date")), "report_buyer_name": _text(row.get("customer_name")),
			"report_amount_pre_vat": amount})
		if row.get("has_gl") and row.get("gl_amount") not in (None, ""):
			gl_amount = float(row["gl_amount"])
			# 旧线上口径：税表正数对总账贷方，负数（贷项凭单）对总账借方并转负。
			credit, debit = (gl_amount, 0.0) if amount >= 0 else (0.0, -gl_amount)
			gl_rows.append({"doc_no": doc_no, "norm_doc_no": "", "date": _text(row.get("date")),
				"account_code": _text(row.get("account_code")) or account, "description": "",
				"debit": max(debit, 0.0), "credit": max(credit, 0.0)})
	for item in inputs.get("gl_only") or []:
		amount = abs(float(item.get("amount") or 0))
		gl_rows.append({"doc_no": _text(item.get("doc_no")), "norm_doc_no": "", "date": _text(item.get("date")),
			"account_code": account, "description": _text(item.get("name")),
			"debit": amount if item.get("side") == "debit" else 0.0,
			"credit": amount if item.get("side") != "debit" else 0.0})
	return _contract(income_recon_contracts.decode_rows, gl_rows, vat_rows)


def income_compute(doc, inputs):
	rows = _income_rows(doc, inputs)
	results, summary = income_vat.reconcile_gl_vat(
		income_recon_contracts.matching_gl_rows(rows[0]), income_recon_contracts.matching_vat_rows(rows[1]),
		amount_tolerance=float(doc.amount_tolerance or income_recon_contracts.AMOUNT_TOLERANCE))
	return rows, results, summary


def income_workbook_bytes(results, summary, lang):
	return income_workbook.export_gl_vat_excel(results, summary, lang=lang)


def income_save(doc, inputs):
	rows, results, summary = income_compute(doc, inputs)
	summary_value = {field: getattr(summary, field) for field in summary.__dataclass_fields__}
	matched, findings, unmatched = income_recon_service.result_counts(results)
	frappe.db.delete(income_recon_service.ROW, {"task": doc.name})
	doc.update({"row_count": len(results), "gl_row_count": len(rows[0]), "matched_count": matched,
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
