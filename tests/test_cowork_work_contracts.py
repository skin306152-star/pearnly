"""Cowork 工作协调看板的纯规则（`work.contracts`）：顺序、字段清洗、提醒 / 重复、清单、自定义字段、链接。"""
import sys
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "pearnly_cowork"))

from pearnly_cowork.work import contracts  # noqa: E402
from pearnly_cowork.work.contracts import Invalid  # noqa: E402


class DefaultsTest(unittest.TestCase):
    def test_default_lists_end_with_the_done_list(self):
        self.assertEqual([key for key, _ in contracts.DEFAULT_LISTS], ["todo", "doing", "done"])
        self.assertEqual([done for _, done in contracts.DEFAULT_LISTS], [False, False, True])


class PositionTest(unittest.TestCase):
    def test_empty_column_and_ends(self):
        self.assertEqual(contracts.position_between(None, None), contracts.STEP)
        self.assertEqual(contracts.position_between(3072.0, None), 3072.0 + contracts.STEP)
        self.assertEqual(contracts.position_between(None, 1024.0), 0.0)

    def test_between_is_midpoint(self):
        self.assertEqual(contracts.position_between(1024.0, 2048.0), 1536.0)

    def test_no_room_asks_for_renumber(self):
        self.assertIsNone(contracts.position_between(1.0, 1.0))
        self.assertIsNone(contracts.position_between(1.0, 1.0 + contracts.MIN_GAP / 2))

    def test_renumber_leaves_room(self):
        values = contracts.renumber(3)
        self.assertEqual(values, [1024.0, 2048.0, 3072.0])
        self.assertIsNotNone(contracts.position_between(values[0], values[1]))


class FieldTest(unittest.TestCase):
    def test_title_collapses_whitespace_and_is_required(self):
        self.assertEqual(contracts.text("  a \n b  ", 10, required=True), "a b")
        with self.assertRaises(Invalid):
            contracts.text("   ", 10, required=True)
        with self.assertRaises(Invalid):
            contracts.text("x" * 11, 10)

    def test_comment_keeps_line_breaks(self):
        self.assertEqual(contracts.comment(" a\r\nb "), "a\nb")
        with self.assertRaises(Invalid):
            contracts.comment("  ")
        with self.assertRaises(Invalid):
            contracts.comment("x" * (contracts.COMMENT_MAX + 1))

    def test_checklists_keep_groups_and_drop_blank_items(self):
        groups = contracts.checklists([{"title": " To do ", "items": [{"title": " a ", "done": True}, {"title": ""}]},
                                       {"title": "", "items": []}])
        self.assertEqual(groups, [{"title": "To do", "items": [{"title": "a", "done": 1}]},
                                  {"title": "", "items": []}])
        self.assertEqual(contracts.checklists(None), [])
        for bad in ("x", [1], [{"items": "x"}], [{"items": [1]}]):
            with self.assertRaises(Invalid):
                contracts.checklists(bad)
        with self.assertRaises(Invalid):
            contracts.checklists([{"items": [{"title": "x"}] * (contracts.CHECKLIST_MAX + 1)}])

    def test_distinct_keeps_order(self):
        self.assertEqual(contracts.distinct(["b", "a", "b"], 5), ["b", "a"])
        with self.assertRaises(Invalid):
            contracts.distinct(["a", ""], 5)
        with self.assertRaises(Invalid):
            contracts.distinct(["a", "b"], 1)

    def test_colors_and_choices(self):
        self.assertEqual(contracts.color("red"), "red")
        with self.assertRaises(Invalid):
            contracts.color("#ff0000")
        self.assertEqual(contracts.choice("1d", contracts.REMINDERS), "1d")
        with self.assertRaises(Invalid):
            contracts.choice("3d", contracts.REMINDERS)

    def test_links_only_http(self):
        self.assertEqual(contracts.link(" https://example.com/a?b=1 "), "https://example.com/a?b=1")
        for bad in ("javascript:alert(1)", "ftp://x", "https://", "notaurl", "http://" + "a" * contracts.URL_MAX):
            with self.assertRaises(Invalid):
                contracts.link(bad)


class CustomFieldTest(unittest.TestCase):
    def test_values_are_normalised_by_type(self):
        self.assertEqual(contracts.field_value("text", [], "  a  b "), "a b")
        self.assertEqual(contracts.field_value("number", [], "1.50"), "1.5")
        self.assertEqual(contracts.field_value("number", [], "100"), "100")
        self.assertEqual(contracts.field_value("date", [], "2026-09-30 00:00:00"), "2026-09-30")
        self.assertEqual(contracts.field_value("checkbox", [], True), "1")
        self.assertEqual(contracts.field_value("checkbox", [], 0), "")
        self.assertEqual(contracts.field_value("select", ["A", "B"], "B"), "B")
        self.assertEqual(contracts.field_value("text", [], ""), "")

    def test_bad_values_rejected(self):
        for fieldtype, options, value in (("number", [], "abc"), ("number", [], "NaN"), ("date", [], "30/09"),
                                          ("select", ["A"], "C"), ("unknown", [], "x")):
            with self.assertRaises(Invalid):
                contracts.field_value(fieldtype, options, value)

    def test_select_needs_options(self):
        self.assertEqual(contracts.field_options("select", [" A ", "A", "", "B"]), ["A", "B"])
        self.assertEqual(contracts.field_options("text", ["ignored"]), [])
        with self.assertRaises(Invalid):
            contracts.field_options("select", [])


class DueTest(unittest.TestCase):
    def test_due_at_defaults_to_end_of_day(self):
        self.assertEqual(contracts.due_at("2026-09-30"), datetime(2026, 9, 30, 23, 59))
        self.assertEqual(contracts.due_at(date(2026, 9, 30), "09:30:00"), datetime(2026, 9, 30, 9, 30))
        self.assertEqual(contracts.due_at("2026-09-30", timedelta(hours=14, minutes=5)), datetime(2026, 9, 30, 14, 5))
        self.assertEqual(contracts.due_at("2026-09-30", time(8, 0)), datetime(2026, 9, 30, 8, 0))

    def test_remind_at(self):
        self.assertEqual(contracts.remind_at("2026-09-30", "10:00", "1h"), datetime(2026, 9, 30, 9, 0))
        self.assertEqual(contracts.remind_at("2026-09-30", "10:00", "at_due"), datetime(2026, 9, 30, 10, 0))
        self.assertIsNone(contracts.remind_at("2026-09-30", "10:00", "none"))
        self.assertIsNone(contracts.remind_at(None, None, "1d"))

    def test_next_due_clamps_month_end(self):
        self.assertEqual(contracts.next_due("2026-01-31", "monthly"), date(2026, 2, 28))
        self.assertEqual(contracts.next_due("2026-12-15", "monthly"), date(2027, 1, 15))
        self.assertEqual(contracts.next_due("2028-02-29", "yearly"), date(2029, 2, 28))
        self.assertEqual(contracts.next_due("2026-09-30", "daily"), date(2026, 10, 1))
        self.assertEqual(contracts.next_due("2026-09-30", "weekly"), date(2026, 10, 7))
        with self.assertRaises(Invalid):
            contracts.next_due("2026-09-30", "never")


if __name__ == "__main__":
    unittest.main()
