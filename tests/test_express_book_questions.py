"""开始前四题（A3 / P2-2，``express_rules.push.book_questions``）：单位、负库存、出库成本、现金科目。纯离线，不需要 Frappe。

每题都走：没定 → 出题；答了 → 载荷带上 CC 桌面版认的键；记住的 → 第二次不问；只有一个合格选项 → 不问、自动定；资料不全 → 不猜、不出题。
代录不出成本与现金科目两题。与 CC 桌面版同一判法的真跨边比对在 ``companion/tests/test_seam_book_questions.py``。
"""
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_rules.push import book_questions as B  # noqa: E402
from pearnly_core.express_rules.push import common  # noqa: E402

CASH, BANK, PETTY = "11-01-01-00", "11-01-02-00", "11-01-03-00"


def masters(**over):
    """ERP 主档镜像（``express_masters`` 结果）的最小样子：可乐（库存单位 ขด，采购单位 ลง×12），冰块（只有 ถง），账套允许负库存。"""
    data = {
        "settings": {"mainloc": "01", "negallow": "Y", "negauth": "", "chkneglev": "2", "cosmtd": "F"},
        "products": [
            {"code": "P001", "name": "โค้ก", "kind": "stock", "unit": "ขด", "sale_unit": "", "sale_factor": "0",
             "purchase_unit": "ลง", "purchase_factor": "12", "negallow": "", "balance": "10", "std_cost": "0"},
            {"code": "P002", "name": "น้ำแข็ง", "kind": "stock", "unit": "ถง", "sale_unit": "", "sale_factor": "0",
             "purchase_unit": "", "purchase_factor": "0", "negallow": "", "balance": "0", "std_cost": "0"},
            {"code": "S001", "name": "ค่าขนส่ง", "kind": "non_stock", "unit": "", "negallow": "", "balance": "0"},
        ],
        "stock": [{"code": "P001", "loccod": "01", "qty": "10", "value": "100.00"}],
        "units": [{"code": "ขด", "name": "ขวด"}, {"code": "ลง", "name": "ลัง"}, {"code": "ถง", "name": "ถัง"},
                  {"code": "กก", "name": "กิโลกรัม"}],
        "cash_account": CASH,
        "facts": {"accounts": [{"code": CASH, "name": "เงินสด"}, {"code": BANK, "name": "ธนาคาร"}]},
    }
    for key, value in over.items():
        if key == "settings":
            data["settings"] = {**data["settings"], **value}
        else:
            data[key] = value
    return data


def line(name, mode, qty="1", unit="", code="", **extra):
    return {"name": name, "code": code, "qty": qty, "unit": unit, "amount": "10.00", "item_mode": mode, **extra}


def doc(direction, *items, doctype=None, **header):
    return {"direction": direction, "doctype": doctype or ("IV" if direction == "sales" else "RR"),
            "items_status": "ok", "items": list(items), **header}


def kinds(result):
    return [q["kind"] for q in result["needed"]]


