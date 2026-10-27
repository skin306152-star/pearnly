"""Claude 路线的票面自洽表（``pearnly_core.claude_ocr.consistency``，2026-10-27）：每条会计恒等式的过 / 不过 / 没印不查，
真实漏错样本（``tests/fixtures/claude-ocr/consistency-cases.json``）一定升级，数字能定的字段由数字定。不调接口、不花钱。

改这张表的规矩：先在样本集加真实读法，再改代码；升级策略（``consistency.POLICY``）只按金标回放的数字改
（``scripts/claude-ocr/gate.py --replay``，结果记在 docs/work/2026-10-27-claude-ocr-self-check-评估.md）。"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))
from pearnly_core.claude_ocr import consistency, engine, rules  # noqa: E402

CASES = json.loads((ROOT / "tests/fixtures/claude-ocr/consistency-cases.json").read_text())["cases"]


def line(qty="1", unit_price="100.00", amount="100.00", **kw):
    return {"description": "x", "code": None, "qty": qty, "unit": None, "unit_price": unit_price, "discount": None,
            "discount_percent": None, "amount": amount, **kw}


def doc(lines, subtotal="200.00", vat="14.00", total="214.00", discount=None, price_mode="exclusive"):
    return {"invoice_number": "IV1", "date": "2026-10-01", "subtotal": subtotal, "discount": discount, "vat": vat, "total": total,
            "price_mode": price_mode, "seller_tax": None, "buyer_tax": None, "lines": lines}


def codes(read, policy=consistency.CHECKS):
    return sorted({code for code, _text in consistency.findings(read) if code in policy})


def enter(*docs):
    return {"entry": "enter", "documents": list(docs)}


class Checks(unittest.TestCase):
    def test_clean_exclusive_and_inclusive_documents_pass(self):
        self.assertEqual(codes(enter(doc([line(), line()]))), [])
        inclusive = doc([line(unit_price="107.00", amount="107.00"), line(unit_price="107.00", amount="107.00")],
                        price_mode="inclusive")
        self.assertEqual(codes(enter(inclusive)), [])

    def test_line_discounts_in_every_printed_form(self):
        self.assertEqual(codes(enter(doc([line("2", "60.00", "100.00", discount="20.00"), line()]))), [])     # 行折扣总额
        self.assertEqual(codes(enter(doc([line("2", "60.00", "100.00", discount="10.00"), line()]))), [])     # 每件折扣
        self.assertEqual(codes(enter(doc([line("2", "62.50", "100.00", discount_percent="20"), line()]))), [])  # 百分比

    def test_bill_discount_and_rounding(self):
        lines = [line(amount="100.00"), line(amount="110.00", unit_price="110.00")]
        self.assertEqual(codes(enter(doc(lines, subtotal="200.00", discount="10.00"))), [])  # 各行 210 − 整单折扣 10 = 小计
        self.assertEqual(codes(enter(doc([line(amount="100.30", unit_price="100.30"), line()]))), [])  # 0.5 以内的进位差

    def test_free_items_and_missing_details_are_not_checked(self):
        free = line(qty="3", unit_price=None, amount="0.00")
        self.assertEqual(codes(enter(doc([line(), line(), free]))), [])
        self.assertEqual(codes(enter(doc([]))), [])  # 明细没读出
        partial = doc([line(), line(amount=None)])  # 有一行印了单价却没读出金额：合计不查
        self.assertNotIn("lines_vs_document", codes(enter(partial)))

    def test_wrong_unit_price_and_wrong_line_amount_escalate(self):
        found = codes(enter(doc([line(unit_price="600.00"), line()])))
        self.assertEqual(found, ["line_math"])
        found = codes(enter(doc([line(amount="1268.00", unit_price="1272.00", discount="4.00"), line()])))
        self.assertIn("lines_vs_document", found)

    def test_total_below_a_line(self):
        self.assertIn("total_below_line", codes(enter(doc([line(amount="500.00", unit_price="500.00")], subtotal="100.00",
                                                            vat="7.00", total="107.00"))))

    def test_not_invoices_are_not_checked(self):
        self.assertEqual(consistency.findings({"entry": "not_invoice", "documents": [doc([line(unit_price="9.00")])]}), [])

    def test_hint_names_the_line_and_the_numbers(self):
        text = consistency.problems(enter(doc([line(unit_price="600.00"), line()])))[0]
        self.assertIn("lines[0]", text)
        self.assertIn("600.00", text)
        self.assertIn("100.00", text)


class Settle(unittest.TestCase):
    def test_price_mode_follows_the_numbers(self):
        read = enter(doc([line(unit_price="107.00", amount="107.00"), line(unit_price="107.00", amount="107.00")],
                         price_mode="exclusive"))
        self.assertEqual(consistency.settle(read), ["documents[0].price_mode exclusive -> inclusive"])
        self.assertEqual(read["documents"][0]["price_mode"], "inclusive")

    def test_ambiguous_numbers_leave_price_mode_alone(self):
        zero_vat = enter(doc([line(), line()], subtotal="200.00", vat="0.00", total="200.00", price_mode=None))
        self.assertEqual(consistency.settle(zero_vat), [])
        self.assertIsNone(zero_vat["documents"][0]["price_mode"])


class RealCases(unittest.TestCase):
    def test_every_recorded_miss_escalates_and_the_rereading_settles(self):
        for case in CASES:
            with self.subTest(case["id"]):
                haiku, opus = copy.deepcopy(case["haiku"]), copy.deepcopy(case["opus"])
                self.assertEqual(rules._voucher_basics(haiku, 1), [], "这条样本当时没被旧自检拦住")
                fired = codes(haiku, consistency.POLICY)
                for code in case["expect"]["haiku_escalates"]:
                    self.assertIn(code, fired)
                self.assertTrue(rules.voucher_problems(haiku, 1))
                if case["expect"].get("opus_passes"):
                    self.assertEqual(rules.voucher_problems(opus, 1), [])
                settled = engine.finish(opus, 1, escalated=True)
                self.assertEqual(settled["why_after"], [])
                if case["expect"].get("settled_price_mode"):
                    self.assertEqual(opus["documents"][0]["price_mode"], case["expect"]["settled_price_mode"])


class Combine(unittest.TestCase):
    """升级后按字段合并（回放基准 2026-10-27：整份换成 Opus 的，金标 6701056 / 6800310 的单号被 Opus 改错）。"""

    def test_unflagged_fields_keep_the_first_reading(self):
        haiku = enter(dict(doc([line(unit_price="600.00"), line()]), invoice_number="0000030621", seller_tax=None))
        opus = enter(dict(doc([line(), line()]), invoice_number="000030621", seller_tax="0105546015062"))
        why = rules.voucher_problems(haiku, 1)
        merged = engine.combine(haiku, opus, why)["documents"][0]
        self.assertEqual(merged["invoice_number"], "0000030621")  # 没被怀疑：留 Haiku 的
        self.assertEqual(merged["seller_tax"], "0105546015062")  # Haiku 没读出：用 Opus 的
        self.assertEqual(merged["lines"][0]["unit_price"], "100.00")  # 被点名的金额一组：用 Opus 的

    def test_named_field_takes_the_rereading(self):
        haiku = enter(dict(doc([]), seller_tax="0105546015063"))
        opus = enter(dict(doc([]), seller_tax="0105546015062"))
        merged = engine.combine(haiku, opus, rules.voucher_problems(haiku, 1))
        self.assertEqual(merged["documents"][0]["seller_tax"], "0105546015062")

    def test_several_invoices_take_the_whole_rereading(self):
        haiku = enter(doc([line(unit_price="600.00"), line()]), doc([]))
        opus = enter(dict(doc([]), invoice_number="B"), dict(doc([line(), line()]), invoice_number="A"))
        self.assertIs(engine.combine(haiku, opus, rules.voucher_problems(haiku, 1)), opus)

    def test_a_changed_entry_takes_the_whole_rereading(self):
        haiku = enter(doc([line(unit_price="600.00"), line()]))
        opus = {"entry": "not_invoice", "documents": []}
        self.assertIs(engine.combine(haiku, opus, rules.voucher_problems(haiku, 1)), opus)


class EngineFlow(unittest.TestCase):
    def test_line_errors_reach_opus_with_the_line_in_the_hint(self):
        case = CASES[0]

        class Fake:
            def __init__(self):
                self.calls, self.replies = [], [case["haiku"], case["opus"]]
                self.messages = self

            def create(self, **kwargs):
                from types import SimpleNamespace
                self.calls.append(kwargs)
                body = json.dumps(self.replies.pop(0))
                usage = SimpleNamespace(input_tokens=10, output_tokens=10, cache_creation_input_tokens=0, cache_read_input_tokens=0)
                return SimpleNamespace(stop_reason="end_turn", usage=usage, content=[SimpleNamespace(type="text", text=body)])

        fake = Fake()
        out = engine.run(fake, "voucher", [("image/jpeg", b"x")])
        self.assertTrue(out["escalated"])
        self.assertIn("lines[6]", fake.calls[1]["messages"][0]["content"][-1]["text"])
        self.assertEqual(out["why_after"], [])
        self.assertEqual(out["read"]["documents"][0]["price_mode"], "inclusive")
        self.assertTrue(out["settled"])


if __name__ == "__main__":
    unittest.main()
