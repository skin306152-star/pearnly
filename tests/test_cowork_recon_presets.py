"""对账预设（用户 2026-09-30 批准）：规矩类的不确定问一次、按账户记住、以后照办。

第一类：银行「多笔对一笔」是不是汇总入账。只调纯函数或给假任务对象，不连数据库、网络或模型。
"""
import io
import json
import unittest
from datetime import date
from unittest import mock

from openpyxl import load_workbook

from pearnly_cowork.reconciliation import bank_groups, bank_matching, bank_recon_contracts, bank_recon_service
from pearnly_cowork.reconciliation import presets, recon_contracts, revision
from pearnly_cowork.reconciliation.bank_matching import BankReconRow
from pearnly_cowork.reconciliation.export import workbook


def stmt_row(day, deposit=0.0, withdrawal=0.0):
	return BankReconRow(match_status="stmt_deposit_only" if deposit else "stmt_withdrawal_only", match_layer=None,
		stmt_date=date(2026, 3, day), stmt_deposit=deposit, stmt_withdrawal=withdrawal)


def gl_row(day, debit=0.0, credit=0.0, doc="RE"):
	return BankReconRow(match_status="gl_debit_only" if debit else "gl_credit_only", match_layer=None,
		gl_date=date(2026, 3, day), gl_debit=debit, gl_credit=credit, gl_doc_no=doc)


class Scope(unittest.TestCase):
	def test_an_account_is_known_by_bank_tail_and_ledger_account(self):
		self.assertEqual(presets.bank_scope("kbank", "123-4-56789", "1112-01"), "bank|KBANK|6789|1112-01")
		self.assertEqual(presets.bank_scope("KBANK", "", "1112-01"), "bank|KBANK|-|1112-01")
		self.assertEqual(presets.bank_scope("KBANK", "4821", ""), "bank|KBANK|4821|-")

	def test_without_a_tail_or_a_ledger_account_nothing_is_remembered(self):
		# 一个工作区有多家客户公司：只凭银行记会把别家的答案套过来。
		self.assertEqual(presets.bank_scope("KBANK", "", ""), "")
		self.assertEqual(presets.bank_scope("generic", "4821", "1112-01"), "")
		self.assertEqual(presets.bank_scope("", "4821", "1112-01"), "")

	def test_the_most_common_tail_and_ledger_account_win(self):
		self.assertEqual(presets.account_tail(["xxx-x-x4821-x", "4821", "12", "", "9999"]), "4821")
		self.assertEqual(presets.ledger_account(["1112-01", "1112-01", "", "2100"]), "1112-01")

	def test_people_see_a_short_label(self):
		self.assertEqual(presets.scope_label("bank|KBANK|4821|1112-01"), "KBANK ···4821 · 1112-01")
		self.assertEqual(presets.scope_label("bank|KBANK|-|1112-01"), "KBANK · 1112-01")

	def test_only_known_answers_are_accepted(self):
		self.assertTrue(presets.valid("bank", presets.GROUPED, presets.YES))
		self.assertFalse(presets.valid("bank", presets.GROUPED, "maybe"))
		self.assertFalse(presets.valid("income", presets.GROUPED, presets.YES))


class GroupedAsMatched(unittest.TestCase):
	def test_a_unique_group_becomes_one_numbered_match(self):
		results = [stmt_row(20, deposit=600.0), gl_row(19, debit=100.0), gl_row(20, debit=200.0),
			gl_row(20, debit=300.0), gl_row(25, credit=50.0)]
		accepted, count = bank_groups.accept(results)
		self.assertEqual(count, 1)
		group = [row for row in accepted if row.group_no]
		self.assertEqual([row.group_no for row in group], [1, 1, 1, 1])
		self.assertEqual({(row.match_status, row.match_layer) for row in group}, {("matched", 4)})
		self.assertIsNotNone(group[0].stmt_date)  # 锚行（银行那一笔）排在组首
		self.assertEqual(accepted[-1].match_status, "gl_credit_only")

	def test_groups_that_do_not_add_up_uniquely_stay_one_sided(self):
		results = [stmt_row(10, deposit=300.0), gl_row(8, debit=100.0), gl_row(8, debit=200.0),
			gl_row(12, debit=150.0), gl_row(12, debit=150.0)]
		accepted, count = bank_groups.accept(results)
		self.assertEqual(count, 0)
		self.assertFalse(any(row.group_no for row in accepted))

	def test_the_formula_recounts_without_the_grouped_lines(self):
		statement = bank_recon_contracts.decode_rows(
			[{"date": "2026-03-20", "description": "BATCH", "withdrawal": "0", "deposit": "600", "balance": "1600"}],
			[{"date": "2026-03-19", "doc_no": "RE1", "account_code": "1112", "description": "", "debit": "100",
				"credit": "0", "balance": None},
			 {"date": "2026-03-20", "doc_no": "RE2", "account_code": "1112", "description": "", "debit": "500",
				"credit": "0", "balance": None}])
		header = {"stmt_opening": 1000, "stmt_closing": 1600, "gl_opening": 1000, "gl_closing": 1600,
			"bank_code": "KBANK", "gl_account_code": "1112"}
		_plain, before = bank_recon_service._match(statement, header)
		self.assertEqual((before["matched_count"], before["stmt_deposit_only_amount"]), (0, 600.0))
		results, after = bank_recon_service._match(statement, header, {presets.GROUPED: presets.YES})
		self.assertEqual(after["matched_count"], 3)
		self.assertEqual((after["stmt_deposit_only_amount"], after["gl_debit_only_amount"]), (0.0, 0.0))
		self.assertEqual(after["formula_diff"], 0.0)
		_no, declined = bank_recon_service._match(statement, header, {presets.GROUPED: presets.NO})
		self.assertEqual(declined, before)

	def test_the_group_number_is_stored_and_read_back(self):
		row = stmt_row(20, deposit=600.0)
		row.match_status, row.match_layer, row.group_no = "matched", 4, 2
		stored = bank_recon_service.row_values(row)
		self.assertEqual(stored["group_no"], 2)

		class Stored(dict):
			__getattr__ = dict.get
		back = bank_recon_service._export_row(Stored({**stored, "stmt_date": row.stmt_date}))
		self.assertEqual((back.group_no, back.match_layer), (2, 4))