class Unit(unittest.TestCase):
    def setUp(self):
        self.facts = B.facts_of(masters())

    def test_a_unit_the_item_has_or_no_unit_on_the_ticket_is_not_asked(self):
        for unit in ("ขวด", "ลัง", "ขด", ""):
            with self.subTest(unit=unit):
                result = B.questions(self.facts, doc("purchase", line("โค้ก", B.STOCK_IN, unit=unit)))
                self.assertEqual(result["needed"], [])

    def test_a_unit_that_is_not_the_items_is_asked_with_its_units_and_a_factor_entry(self):
        result = B.questions(self.facts, doc("purchase", line("โค้ก", B.STOCK_IN, unit="แพ็ค")))
        [question] = result["needed"]
        self.assertEqual(question["kind"], B.UNIT)
        self.assertEqual([opt["code"] for opt in question["options"]], ["ขด", "ลง", "factor"])
        self.assertEqual(question["options"][-1], {"code": "factor", "entry": {"input": "number", "min_exclusive": "0"}})
        self.assertEqual([B.answer_problem(question, v) for v in ("ลง", "factor:6", "factor", "factor:0", "factor:x", "ลง:2", "กก")],
                         ["", "", "entry_missing", "entry_out_of_range", "entry_not_a_number", "no_entry", "not_an_option"])
        self.assertEqual(question["preselected"], "ลง", "采购只有一个采购包装单位：预选它")
        self.assertEqual(question["subject"], "unit|purchase|โค้ก|แพ็ค", "行上没带码：按规范品名记")

    def test_the_source_factor_answers_it_already(self):
        result = B.questions(self.facts, doc("purchase", line("โค้ก", B.STOCK_IN, unit="แพ็ค", factor="6")))
        self.assertEqual(result["needed"], [])

    def test_a_line_with_its_own_express_code_is_remembered_by_that_code(self):
        [question] = B.questions(self.facts, doc("purchase", line("โค้ก", B.STOCK_IN, unit="แพ็ค", code="P001")))["needed"]
        self.assertEqual(question["subject"], "unit|purchase|P001|แพ็ค")

    def test_a_new_item_whose_unit_does_not_resolve_is_asked_from_the_unit_table(self):
        result = B.questions(self.facts, doc("purchase", line("ข้าวสาร", B.STOCK_IN, unit="กระสอบ")))
        [question] = result["needed"]
        self.assertEqual([opt["code"] for opt in question["options"]], ["ขด", "ลง", "ถง", "กก"])
        self.assertFalse(any("entry" in opt for opt in question["options"]))
        self.assertEqual(B.questions(self.facts, doc("purchase", line("ข้าวสาร", B.STOCK_IN, unit="ถัง")))["needed"], [],
                         "新物料的票面单位能解析成单位表里的码：不问")

    def test_the_answer_goes_into_the_payload_and_is_remembered(self):
        payload = doc("purchase", line("โค้ก", B.STOCK_IN, unit="แพ็ค"), line("ข้าวสาร", B.STOCK_IN, unit="กระสอบ"))
        result = B.questions(self.facts, payload)
        answers = {B.UNIT: {result["needed"][0]["subject"]: "factor:6", result["needed"][1]["subject"]: "กก"}}
        written = B.apply(deepcopy(payload), answers)
        self.assertEqual(written["items"][0]["factor"], "6")
        self.assertEqual(written["items"][1]["unit_code"], "กก")
        self.assertTrue(written["guided"])
        self.assertEqual(common.finalize_payload(written)["payload_version"], common.GUIDED_PAYLOAD_VERSION)
        again = B.questions(self.facts, payload, remembered=B.remember_of(answers))
        self.assertEqual(again["needed"], [], "第二次同账套同物料同票面单位：不问")
        self.assertEqual(B.apply(deepcopy(payload), again["auto"])["items"][1]["unit_code"], "กก")

    def test_no_unit_table_reported_means_no_question(self):
        facts = B.facts_of(masters(units=None))
        self.assertEqual(B.questions(facts, doc("purchase", line("โค้ก", B.STOCK_IN, unit="แพ็ค")))["needed"], [])


