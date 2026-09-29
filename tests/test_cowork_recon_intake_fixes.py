"""2026-09-29 离线复现后改正的 Cowork 产品问题（录入工作台、银行对账、收入对账、销项税报告核查）。

每条用例对应一个复现过的现象；只调纯函数或给假任务对象，不连数据库、网络或模型。
"""
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

import frappe

from pearnly_cowork.contracts import history as history_contract
from pearnly_cowork.push.express.history import flatten_history_for_mrerp
from pearnly_cowork.reconciliation import (
	bank_recon_contracts,
	bank_review,
	recon_contracts,
	revision,
	vat_check_core,
	vat_parse,
)


class FakeDoc:
	"""结果页重算只读任务上的这些字段。"""

	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


# ── 销项税报表 / 收入对账税表解析 ─────────────────────────────────────────

HEADER = ["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่าสินค้า", "ภาษีมูลค่าเพิ่ม"]


class ReportCells(unittest.TestCase):
	def test_whole_numbers_stored_as_floats_read_without_a_trailing_point_zero(self):
		# 旧 .xls（xlrd）把数字一律读成浮点：发票号 1001.0、分支 1.0，一致的行全判「疑似匹配」
		result = vat_parse.excel_rows([HEADER, ["01/06/2569", 1001.0, "ABC", 3101234567890.0, 1.0, 1000.0, 70.0]])
		row = result["rows"][0]
		self.assertEqual(row["report_invoice_no"], "1001")
		self.assertEqual(row["report_buyer_tax_id"], "3101234567890")
		self.assertEqual(row["report_buyer_branch"], "00001")
		self.assertEqual((row["report_amount_pre_vat"], row["report_vat_amount"]), (1000.0, 70.0))

	def test_a_tax_id_stored_as_a_number_gets_its_leading_zero_back(self):
		result = vat_parse.excel_rows([HEADER, ["01/06/2569", "IV001", "ABC", 105556012345, "00000", 1000, 70]])
		self.assertEqual(result["rows"][0]["report_buyer_tax_id"], "0105556012345")
		# 文字格子照原样：少一位就是少一位，不替人补
		result = vat_parse.excel_rows([HEADER, ["01/06/2569", "IV001", "ABC", "105556012345", "00000", 1000, 70]])
		self.assertNotEqual(result["rows"][0]["report_buyer_tax_id"], "0105556012345")

	def test_a_total_row_labelled_in_a_later_cell_is_not_an_invoice(self):
		# 合计写在第二、三格（第一格空着）时原来被当成一张发票，税表合计翻倍
		rows = [["Date", "Invoice No.", "Customer", "Net Amount", "VAT"],
			["01/06/2026", "IV001", "ABC", "1,000.00", "70.00"],
			["02/06/2026", "IV002", "DEF", "2,000.00", "140.00"],
			["", "Total", "", "3,000.00", "210.00"]]
		result = vat_parse.excel_rows(rows)
		self.assertEqual([row["report_invoice_no"] for row in result["rows"]], ["IV001", "IV002"])
		self.assertEqual(result["meta"].get("total_amount_pre_vat"), 3000.0)
		rows[-1] = ["", "", "Grand Total", "3,000.00", "210.00"]
		self.assertEqual(len(vat_parse.excel_rows(rows)["rows"]), 2)

	def test_a_customer_whose_name_starts_with_a_total_word_stays_a_row(self):
		rows = [HEADER, ["01/06/2569", "IV001", "รวมโชค จำกัด", "0105556012345", "00000", 1000, 70],
			["", "IV002", "Total Energies", "0105556012346", "00000", 500, 35]]
		self.assertEqual([row["report_invoice_no"] for row in vat_parse.excel_rows(rows)["rows"]], ["IV001", "IV002"])


# ── 销项税报告核查的金额口径 ─────────────────────────────────────────────


