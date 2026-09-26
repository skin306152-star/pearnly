"""Cowork 工作协调看板的纯规则（`work.contracts`）：状态、顺序、字段清洗。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "pearnly_cowork"))

from pearnly_cowork.work import contracts  # noqa: E402
from pearnly_cowork.work.contracts import Invalid  # noqa: E402


class StatusTest(unittest.TestCase):
    def test_three_fixed_columns(self):
        self.assertEqual(contracts.STATUSES, ("todo", "doing", "done"))
        for status in contracts.STATUSES:
            self.assertEqual(contracts.status(status), status)

    def test_unknown_status_rejected(self):
        for value in ("review", "", None, "Done"):
            with self.assertRaises(Invalid):
                contracts.status(value)


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

    def test_checklist_drops_blank_rows_and_normalises_done(self):
        rows = contracts.checklist([{"title": " a ", "done": True}, {"title": ""}, {"title": "b", "done": "0"}])
        self.assertEqual(rows, [{"title": "a", "done": 1}, {"title": "b", "done": 0}])
        self.assertEqual(contracts.checklist(None), [])
        with self.assertRaises(Invalid):
            contracts.checklist("not a list")
        with self.assertRaises(Invalid):
            contracts.checklist([{"title": "x"}] * (contracts.CHECKLIST_MAX + 1))

    def test_distinct_keeps_order(self):
        self.assertEqual(contracts.distinct(["b", "a", "b"], 5), ["b", "a"])
        with self.assertRaises(Invalid):
            contracts.distinct(["a", ""], 5)
        with self.assertRaises(Invalid):
            contracts.distinct(["a", "b"], 1)

    def test_label_colors_are_native_indicators(self):
        self.assertEqual(contracts.color("red"), "red")
        with self.assertRaises(Invalid):
            contracts.color("#ff0000")


if __name__ == "__main__":
    unittest.main()