class Negative(unittest.TestCase):
    def test_the_book_allows_negative_so_nothing_is_said(self):
        facts = B.facts_of(masters())
        result = B.questions(facts, doc("sales", line("โค้ก", B.STOCK_OUT, qty="50"), line("ใหม่", B.STOCK_OUT, qty="2", unit="ถัง")))
        self.assertEqual(result["facts"], [])
        self.assertNotIn(B.NEGATIVE, kinds(result))

    def test_an_existing_item_short_in_a_book_without_negative_stock_is_a_fact_with_hold(self):
        facts = B.facts_of(masters(settings={"negallow": "N"}))
        result = B.questions(facts, doc("sales", line("โค้ก", B.STOCK_OUT, qty="11")))
        [fact] = result["facts"]
        self.assertEqual((fact["kind"], fact["exit"]), (B.NEGATIVE, B.HOLD))
        self.assertEqual(fact["rows"], [{"code": "P001", "name": "โค้ก", "loccod": "01", "need": "11", "have": "10"}])
        self.assertEqual(B.questions(facts, doc("sales", line("โค้ก", B.STOCK_OUT, qty="10")))["facts"], [])

    def test_whole_book_level_checks_the_total_and_by_warehouse_the_line_warehouse(self):
        facts = B.facts_of(masters(settings={"negallow": "N", "chkneglev": "0"}))
        self.assertEqual(B.questions(facts, doc("sales", line("โค้ก", B.STOCK_OUT, qty="5", loccod="02")))["facts"], [])
        facts = B.facts_of(masters(settings={"negallow": "N", "chkneglev": "2"}))
        [fact] = B.questions(facts, doc("sales", line("โค้ก", B.STOCK_OUT, qty="5", loccod="02")))["facts"]
        self.assertEqual(fact["rows"][0]["loccod"], "02")

    def test_item_level_overrides_the_book(self):
        products = masters()["products"]
        products[0] = {**products[0], "negallow": "Y"}
        self.assertEqual(B.questions(B.facts_of(masters(settings={"negallow": "N"}, products=products)),
                                     doc("sales", line("โค้ก", B.STOCK_OUT, qty="50")))["facts"], [])
        products[0] = {**products[0], "negallow": "N"}
        self.assertTrue(B.questions(B.facts_of(masters(products=products)),
                                    doc("sales", line("โค้ก", B.STOCK_OUT, qty="50")))["facts"])

    def test_a_new_item_sold_in_a_book_without_negative_stock_asks_allow_or_hold_and_allow_is_remembered(self):
        facts = B.facts_of(masters(settings={"negallow": "N"}))
        payload = doc("sales", line("ใหม่", B.STOCK_OUT, qty="2", unit="ถัง"))
        result = B.questions(facts, payload)
        [question] = result["needed"]
        self.assertEqual(question["kind"], B.NEGATIVE)
        self.assertEqual([opt["code"] for opt in question["options"]], [B.ALLOW, B.HOLD])
        self.assertNotIn("preselected", question)
        self.assertNotIn(B.COST, kinds(result), "负库存这一关没过：不出成本题")
        answers = {B.NEGATIVE: {"ใหม่": B.ALLOW}}
        self.assertEqual(B.apply(deepcopy(payload), answers)["items"][0]["negallow"], "Y")
        self.assertFalse(B.held(answers))
        again = B.questions(facts, payload, remembered=B.remember_of(answers))
        self.assertNotIn(B.NEGATIVE, kinds(again))
        self.assertEqual(again["auto"][B.NEGATIVE], {"ใหม่": B.ALLOW})
        self.assertEqual(B.remember_of({B.NEGATIVE: {"ใหม่": B.HOLD}}), {}, "「先留下」不记")
        self.assertTrue(B.held({B.NEGATIVE: {"ใหม่": B.HOLD}}))

    def test_approval_books_only_offer_hold(self):
        facts = B.facts_of(masters(settings={"negallow": "Y", "negauth": "5"}))
        result = B.questions(facts, doc("sales", line("ใหม่", B.STOCK_OUT, unit="ถัง")))
        self.assertEqual(result["needed"], [])
        [fact] = result["facts"]
        self.assertTrue(fact["approval"])
        self.assertEqual(fact["exit"], B.HOLD)

    def test_old_masters_without_the_approval_setting_are_not_guessed(self):
        settings = {"mainloc": "01", "negallow": "Y", "cosmtd": "F"}
        facts = B.facts_of({**masters(), "settings": settings})
        result = B.questions(facts, doc("sales", line("ใหม่", B.STOCK_OUT, unit="ถัง")))
        self.assertEqual(result["facts"], [])
        self.assertNotIn(B.NEGATIVE, kinds(result))


class Cost(unittest.TestCase):
    def test_a_sale_without_a_cost_basis_asks_none_or_given_without_preselect_and_never_remembers(self):
        facts = B.facts_of(masters())
        payload = doc("sales", line("น้ำแข็ง", B.STOCK_OUT), line("ใหม่", B.STOCK_OUT, unit="ถัง"), line("โค้ก", B.STOCK_OUT))
        result = B.questions(facts, payload)
        costs = [q for q in result["needed"] if q["kind"] == B.COST]
        self.assertEqual([q["lines"] for q in costs], [[1], [2]], "有正结存的可乐不问")
        for question in costs:
            self.assertEqual([opt["code"] for opt in question["options"]], [B.COST_NONE, B.COST_GIVEN])
            self.assertEqual(question["options"][1]["entry"], B.NUMBER_ENTRY)
            self.assertEqual((B.answer_problem(question, "none"), B.answer_problem(question, "given:7.5")), ("", ""))
            self.assertNotIn("preselected", question)
        written = B.apply(deepcopy(payload), {B.COST: {"line-1": "none", "line-2": "given:7.5"}})
        self.assertEqual(written["items"][0]["cost_choice"], "none")
        self.assertEqual((written["items"][1]["cost_choice"], written["items"][1]["cost"]), ("given", "7.5"))
        self.assertEqual(B.remember_of({B.COST: {"line-1": "none"}}), {})

    def test_a_standard_cost_is_a_basis(self):
        products = masters()["products"]
        products[1] = {**products[1], "std_cost": "3"}
        self.assertEqual(B.questions(B.facts_of(masters(products=products)), doc("sales", line("น้ำแข็ง", B.STOCK_OUT)))["needed"], [])

    def test_purchases_are_never_asked_cost(self):
        self.assertEqual(B.questions(B.facts_of(masters()), doc("purchase", line("ใหม่", B.STOCK_IN, unit="ถัง")))["needed"], [])