class VatCheckTotals(unittest.TestCase):
	def test_a_report_with_only_a_total_column_is_not_taxed_twice(self):
		# 表头 Amount / VAT / Total：原来按「合计 + VAT」算成 1,140，一致的行判成金额不一致
		self.assertEqual(vat_check_core._get_rep_total({"report_amount": 1070.0, "report_vat_amount": 70.0}), 1070.0)
		self.assertEqual(vat_check_core._get_rep_total(
			{"report_amount_pre_vat": 1000.0, "report_vat_amount": 70.0, "report_amount": 1070.0}), 1070.0)
		self.assertEqual(vat_check_core._get_rep_total({"report_amount_pre_vat": 1000.0, "report_vat_amount": 70.0}),
			1070.0)

	def test_an_invoice_sheet_without_a_total_column_gets_net_plus_vat(self):
		# 发票表只有未税、VAT 两列：原来合计取空按 0 比
		invoice = vat_check_core.report_row_to_invoice(
			{"report_invoice_no": "IV001", "report_amount_pre_vat": 1000.0, "report_vat_amount": 70.0}, "inv.xlsx")
		self.assertEqual(invoice["total_amount"], 1070.0)
		self.assertEqual(vat_check_core._get_inv_total(invoice), 1070.0)
		given = vat_check_core.report_row_to_invoice({"report_amount": 999.0, "report_amount_pre_vat": 1000.0}, "x")
		self.assertEqual(given["total_amount"], 999.0)


# ── 银行对账：复核、总账负数、结果页重算 ─────────────────────────────────


class BankReview(unittest.TestCase):
	def test_a_blank_balance_is_a_mismatch_not_a_crash(self):
		# 待复核任务清空某行余额后保存 / 确认：原来 NoneType.quantize 弹系统错误
		rows = [{"deposit": Decimal("100"), "withdrawal": Decimal("0"), "balance": None},
			{"deposit": Decimal("50"), "withdrawal": Decimal("0"), "balance": Decimal("1150")}]
		checks = bank_review.balance_chain(rows, Decimal("1000"))
		self.assertEqual([check["ok"] for check in checks], [False, True])
		self.assertEqual(checks[0]["expected"], "1100.00")


class BankLedgerSign(unittest.TestCase):
	def test_a_negative_debit_is_recorded_as_a_credit(self):
		row = bank_recon_contracts.gl_row({"date": "2026-06-02", "doc_no": "JV1", "account_code": "",
			"description": "", "debit": "-500", "credit": "0", "balance": None})
		self.assertEqual((row["debit"], row["credit"]), (Decimal(0), Decimal("500")))


def statement(day, description, withdrawal, deposit, balance):
	return {"date": day, "description": description, "withdrawal": withdrawal, "deposit": deposit,
		"balance": balance, "confidence": "high", "balance_ok": "", "autocorrected": False}


def ledger(day, number, debit, credit, balance=None):
	return {"date": day, "doc_no": number, "account_code": "", "description": "", "debit": debit,
		"credit": credit, "balance": balance}


def bank_doc(inputs=None):
	return FakeDoc(name="CBR-1", status="completed", review_json="{}",
		inputs_json=recon_contracts.inputs_json(**inputs) if inputs else "",
		stmt_opening=1000, stmt_closing=1400, gl_opening=1000, gl_closing=1400, bank_code="", gl_account_code="")


# 同一天先扣手续费 100（余额 900）、再存入 500（余额 1,400，已匹配）
STATEMENT = [statement("2026-01-31", "FEE", "100", "0", "900"), statement("2026-01-31", "DEPOSIT", "0", "500", "1400")]
LEDGER = [ledger("2026-01-31", "RV1", "500", "0", "1500"), ledger("2026-01-31", "PV1", "0", "100", "1400")]