class Workbook(unittest.TestCase):
	def sheets(self, lang="zh"):
		results = [stmt_row(5, deposit=100.0), gl_row(5, debit=100.0), stmt_row(20, deposit=600.0),
			gl_row(19, debit=100.0), gl_row(20, debit=500.0)]
		results = [BankReconRow(**{**vars(results[0]), "match_status": "matched", "match_layer": 1,
			"gl_date": date(2026, 3, 5), "gl_debit": 100.0, "gl_doc_no": "RV"})] + results[2:]
		accepted, _count = bank_groups.accept(results)
		content = workbook(accepted, bank_matching.BankReconSummary(), lang=lang)
		return load_workbook(io.BytesIO(content))

	def test_group_rows_follow_the_plain_matches_with_their_number(self):
		book = self.sheets()
		match = book.worksheets[1]
		layers = [match.cell(row, 2).value for row in range(2, 6)]
		self.assertEqual(layers[0], "L1-精确日期")
		self.assertEqual(layers[1:], ["L4-合计匹配（按预设） #1"] * 3)

	def test_the_ledger_sheet_skips_the_bank_side_of_a_group(self):
		book = self.sheets()
		ledger = book.worksheets[-1]
		dates = [ledger.cell(row, 1).value for row in range(2, ledger.max_row + 1) if ledger.cell(row, 1).value]
		self.assertEqual(len(dates), 3)  # 普通匹配一行 + 组里总账两行，不含银行那一笔


class FakeDoc:
	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


class Questions(unittest.TestCase):
	STATEMENT = [{"date": "2026-03-20", "description": "BATCH", "withdrawal": "0", "deposit": "600", "balance": "1600"}]
	LEDGER = [{"date": "2026-03-19", "doc_no": "RE1", "account_code": "1112-01", "description": "", "debit": "100",
		"credit": "0", "balance": None},
		{"date": "2026-03-20", "doc_no": "RE2", "account_code": "1112-01", "description": "", "debit": "500",
		"credit": "0", "balance": None}]

	def doc(self):
		rows = bank_recon_contracts.decode_rows(self.STATEMENT, self.LEDGER)
		return FakeDoc(name="CBR-9", workspace="W", status="completed", review_json="{}",
			inputs_json=recon_contracts.inputs_json(statement=rows[0], gl=rows[1]),
			parse_info_json=json.dumps({"stmt_files": [{"file": "a.pdf", "ok": True, "account": "4821"}]}),
			stmt_opening=1000, stmt_closing=1600, gl_opening=1000, gl_closing=1600, bank_code="KBANK",
			gl_account_code="")

	def ask(self, stored):
		doc = self.doc()
		calls = []

		def rules(workspace, scope):
			calls.append((workspace, scope))
			return stored
		with mock.patch.object(bank_recon_service, "preset_rules", side_effect=rules), \
				mock.patch.object(revision, "_", side_effect=lambda text: text):
			given = revision.bank_inputs(doc)
			results = revision.bank_compute(doc, given)[2]
			return revision.bank_questions(doc, given, results), results, calls

	def test_an_undecided_account_is_asked_once_with_evidence(self):
		questions, results, calls = self.ask({})
		self.assertEqual(calls[0], ("W", "bank|KBANK|4821|1112-01"))
		self.assertEqual([item["rule"] for item in questions], [presets.GROUPED])
		self.assertEqual([option["value"] for option in questions[0]["options"]], [presets.YES, presets.NO])
		self.assertEqual(len(questions[0]["evidence"]), 1)
		self.assertFalse(any(row.group_no for row in results))

	def test_a_decided_account_is_not_asked_and_yes_matches_the_group(self):
		questions, results, _calls = self.ask({presets.GROUPED: presets.YES})
		self.assertEqual(questions, [])
		self.assertEqual({row.group_no for row in results}, {1})
		with mock.patch.object(revision, "_", side_effect=lambda text: text):
			self.assertEqual(revision.bank_preset_notes(results), ["reconciliation.preset.grouped_applied"])

	def test_no_is_remembered_too(self):
		questions, results, _calls = self.ask({presets.GROUPED: presets.NO})
		self.assertEqual(questions, [])
		self.assertFalse(any(row.group_no for row in results))


if __name__ == "__main__":
	unittest.main()
