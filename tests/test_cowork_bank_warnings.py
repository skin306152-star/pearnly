"""Offline regressions for the migrated bank warnings and anchor overrides."""

import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from pearnly_cowork.reconciliation import bank_recon_contracts as contracts
from pearnly_cowork.reconciliation import bank_warnings as warnings

BASE = date(2026, 6, 1)


def side(days):
	return [SimpleNamespace(date=BASE + timedelta(days=day) if day is not None else None)
		for day in days]


class WarningTable(unittest.TestCase):
	def test_every_warning_covers_four_languages(self):
		self.assertEqual(set(warnings._BRV2_WARN), {"period_mismatch", "gl_too_few", "no_match",
			"completeness", "multi_account"})
		for key, table in warnings._BRV2_WARN.items():
			self.assertEqual(set(table), {"zh", "en", "th", "ja"}, key)
			for text in table.values():
				self.assertTrue(text.strip(), key)

	def test_an_unknown_language_or_key_falls_back_the_deployed_way(self):
		self.assertEqual(warnings.brv2_warn("no_match", "bogus"),
			warnings._BRV2_WARN["no_match"]["th"])
		self.assertEqual(warnings.brv2_warn("no_match", ""),
			warnings._BRV2_WARN["no_match"]["th"])
		self.assertEqual(warnings.brv2_warn("missing", "en"), "missing")
		self.assertEqual(warnings.brv2_warn("gl_too_few", "en", n=3, m=237),
			warnings._BRV2_WARN["gl_too_few"]["en"].format(n=3, m=237))


class CompletenessDetails(unittest.TestCase):
	def test_each_issue_type_reads_as_a_short_snippet(self):
		lines = warnings.completeness_details([
			{"type": "credit_count_mismatch", "printed": 5, "count": 3},
			{"type": "debit_count_mismatch", "printed": 9, "count": 9},
			{"type": "credit_sum_mismatch", "printed": 12345.6, "sum": 12000.0},
			{"type": "debit_sum_mismatch", "printed": -500.0, "sum": 0.0},
			{"type": "closing_mismatch", "printed": 560.0, "calc": 510.0},
		], "en")
		self.assertEqual(lines, [
			"creditscount(stmt prints5/extracted3)", "debitscount(stmt prints9/extracted9)",
			"creditssum(stmt prints12,346/extracted12,000)",
			"debitssum(stmt prints-500/extracted0)",
			"closing(stmt prints560/calculated510)"])

	def test_a_large_count_gap_adds_the_missing_page_hint(self):
		lines = warnings.completeness_details([{"type": "credit_count_mismatch", "printed": 40,
			"count": 3}], "en")
		self.assertEqual(lines[-1], warnings._MISSPAGE_LBL["en"])
		self.assertEqual(len(lines), 2)
		# The hint needs both twice as many printed and a gap of twenty.
		self.assertEqual(len(warnings.completeness_details([
			{"type": "credit_count_mismatch", "printed": 30, "count": 10}], "en")), 2)
		self.assertEqual(len(warnings.completeness_details([
			{"type": "credit_count_mismatch", "printed": 20, "count": 10}], "en")), 1)
		self.assertEqual(len(warnings.completeness_details([
			{"type": "debit_count_mismatch", "printed": 19, "count": 10}], "en")), 1)

	def test_a_balance_break_reports_the_row_count_in_each_language(self):
		for lang, expected in [("en", "4 row(s) fail"), ("zh", "4 行余额对不上"),
				("th", "4 แถวยอดคงเหลือ"), ("ja", "4 行で残高")]:
			with self.subTest(lang=lang):
				self.assertIn(expected, warnings.completeness_details(
					[{"type": "balance_break", "count": 4}], lang)[0])
		self.assertIn("0 row(s)", warnings.completeness_details(
			[{"type": "balance_break", "count": 0}], "en")[0])

	def test_an_unknown_issue_and_an_empty_list_produce_nothing(self):
		self.assertEqual(warnings.completeness_details([{"type": "mystery"}, {}], "th"), [])
		self.assertEqual(warnings.completeness_details(None, "th"), [])
		self.assertEqual(warnings.completeness_details([], "th"), [])


