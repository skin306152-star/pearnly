"""每个「停下」的出口都要有下一步（``pearnly_core.assistant.stop_exits`` 与闸门 ``scripts/check-stop-exits.py``；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 第 9 节，P0 第 11 个提交）。

断言的是应有状态：
- 归类只有三种：问（写明选项来源与谁执行）、等（写明等什么）、用户去做（写明那一句的术语表键）；不是现成的要写排在哪一期；
- 仓库里每个让流程停下的原因码，要么归了类，要么在基线里（存量，只许减少）；
- 新加一个原因码而没归类，闸门失败；归了类还留在基线里、基线里的码源码里已经没有、归类了一个不存在的码，都失败；
- 业务选择（要人在几个选项里定的）一律是「问」，不是「用户去做」；自己会好的一律是「等」。

不依赖 Frappe，只读源码。

    python3 tests/test_stop_exits.py
"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.assistant import stop_exits as s  # noqa: E402
from pearnly_core.express_operator import contracts as operator  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_stop_exits", ROOT / "scripts/check-stop-exits.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


class ShapeTests(unittest.TestCase):
    def test_the_register_is_well_formed(self):
        self.assertEqual(s.problems(), [])

    def test_each_kind_must_say_what_comes_next(self):
        self.assertEqual(s.problems({"x": {"next": "refuse"}}), ["x: next 'refuse' is not one of ('ask', 'wait', 'act')"])
        self.assertTrue(s.problems({"x": {"next": s.ASK, "source": "somewhere", "by": s.SERVER, "live": True}}))
        self.assertTrue(s.problems({"x": {"next": s.ASK, "source": "review", "by": "model", "live": True}}))
        self.assertTrue(s.problems({"x": {"next": s.WAIT, "live": True}}))
        self.assertTrue(s.problems({"x": {"next": s.ACT, "tell": "do it yourself", "phase": "p2"}}))
        self.assertTrue(s.problems({"x": {"next": s.ASK, "source": "review", "by": s.SERVER}}))
        self.assertEqual(s.problems({"x": s.ask("receipt:choice", s.DESKTOP, "p2"), "y": s.wait("ledger_free"),
                                     "z": s.act("license", "p2")}), [])

    def test_of_returns_a_copy_or_nothing(self):
        found = s.of("menu_choice")
        self.assertEqual(found, {"next": "ask", "source": "receipt:menu_options", "by": "desktop", "live": True})
        found["next"] = "act"
        self.assertEqual(s.of("menu_choice")["next"], "ask")
        self.assertIsNone(s.of("nope"))


class RepositoryTests(unittest.TestCase):
    def test_every_reason_is_classified_or_in_the_baseline(self):
        self.assertEqual(gate.check(gate.harvest(), s.EXITS, gate.read_baseline()), [])

    def test_the_harvest_reads_every_registry(self):
        found = gate.harvest()
        for code in ("unknown_screen", "UNIT_CHOICE", "PERIOD_LOCKED", "express.warehouse_unmapped", "collection.over_collected",
                     "ALREADY_COLLECTED", "undecided_items", "ERR_BILL_NO_TOO_LONG", "ERR_MRERP_MENU_CHANGED"):
            self.assertIn(code, found)
        self.assertEqual(set(operator.REASONS) - set(found), set())

    def test_every_operator_reason_is_classified(self):
        self.assertEqual(sorted(set(operator.REASONS) - set(s.EXITS)), [])

    def test_reasons_that_recover_by_themselves_wait_and_choices_ask(self):
        for code, level in operator.REASONS.items():
            if level != operator.HUMAN:
                self.assertEqual(s.EXITS[code]["next"], s.WAIT, code)
        for code in ("UNIT_CHOICE", "COST_BASIS_CHOICE", "CASH_ACCOUNT_CHOICE", "menu_choice", "company_pick", "choice_needed",
                     "express.account_set_several", "ledger_needed", "already_entered", "last_write_result_unknown"):
            self.assertEqual(s.EXITS[code]["next"], s.ASK, code)
        for code in ("express.no_writer", "express.masters_reading", "collection.busy", "waiting_for_computer"):
            self.assertEqual(s.EXITS[code]["next"], s.WAIT, code)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.found = {"known": "a.py", "old": "a.py"}
        self.exits = {"known": s.wait("ledger_free")}

    def test_a_new_reason_without_a_next_step_fails(self):
        errors = gate.check({**self.found, "brand_new": "b.py"}, self.exits, {"old"})
        self.assertEqual(len(errors), 1)
        self.assertIn("brand_new (b.py): a reason that stops the flow has no next step", errors[0])

    def test_a_classified_reason_must_leave_the_baseline(self):
        errors = gate.check(self.found, {**self.exits, "old": s.act("license", "p2")}, {"old"})
        self.assertEqual(errors, ["old: classified now: remove it from scripts/stop-exits-baseline.json (run --update)"])

    def test_a_baseline_entry_or_a_classified_reason_that_no_longer_exists_fails(self):
        self.assertIn("gone: in the baseline but no longer in the code: run --update",
                      gate.check(self.found, self.exits, {"old", "gone"}))
        self.assertIn("ghost: classified but not found in any reason registry",
                      gate.check(self.found, {**self.exits, "ghost": s.wait("network")}, {"old"}))

    def test_a_malformed_classification_fails(self):
        self.assertTrue(gate.check(self.found, {"known": {"next": s.ASK, "live": True}}, {"old"}))


if __name__ == "__main__":
    unittest.main()