class BankResultPage(unittest.TestCase):
	def test_the_page_opens_on_the_stored_inputs_in_file_order(self):
		doc = bank_doc({"statement": bank_recon_contracts.decode_rows(STATEMENT, LEDGER)[0],
			"gl": bank_recon_contracts.decode_rows(STATEMENT, LEDGER)[1]})
		given = revision.bank_inputs(doc)
		self.assertEqual([row["description"] for row in given["statement"]], ["FEE", "DEPOSIT"])
		_rows, header, _results, summary = revision.bank_compute(doc, given)
		self.assertEqual(header["stmt_closing"], Decimal("1400"))
		self.assertEqual(summary["stmt_closing"], 1400.0)

	def test_an_old_task_opened_without_edits_keeps_its_closing(self):
		# 2026-09-29 之前的任务没存原始输入：按结果行还原时同一天的行会重排（已匹配的排前面），
		# 原来取最后一行余额当期末 —— 什么都不改页面就变成 900、差额 −500
		matched = frappe._dict(match_status="matched", stmt_date="2026-01-31", stmt_desc="DEPOSIT",
			stmt_withdrawal=0, stmt_deposit=500, stmt_balance=1400, stmt_balance_known=1, stmt_confidence="high",
			stmt_balance_ok="", stmt_autocorrected=0, gl_date="2026-01-31", gl_doc_no="RV1", gl_account_code="",
			gl_desc="", gl_debit=500, gl_credit=0, gl_balance=1500, row_index=1)
		fee = frappe._dict(match_status="stmt_withdrawal_only", stmt_date="2026-01-31", stmt_desc="FEE",
			stmt_withdrawal=100, stmt_deposit=0, stmt_balance=900, stmt_balance_known=1, stmt_confidence="high",
			stmt_balance_ok="", stmt_autocorrected=0, gl_date=None, gl_doc_no=None, gl_account_code=None,
			gl_desc=None, gl_debit=None, gl_credit=None, gl_balance=None, row_index=2)
		doc = bank_doc()
		with mock.patch.object(revision.frappe, "get_all", return_value=[matched, fee]):
			given = revision.bank_inputs(doc)
			self.assertEqual(given["statement"][-1]["description"], "FEE")
			_rows, header, _results, _summary = revision.bank_compute(doc, given)
		self.assertEqual(header["stmt_closing"], Decimal("1400"))

	def test_an_edited_statement_still_moves_the_closing(self):
		doc = bank_doc({"statement": bank_recon_contracts.decode_rows(STATEMENT, LEDGER)[0],
			"gl": bank_recon_contracts.decode_rows(STATEMENT, LEDGER)[1]})
		edited = revision.bank_inputs(doc)
		edited["statement"][1]["balance"] = "1410"
		_rows, header, _results, _summary = revision.bank_compute(doc, edited)
		self.assertEqual(header["stmt_closing"], Decimal("1410"))


# ── 收入对账结果页重算 ───────────────────────────────────────────────────


def income_row(number, vat_amount, gl_amount, accounts="4110"):
	return frappe._dict(doc_no=number, vat_date="2026-06-01", customer_name="ACME", vat_amount=vat_amount,
		has_gl_match=1, gl_amount=gl_amount, account_codes=accounts)


def income_gl(number, account, debit, credit):
	return {"doc_no": number, "norm_doc_no": "", "date": "2026-06-01", "account_code": account,
		"description": "", "debit": debit, "credit": credit}


class IncomeResultPage(unittest.TestCase):
	def compute(self, doc, rows, edit=None):
		with mock.patch.object(revision.frappe, "get_all", return_value=rows):
			inputs = revision.income_inputs(doc)
			if edit:
				edit(inputs)
			_rows, results, summary = revision.income_compute(doc, inputs)
		return results, summary

	def stored_doc(self, gl_rows):
		from pearnly_cowork.reconciliation import income_recon_contracts
		decoded = income_recon_contracts.decode_rows(gl_rows, [])[0]
		return FakeDoc(name="CIR-1", gl_account_code="4", amount_tolerance=0.01, summary_json="{}",
			inputs_json=recon_contracts.inputs_json(gl=decoded))

	def test_the_other_side_of_a_voucher_and_every_account_are_kept(self):
		# 原来只按结果行的总账金额还原：同凭证的折扣借方丢了，科目只剩第一个，页面合计和导出对不上
		doc = self.stored_doc([income_gl("IV001", "4110", "0", "700"), income_gl("IV001", "4120", "0", "350"),
			income_gl("IV001", "4190", "50", "0")])
		results, summary = self.compute(doc, [income_row("IV001", 1000, 1000, "4110,4120,4190")])
		self.assertEqual((results[0].gl_amount, results[0].diff), (1000.0, 0.0))
		self.assertEqual(results[0].account_codes, "4110,4120,4190")
		self.assertEqual(summary.gl_total, 1000.0)

	def test_an_edited_ledger_amount_is_applied_once(self):
		doc = self.stored_doc([income_gl("IV002", "4110", "0", "1000")])
		rows = [income_row("IV002", 600, 600), income_row("IV002", 400, 400)]

		def edit(inputs):
			inputs["vat"][1]["gl_amount"] = 300
		results, summary = self.compute(doc, rows, edit)
		self.assertEqual(summary.gl_total, 900.0)
		self.assertEqual(round(sum(row.diff for row in results), 2), 100.0)

	def test_an_old_task_with_one_number_on_two_lines_counts_the_ledger_once(self):
		# 旧任务（没存原始输入）：两行都带着整张凭证 1,000，原来还原成两张 1,000，页面总账 2,000
		doc = FakeDoc(name="CIR-2", gl_account_code="4", amount_tolerance=0.01, summary_json="{}", inputs_json="")
		results, summary = self.compute(doc, [income_row("IV002", 600, 1000), income_row("IV002", 400, 1000)])
		self.assertEqual(summary.gl_total, 1000.0)
		self.assertEqual([row.diff for row in results], [0.0, 0.0])