class MismatchDetection(unittest.TestCase):
	def test_a_period_that_does_not_overlap_is_reported(self):
		statement, gl = side([0, 1]), side([90, 120])
		lines = warnings.detect_recon_mismatch(statement, gl, 1, "en")
		self.assertEqual(len(lines), 1)
		self.assertEqual(lines[0], warnings.brv2_warn("period_mismatch", "en",
			g=f"{gl[0].date}~{gl[1].date}", s=f"{statement[0].date}~{statement[1].date}"))
		self.assertIn("periods do not overlap", lines[0])

	def test_a_scale_gap_needs_twenty_statement_rows_and_a_fifth_of_them(self):
		too_short = warnings.detect_recon_mismatch(side([0] * 19), side([0] * 3), 1, "en")
		self.assertNotIn(warnings.brv2_warn("gl_too_few", "en", n=3, m=19), too_short)
		exact_fifth = warnings.detect_recon_mismatch(side([0] * 20), side([0] * 4), 1, "en")
		self.assertNotIn(warnings.brv2_warn("gl_too_few", "en", n=4, m=20), exact_fifth)
		gap = warnings.detect_recon_mismatch(side([0] * 20), side([0] * 3), 1, "en")
		self.assertIn(warnings.brv2_warn("gl_too_few", "en", n=3, m=20), gap)

	def test_no_match_needs_a_zero_count_and_ten_rows_together(self):
		self.assertNotIn(warnings.brv2_warn("no_match", "th"),
			warnings.detect_recon_mismatch(side([0] * 5), side([0] * 4), 0, "th"))
		self.assertIn(warnings.brv2_warn("no_match", "th"),
			warnings.detect_recon_mismatch(side([0] * 5), side([0] * 5), 0, "th"))
		self.assertEqual(warnings.detect_recon_mismatch(side([0] * 9), side([0] * 9), 1, "th"), [])

	def test_a_task_waiting_for_review_is_not_told_nothing_matched(self):
		# 需复核的任务建单时还没匹配（匹配数传 None）：不写「没有任何记录匹配成功」（2026-10-02 重放：B08-05 能匹配 44 笔）。
		self.assertNotIn(warnings.brv2_warn("no_match", "th"),
			warnings.detect_recon_mismatch(side([0] * 5), side([0] * 5), None, "th"))
		from pathlib import Path

		source = (Path(__file__).resolve().parents[1] /
			"apps/pearnly_cowork/pearnly_cowork/reconciliation/bank_recon_service.py").read_text()
		self.assertIn('run_info(rows, None if _review is not None else summary_value.get("matched_count", 0)', source)

	def test_a_side_with_no_dates_still_answers(self):
		self.assertEqual(warnings.detect_recon_mismatch(side([None, None]), side([None]), 0, "th"), [])
		self.assertEqual(warnings.detect_recon_mismatch([], [], 0, "th"), [])
		# Printed dates compare as text, so a text period gap is reported the same way.
		lines = warnings.detect_recon_mismatch([SimpleNamespace(date="2026-06-01")],
			[SimpleNamespace(date="2026-07-01")], 0, "en")
		self.assertEqual(lines, [warnings.brv2_warn("period_mismatch", "en",
			g="2026-07-01~2026-07-01", s="2026-06-01~2026-06-01")])

	def test_a_mixed_pair_leaves_the_warning_list_empty(self):
		lines = warnings.detect_recon_mismatch([SimpleNamespace(date=BASE)],
			[SimpleNamespace(date="2026-07-01")], 0, "en")
		self.assertEqual(lines, [])


