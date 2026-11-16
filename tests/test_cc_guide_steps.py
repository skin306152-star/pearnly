"""CC 引导：按表单出题的步骤引擎（``pearnly_core.assistant.guide.steps``，纯函数；总设计 2.3）。

断言的是应有状态（用收款那份表单声明走一遍）：
- 没依赖的步骤先问；依赖前一步的（选了客户才列它的发票）等前一步有值才问；``when`` 不成立的不问也不要求有值
  （没选转账就不要凭证）；
- 后台定了值的进「已定」、不问，用户可以改；``always`` 的步骤后台给了值也照问（钱和对象的事每次明选）；``auto`` 给不出值降成问；
- 改了前面一步，依赖它的后面各步作废；
- 适用的步骤都有值才算问完；交给业务函数的值不含不适用的步骤；
- 值按输入方式核形状：金额必须是正数、同一个码不能出现两次、月份是 YYYY-MM、文件编号不带路径。

    python3 tests/test_cc_guide_steps.py
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.assistant.guide import declaration as d, steps  # noqa: E402

GUIDE = {"id": "collection", "label_key": "guide.business.collection", "icon": "wallet", "order": 10, "modes": ["chat"],
         "ledgers": ["express_local"], "opening_key": "guide.open.collection", "source": "form", "writes": True,
         "steps": [{"key": "customer", "input": "choice", "rule": "always", "search": True, "options": "app.mod.customers"},
                   {"key": "invoices", "input": "amounts", "rule": "always", "after": ["customer"], "options": "app.mod.invoices"},
                   {"key": "method", "input": "split", "rule": "always", "after": ["invoices"], "options": "app.mod.methods"},
                   {"key": "voucher", "input": "file", "rule": "always", "when": {"key": "method", "has": "transfer"}},
                   {"key": "on_date", "input": "date", "rule": "auto", "default": "app.mod.today"},
                   {"key": "staff", "input": "choice", "rule": "auto", "after": ["invoices"], "options": "app.mod.staff",
                    "default": "app.mod.staff_default"},
                   {"key": "preset", "input": "choice", "rule": "remember", "remember": "company", "options": "app.mod.presets"}],
         "review": "app.mod.review", "confirm": "app.mod.release"}
TODAY = {"on_date": {"value": "2026-11-14", "source": "today"}}
INVOICES = [{"code": "IV001", "amount": "1000.00"}, {"code": "IV002", "amount": "250.50"}]
CASH = [{"code": "cash", "amount": "1250.50"}]
SPLIT = [{"code": "cash", "amount": "250.50"}, {"code": "transfer", "amount": "1000"}]


def keys(found):
    return [item["key"] for item in found]


class OrderTests(unittest.TestCase):
    def test_the_declaration_used_here_is_well_formed(self):
        self.assertEqual(d.problems(GUIDE), [])

    def test_steps_without_dependencies_are_asked_first_and_together(self):
        self.assertEqual(keys(steps.to_ask(GUIDE, {}, TODAY)), ["customer", "preset"])
        self.assertEqual(steps.blocked(GUIDE, {}, TODAY), ["invoices", "method", "voucher", "staff"])
        self.assertEqual(steps.state(GUIDE, {}, TODAY), steps.ASKING)

    def test_a_step_waits_for_the_step_it_depends_on(self):
        answers = {"customer": "C001", "preset": "p1"}
        self.assertEqual(keys(steps.to_ask(GUIDE, answers, TODAY)), ["invoices"])
        answers["invoices"] = INVOICES
        self.assertEqual(keys(steps.to_ask(GUIDE, answers, TODAY)), ["method", "staff"])

    def test_a_step_whose_condition_does_not_hold_is_neither_asked_nor_required(self):
        answers = {"customer": "C001", "preset": "p1", "invoices": INVOICES, "method": CASH}
        decided = {**TODAY, "staff": {"value": "S1", "source": "invoice"}}
        self.assertEqual(steps.to_ask(GUIDE, answers, decided), [])
        self.assertEqual(steps.state(GUIDE, answers, decided), steps.READY)
        self.assertNotIn("voucher", steps.final_values(GUIDE, answers, decided))
        answers["method"] = SPLIT
        self.assertEqual(keys(steps.to_ask(GUIDE, answers, decided)), ["voucher"])
        self.assertEqual(steps.state(GUIDE, answers, decided), steps.ASKING)

    def test_a_condition_can_test_one_value_or_any_of_several(self):
        one = {"key": "x", "when": {"key": "kind", "value": "bank"}}
        many = {"key": "x", "when": {"key": "kind", "in": ["bank", "income"]}}
        self.assertTrue(steps.applies(one, {"kind": "bank"}))
        self.assertFalse(steps.applies(one, {"kind": "vat"}))
        self.assertFalse(steps.applies(one, {}))
        self.assertTrue(steps.applies(many, {"kind": "income"}))
        self.assertFalse(steps.applies(many, {"kind": "vat"}))


class DecidedTests(unittest.TestCase):
    def test_what_the_server_decided_is_not_asked_and_is_listed_as_decided(self):
        answers = {"customer": "C001", "invoices": INVOICES}
        decided = {**TODAY, "staff": {"value": "S1", "value_label": "S1 Somchai", "source": "invoice"},
                   "preset": {"value": "p1", "source": "remembered"}}
        self.assertEqual(keys(steps.to_ask(GUIDE, answers, decided)), ["method"])
        listed = steps.settled(GUIDE, answers, decided)
        self.assertEqual(keys(listed), ["on_date", "staff", "preset"])
        self.assertEqual(listed[1], {"key": "staff", "input": "choice", "value": "S1", "value_label": "S1 Somchai", "source": "invoice"})

    def test_a_decided_step_the_user_changed_is_an_answer_not_a_decided_item(self):
        answers = {"customer": "C001", "invoices": INVOICES, "method": CASH, "staff": "S2"}
        decided = {**TODAY, "staff": {"value": "S1", "source": "invoice"}, "preset": {"value": "p1", "source": "remembered"}}
        self.assertEqual(keys(steps.settled(GUIDE, answers, decided)), ["on_date", "preset"])
        self.assertEqual(steps.final_values(GUIDE, answers, decided)["staff"], "S2")

    def test_an_always_step_is_asked_even_when_the_server_has_a_value(self):
        decided = {**TODAY, "customer": {"value": "C001", "source": "history"}, "preset": {"value": "p1", "source": "remembered"}}
        self.assertEqual(keys(steps.to_ask(GUIDE, {}, decided)), ["customer"])
        self.assertNotIn("customer", keys(steps.settled(GUIDE, {}, decided)))

    def test_an_auto_step_without_a_value_falls_back_to_a_question(self):
        answers = {"customer": "C001", "preset": "p1", "invoices": INVOICES, "method": CASH}
        self.assertEqual(keys(steps.to_ask(GUIDE, answers, {})), ["on_date", "staff"])


class ChangeTests(unittest.TestCase):
    def test_changing_a_step_voids_the_answers_that_depended_on_it(self):
        answers = {"customer": "C001", "preset": "p1", "invoices": INVOICES, "method": SPLIT, "voucher": ["upl_1"], "staff": "S2"}
        self.assertEqual(steps.dependents(GUIDE, "customer"), ["invoices", "method", "voucher", "staff"])
        self.assertEqual(steps.without_dependents(GUIDE, answers, ["customer"]), {"customer": "C001", "preset": "p1"})
        self.assertEqual(steps.without_dependents(GUIDE, answers, ["method"]),
                         {key: answers[key] for key in ("customer", "preset", "invoices", "method", "staff")})
        self.assertEqual(steps.without_dependents(GUIDE, answers, ["preset"]), answers)

    def test_only_steps_that_can_be_asked_decided_or_were_answered_take_an_answer(self):
        self.assertEqual(steps.problems_in(GUIDE, {}, TODAY, {"customer": "C001", "invoices": INVOICES}),
                         {"invoices": "not asked now"})
        self.assertEqual(steps.problems_in(GUIDE, {}, TODAY, {"on_date": "2026-11-13"}), {})
        self.assertEqual(steps.problems_in(GUIDE, {"customer": "C001"}, TODAY, {"customer": "C002"}), {})
        self.assertEqual(steps.problems_in(GUIDE, {}, TODAY, {"nope": "x"}), {"nope": "not asked now"})


class ValueTests(unittest.TestCase):
    def step(self, kind):
        return {"key": "s", "input": kind}

    def test_amount_rows_need_a_code_and_a_positive_amount_once_each(self):
        ok = steps.value_problem(self.step("amounts"), INVOICES)
        self.assertEqual(ok, "")
        for bad in ([], [{"code": "IV001", "amount": "0"}], [{"code": "IV001", "amount": "-5"}], [{"code": "", "amount": "5"}],
                    [{"code": "IV001", "amount": "abc"}], [{"code": "IV001", "amount": "NaN"}], ["IV001"],
                    [{"code": "IV001", "amount": "1"}, {"code": "IV001", "amount": "2"}]):
            self.assertNotEqual(steps.value_problem(self.step("amounts"), bad), "", bad)
        self.assertEqual(steps.value_problem(self.step("split"), SPLIT), "")

    def test_choice_multi_and_file_values(self):
        self.assertEqual(steps.value_problem(self.step("choice"), "C001"), "")
        self.assertNotEqual(steps.value_problem(self.step("choice"), ["C001"]), "")
        self.assertEqual(steps.value_problem(self.step("multi"), ["a", "b"]), "")
        self.assertNotEqual(steps.value_problem(self.step("multi"), ["a", "a"]), "")
        self.assertEqual(steps.value_problem(self.step("file"), ["upl_abc-1"]), "")
        self.assertNotEqual(steps.value_problem(self.step("file"), ["../etc/passwd"]), "")
        self.assertNotEqual(steps.value_problem(self.step("file"), []), "")

    def test_month_date_number_and_text_values(self):
        self.assertEqual(steps.value_problem(self.step("month"), "2026-11"), "")
        self.assertNotEqual(steps.value_problem(self.step("month"), "2026-13"), "")
        self.assertEqual(steps.value_problem(self.step("date"), "2026-11-14"), "")
        self.assertNotEqual(steps.value_problem(self.step("date"), "14/11/2026"), "")
        self.assertEqual(steps.value_problem(self.step("number"), "12.50"), "")
        self.assertNotEqual(steps.value_problem(self.step("number"), "twelve"), "")
        self.assertNotEqual(steps.value_problem(self.step("number"), True), "")
        self.assertEqual(steps.value_problem(self.step("text"), "ABC Co., Ltd."), "")
        self.assertNotEqual(steps.value_problem(self.step("text"), "   "), "")
        self.assertNotEqual(steps.value_problem(self.step("text"), "x" * (steps.TEXT_MAX + 1)), "")

    def test_time_counted_rows_and_question_answers(self):
        """N8 时刻、N4 盘点数到 0 的行、N1a 子题的答案（约定 11-16）。"""
        self.assertEqual(steps.value_problem(self.step("time"), "08:30"), "")
        for bad in ("8:30", "24:00", "08:60", "0830"):
            self.assertNotEqual(steps.value_problem(self.step("time"), bad), "", bad)
        counted = {"key": "rows", "input": "amounts", "zero": True}
        self.assertEqual(steps.value_problem(counted, [{"code": "P1", "amount": "0"}, {"code": "P2", "amount": "3"}]), "")
        self.assertNotEqual(steps.value_problem(counted, [{"code": "P1", "amount": "-1"}]), "")
        self.assertNotEqual(steps.value_problem(self.step("amounts"), [{"code": "P1", "amount": "0"}]), "")   # 没声明 zero 照旧
        self.assertNotEqual(steps.value_problem({"key": "m", "input": "split", "zero": True}, [{"code": "c", "amount": "0"}]), "")
        self.assertEqual(steps.value_problem(self.step("questions"), {"col.date": "C2", "gl_account": "1100"}), "")
        for bad in ({}, {"a": ""}, {"a b": "x"}, ["x"], {f"k{i}": "x" for i in range(steps.QUESTIONS_MAX + 1)}):
            self.assertNotEqual(steps.value_problem(self.step("questions"), bad), "", bad)

    def test_every_declared_input_has_a_value_rule(self):
        for kind in d.INPUTS:
            self.assertNotEqual(steps.value_problem(self.step(kind), None), "", kind)


OPTIONAL = {"id": "readback", "label_key": "guide.business.readback", "icon": "download", "order": 40, "modes": ["chat"],
            "ledgers": ["express_local"], "opening_key": "guide.open.readback", "source": "form", "writes": False,
            "steps": [{"key": "period", "input": "month", "rule": "always"},
                      {"key": "accounts", "input": "multi", "rule": "always", "required": False, "after": ["period"],
                       "options": "app.mod.accounts"},
                      {"key": "note", "input": "text", "rule": "always", "after": ["accounts"]},
                      {"key": "only", "input": "choice", "rule": "always", "when": {"key": "accounts", "has": "1100"},
                       "options": "app.mod.only"}],
            "confirm": "app.mod.request"}


class OptionalTests(unittest.TestCase):
    """可不答的步骤（``required: False``）：11-16 前不答会一直停在「问」，整次引导确定不了。"""

    def test_the_declaration_used_here_is_well_formed(self):
        self.assertEqual(d.problems(OPTIONAL), [])

    def test_an_unanswered_optional_step_does_not_keep_the_guide_asking(self):
        answers = {"period": "2026-10", "note": "x"}
        self.assertIn("accounts", keys(steps.to_ask(OPTIONAL, answers, {})))   # 仍在卡上，可以答
        self.assertEqual(steps.blocked(OPTIONAL, answers, {}), [])
        self.assertEqual(steps.state(OPTIONAL, answers, {}), steps.READY)
        self.assertEqual(steps.final_values(OPTIONAL, answers, {}), {"period": "2026-10", "note": "x"})

    def test_a_step_after_an_optional_step_is_asked_on_the_same_card(self):
        self.assertEqual(keys(steps.to_ask(OPTIONAL, {"period": "2026-10"}, {})), ["accounts", "note"])
        self.assertEqual(steps.state(OPTIONAL, {"period": "2026-10"}, {}), steps.ASKING)   # 要答的 note 还没答

    def test_a_step_conditioned_on_an_unanswered_optional_step_does_not_apply(self):
        answers = {"period": "2026-10", "note": "x"}
        self.assertNotIn("only", keys(steps.to_ask(OPTIONAL, answers, {})))
        self.assertNotIn("only", steps.blocked(OPTIONAL, answers, {}))

    def test_answering_the_optional_step_brings_its_condition_back(self):
        answers = {"period": "2026-10", "note": "x", "accounts": ["1100"]}
        self.assertIn("only", keys(steps.to_ask(OPTIONAL, answers, {})))
        self.assertEqual(steps.state(OPTIONAL, answers, {}), steps.ASKING)
        self.assertEqual(steps.state(OPTIONAL, {**answers, "only": "a"}, {}), steps.READY)

    def test_changing_the_optional_step_still_voids_what_depended_on_it(self):
        kept = steps.without_dependents(OPTIONAL, {"period": "2026-10", "accounts": ["1100"], "note": "x", "only": "a"},
                                        ["accounts"])
        self.assertEqual(kept, {"period": "2026-10", "accounts": ["1100"]})

    def test_a_required_step_is_unchanged(self):
        self.assertEqual(steps.state(GUIDE, {"customer": "C1"}, TODAY), steps.ASKING)


if __name__ == "__main__":
    unittest.main()