class Cash(unittest.TestCase):
    def test_a_postable_cash_account_is_not_asked(self):
        self.assertEqual(B.questions(B.facts_of(masters()), doc("purchase", doctype="HP"))["needed"], [])

    def test_one_cash_named_account_is_used_without_asking(self):
        facts = B.facts_of(masters(cash_account="11-01-00-00"))
        result = B.questions(facts, doc("purchase", doctype="HP"))
        self.assertEqual(result["needed"], [])
        self.assertEqual(result["auto"], {B.CASH: {"cash": CASH}})
        self.assertEqual(B.apply(doc("purchase", doctype="HP"), result["auto"])["cash_account"], CASH)

    def test_several_cash_named_accounts_are_asked_cash_first_and_remembered(self):
        accounts = [{"code": BANK, "name": "ธนาคาร"}, {"code": CASH, "name": "เงินสด"}, {"code": PETTY, "name": "เงินสดย่อย"}]
        facts = B.facts_of(masters(cash_account="", facts={"accounts": accounts}))
        payload = doc("sales", doctype="HS")
        [question] = B.questions(facts, payload)["needed"]
        self.assertEqual([opt["code"] for opt in question["options"]], [CASH, PETTY, BANK])
        answers = {B.CASH: {"cash": PETTY}}
        self.assertEqual(B.questions(facts, payload, remembered=B.remember_of(answers))["auto"], {B.CASH: {"cash": PETTY}})

    def test_credit_documents_pos_and_refunds_are_not_asked(self):
        facts = B.facts_of(masters(cash_account=""))
        for payload in (doc("purchase"), doc("sales", doctype="HS", pos={}), doc("sales", doctype="HS", refund={})):
            self.assertEqual(B.questions(facts, payload)["needed"], [])


class Operator(unittest.TestCase):
    def test_the_operator_mode_never_asks_cost_or_cash(self):
        accounts = [{"code": CASH, "name": "เงินสด"}, {"code": PETTY, "name": "เงินสดย่อย"}]
        facts = B.facts_of(masters(cash_account="", facts={"accounts": accounts}))
        payload = doc("sales", line("น้ำแข็ง", B.STOCK_OUT, unit="กระสอบ"), doctype="HS")
        direct = set(kinds(B.questions(facts, payload)))
        operator = set(kinds(B.questions(facts, payload, mode=B.OPERATOR)))
        self.assertEqual(direct, {B.UNIT, B.COST, B.CASH})
        self.assertEqual(operator, {B.UNIT})
        self.assertTrue(operator <= set(B.OPERATOR_KINDS))


class Keys(unittest.TestCase):
    def test_every_key_apply_writes_is_a_payload_21_key_cc_knows(self):
        line_keys, header_keys = set(common.GUIDED_KEYS["line"]), set(common.GUIDED_KEYS["header"])
        payload = doc("sales", line("น้ำแข็ง", B.STOCK_OUT, unit="x"), line("ใหม่", B.STOCK_OUT), doctype="HS")
        before = deepcopy(payload)
        written = B.apply(payload, {B.UNIT: {B.unit_subject("sales", {"name": "น้ำแข็ง", "unit": "x"}): "ถง"},
                                    B.NEGATIVE: {"ใหม่": "Y"}, B.COST: {"line-1": "given:2"}, B.CASH: {"cash": CASH}})
        for old, new in zip(before["items"], written["items"]):
            self.assertLessEqual(set(new) - set(old), line_keys)
        self.assertLessEqual(set(written) - set(before), header_keys)

    def test_without_guided_the_payload_does_not_turn_on_the_strict_mode(self):
        self.assertNotIn("guided", B.apply(doc("purchase"), {}, guided=False))


if __name__ == "__main__":
    unittest.main()