class AnchorOverrides(unittest.TestCase):
	def test_the_snapshot_always_comes_back_and_no_override_leaves_it_alone(self):
		result = warnings.apply_anchor_overrides(1000.0, 990.0, 500.0, 510.0, None, None, None)
		self.assertEqual(result[:4], (1000.0, 990.0, 500.0, 510.0))
		self.assertEqual(result[4], {"stmt_opening": 1000.0, "gl_opening": 990.0,
			"gl_closing": 500.0, "stmt_closing": 510.0})
		self.assertEqual(result[5], {})

	def test_each_override_replaces_its_amount_and_keeps_the_reader_value(self):
		result = warnings.apply_anchor_overrides(1000.0, 990.0, 500.0, 510.0, 950.0, 985.0, 498.0,
			555.0)
		self.assertEqual(result[:4], (950.0, 985.0, 498.0, 555.0))
		self.assertEqual(result[5], {
			"stmt_opening": {"ocr": 1000.0, "user": 950.0},
			"gl_opening": {"ocr": 990.0, "user": 985.0},
			"gl_closing": {"ocr": 500.0, "user": 498.0},
			"stmt_closing": {"ocr": 510.0, "user": 555.0}})
		self.assertEqual(result[4], {"stmt_opening": 1000.0, "gl_opening": 990.0,
			"gl_closing": 500.0, "stmt_closing": 510.0})

	def test_a_zero_override_is_an_override_not_an_absence(self):
		result = warnings.apply_anchor_overrides(1000.0, 990.0, 500.0, 510.0, 0.0, None, None, None)
		self.assertEqual(result[0], 0.0)
		self.assertEqual(result[5], {"stmt_opening": {"ocr": 1000.0, "user": 0.0}})


class RunInfoContract(unittest.TestCase):
	def test_the_deployed_three_keys_round_trip(self):
		value = contracts.run_info({
			"warnings": ["⚠️ one", "⚠️ two"],
			"anchor_ocr": {"stmt_opening": "1,000.00", "gl_opening": None},
			"anchor_overrides": {"gl_closing": {"ocr": 500, "user": "498.5"}},
		})
		self.assertEqual(value["warnings"], ["⚠️ one", "⚠️ two"])
		self.assertEqual(value["anchor_ocr"], {"stmt_opening": 1000.0, "gl_opening": None})
		self.assertEqual(value["anchor_overrides"], {"gl_closing": {"ocr": 500.0, "user": 498.5}})

	def test_an_empty_value_stores_nothing(self):
		self.assertEqual(contracts.run_info(None), {})
		self.assertEqual(contracts.run_info(""), {})
		self.assertEqual(contracts.run_info({"warnings": []}), {})

	def test_an_unknown_key_shape_or_amount_is_refused(self):
		cases = [
			({"surprise": 1}, "reconciliation.run_info_invalid"),
			({"warnings": "one"}, "reconciliation.run_info_invalid:warnings"),
			({"warnings": [1]}, "reconciliation.run_info_invalid:warnings"),
			({"anchor_ocr": {"stmt_opening": "x"}},
				"reconciliation.run_info_invalid:anchor_ocr.stmt_opening"),
			({"anchor_ocr": {"mystery": 1}}, "reconciliation.run_info_invalid:anchor_ocr"),
			({"anchor_overrides": {"gl_opening": {"ocr": 1}}},
				"reconciliation.run_info_invalid:anchor_overrides.gl_opening"),
			({"anchor_overrides": {"gl_opening": {"user": "x"}}},
				"reconciliation.run_info_invalid:anchor_overrides.gl_opening"),
			({"anchor_overrides": {"mystery": {"user": 1}}},
				"reconciliation.run_info_invalid:anchor_overrides"),
		]
		for payload, expected in cases:
			with self.subTest(payload=payload):
				with self.assertRaises(contracts.ReconciliationContractError) as caught:
					contracts.run_info(payload)
				self.assertEqual(str(caught.exception), expected)

	def test_too_many_or_too_long_warnings_are_folded_not_refused(self):
		# 2026-09-30 有意变化：提示只是说明，超出上限收拢（末尾「…(+N)」），不让整份对账失败（压测 rK I01）。
		folded = contracts.run_info({"warnings": ["x"] * (contracts.MAX_WARNINGS + 1)})["warnings"]
		self.assertEqual(len(folded), contracts.MAX_WARNINGS)
		self.assertEqual(folded[-1], "…(+2)")
		long = contracts.run_info({"warnings": ["x" * (contracts.MAX_WARNING_TEXT + 1)]})["warnings"][0]
		self.assertLessEqual(len(long), contracts.MAX_WARNING_TEXT)
		self.assertTrue(long.endswith("…(+1)"))


if __name__ == "__main__":
	unittest.main()