# ── 录入工作台 ───────────────────────────────────────────────────────────


class ReviewSaveOfSeveralPages(unittest.TestCase):
	def pages(self):
		return [{"fields": {"date_raw": "01/06/2569", "total_amount": "1070"}},
			{"fields": {"date_raw": "28-Sep-26", "date": "2026-09-28", "total_amount": "500"}}]

	def test_an_untouched_page_with_an_odd_date_does_not_block_the_save(self):
		# 第 2 页日期写成「28-Sep-26」：入库时放宽，复核只改第 1 页金额，原来任何保存都报「无法识别票面日期」
		stored = history_contract.prepare_pages(self.pages(), recognized=True)
		edited = [dict(page, fields=dict(page["fields"])) for page in stored]
		edited[0]["fields"]["total_amount"] = "1071"
		prepared = history_contract.prepare_pages(edited, previous=stored)
		self.assertEqual(prepared[1]["fields"]["date"], "2026-09-28")
		self.assertEqual(prepared[0]["fields"]["date"], "2026-06-01")

	def test_an_edited_unreadable_date_is_still_refused(self):
		stored = history_contract.prepare_pages(self.pages(), recognized=True)
		edited = [dict(page, fields=dict(page["fields"])) for page in stored]
		edited[1]["fields"]["date_raw"] = "Sep 28th"
		with self.assertRaises(history_contract.HistoryContractError):
			history_contract.prepare_pages(edited, previous=stored)


class PushReadsTheReviewedPage(unittest.TestCase):
	def test_the_push_reads_the_page_the_review_page_shows(self):
		# 第 1 页是「สำเนา」副本、有票面；第 2 页读空：复核页改第 1 页，推送原来读空的第 2 页 → 「请选进项还是销项」
		pages = [{"is_copy": True, "fields": {"invoice_number": "IV001", "total_amount": "1070", "direction": "purchase"}},
			{"fields": {}}]
		self.assertEqual(history_contract.primary_page_fields(pages)["invoice_number"], "IV001")
		self.assertEqual(flatten_history_for_mrerp({"pages": pages})["fields"]["invoice_number"], "IV001")

	def test_with_nothing_readable_the_first_original_page_is_kept(self):
		pages = [{"is_copy": True, "fields": {}}, {"fields": {"buyer_name": "X"}}]
		self.assertEqual(history_contract.primary_page(pages), pages[1])
		self.assertEqual(history_contract.primary_page_fields(pages), {})

	def test_an_ordinary_record_is_unchanged(self):
		pages = [{"fields": {"invoice_number": "A"}}, {"fields": {"invoice_number": "B"}}]
		self.assertEqual(flatten_history_for_mrerp({"pages": pages})["fields"]["invoice_number"], "A")


class StoredInputs(unittest.TestCase):
	def test_decimals_and_dates_are_stored_as_text(self):
		value = recon_contracts.json_loads(recon_contracts.inputs_json(
			statement=[{"date": date(2026, 1, 31), "balance": Decimal("1400.00")}], gl=None))
		self.assertEqual(value, {"statement": [{"date": "2026-01-31", "balance": "1400.00"}]})


if __name__ == "__main__":
	unittest.main()
