"""ERP 接开始前四题（A3 / P2-2，``express.book_answers``）：内存替身，不连库。

- 开始前确认卡片里（``book_answers.asking``）：单位对不上 / 新物料在不许负库存的账套卖出 / 出库没有成本基础 / 现金科目不能记账 → 停下问
  （``choice_needed:<类>``，带选项，带数字输入的选项保留 ``entry``）；已有物料库存不够 → 停在 ``STOCK_INSUFFICIENT``；
- 答了（记进 ``ERP Express Choice``，对象带账套键；成本带单据号）→ 再准备照写进载荷；第二次不问；
- 后台直写（不在 ``asking`` 里，网页提交 / LINE / 定时同一条路）不新增停下，只套用记住的值——LINE 这次不出四题、行为不变；
- 代录（``book_answers.operating``）只问单位与负库存，载荷只带 CC 代录认的行键、不带 ``guided``；
- 共用卡片契约认这四类，带数字输入的选项收「码:数」。
"""
import sys
import unittest
from copy import deepcopy

from test_erp_express_direct import COMPANY, Base, book, doc  # noqa: E402  （同目录；导入时装好内存替身）

from pearnly_core.express_rules.push import book_questions as B  # noqa: E402
from pearnly_core.express_rules.push import choices as C  # noqa: E402
from pearnly_core.write_precheck import contracts as card  # noqa: E402
from pearnly_erp_portal.express import book_answers, choices, contracts as E  # noqa: E402

CASH, PETTY, BANK = "11-01-01-00", "11-01-03-00", "11-01-02-00"
STOCK = [{"code": "ICE-01", "name": "น้ำแข็งแพ็ค", "kind": "stock", "unit": "ถง", "purchase_unit": "ลง", "purchase_factor": "12",
          "sale_unit": "", "sale_factor": "0", "negallow": "", "balance": "5", "std_cost": "0"},
         {"code": "ICE-02", "name": "อื่น", "kind": "stock", "unit": "ถง", "purchase_unit": "", "purchase_factor": "0",
          "sale_unit": "", "sale_factor": "0", "negallow": "", "balance": "0", "std_cost": "0"}]
UNITS = [{"code": "ถง", "name": "ถัง"}, {"code": "ลง", "name": "ลัง"}, {"code": "กก", "name": "กิโลกรัม"}]


def item(name, code, unit="ถัง", qty="1", amount="100.00", **extra):
    return {"name": name, "code": code, "unit": unit, "qty": qty, "price": amount, "amount": amount, "loccod": "", "stock": True,
            **extra}


def one(direction="purchase", paid=False, *items):
    party = ({"code": "C01", "name": "ร้านป้าแดง", "tax_id": "", "branch": ""} if direction == "sales" else None)
    lines = list(items) or [item("น้ำแข็งแพ็ค", "ICE-01")]
    total = sum(float(row["amount"]) for row in lines)
    body = doc(direction=direction, paid=paid, items=lines, subtotal=f"{total:.2f}", vat=f"{total * 0.07:.2f}",
               total=f"{total * 1.07:.2f}")
    if party:
        body["party"] = party
    return body


class BookQuestions(Base):
    def setUp(self):
        super().setUp()
        self.key, = self.computer([book()])
        self.data(settings={"mainloc": "01", "negallow": "Y", "negauth": "", "chkneglev": "2"})

    def data(self, **extra):
        self.masters(self.key, **{"products": STOCK, "units": UNITS, "stock": [{"code": "ICE-01", "loccod": "01", "qty": "5"}],
                                  "cash_account": CASH, **extra})

    def ask(self, body):
        """开始前确认卡片那样判：物料题（新建 / 选已有）按「新建」记下往下判（卡片里同样按新建试算），只看四题。"""
        with book_answers.asking():
            for _round in range(3):
                result = self.prepare(deepcopy(body))
                if result.get("hold") != E.CHOICE + ":item":
                    return result
                for needed in result["needed"]:
                    choices.remember(COMPANY["name"], needed["kind"], needed["subject"], C.NEW)
            return result

    def answer(self, needed, value):
        item = needed if isinstance(needed, dict) else needed[0]
        self.assertEqual(book_answers.value_problem(item, value), "")
        if book_answers.keep(item, value):
            choices.remember(COMPANY["name"], item["kind"], item["subject"], value)
            book_answers.remember_extra(COMPANY["name"], item, value, choices.remember)

    def test_a_unit_that_is_not_the_items_is_asked_then_written_and_remembered(self):
        body = one("purchase", False, item("น้ำแข็งแพ็ค", "ICE-01", unit="แพ็ค"))
        held = self.ask(body)
        self.assertEqual(held["hold"], E.CHOICE + ":unit")
        [needed] = held["needed"]
        self.assertEqual(needed["subject"], f"{self.key}|unit|purchase|ICE-01|แพ็ค")
        self.assertEqual([(o["code"], o["name"]) for o in needed["options"]][:2], [("ถง", "ถง"), ("ลง", "ลง × 12")])
        self.assertEqual(needed["options"][-1]["entry"], B.NUMBER_ENTRY)
        self.assertEqual(needed["preselected"], "ลง")
        quiet = self.prepare(deepcopy(body))
        self.assertNotIn("hold", quiet, "后台直写不新增停下")
        self.assertNotIn("unit_code", quiet["payload"]["items"][0])
        self.answer(needed, "factor:6")
        self.assertNotIn("hold", self.ask(body), "第二次不问")
        self.assertEqual(self.prepare(deepcopy(body))["payload"]["items"][0]["factor"], "6")

    def test_a_new_item_sold_in_a_book_without_negative_stock_then_its_cost(self):
        self.data(settings={"mainloc": "01", "negallow": "N", "negauth": "", "chkneglev": "2"})
        body = one("sales", False, item("ของใหม่", "", unit="ถัง"))
        held = self.ask(body)
        self.assertEqual(held["hold"], E.CHOICE + ":negative_stock")
        self.assertEqual([o["code"] for o in held["needed"][0]["options"]], [B.ALLOW, B.HOLD])
        self.answer(held["needed"], B.HOLD)
        self.assertEqual(self.ask(body)["hold"], E.CHOICE + ":negative_stock", "「先留下」不记，下次再问")
        self.answer(held["needed"], B.ALLOW)
        cost = self.ask(body)
        self.assertEqual(cost["hold"], E.CHOICE + ":cost_basis")
        self.assertIn("|doc:", cost["needed"][0]["subject"], "成本只对这一张单")
        self.answer(cost["needed"], "given:4")
        line = self.prepare(deepcopy(body))["payload"]["items"][0]
        self.assertEqual((line["negallow"], line["cost_choice"], line["cost"]), ("Y", "given", "4"))
        other = one("sales", False, item("ของใหม่อีก", "", unit="ถัง"))
        other["ref"] = "SAL-2026-00099@20261116101010000000"
        again = self.ask(other)
        self.assertEqual(again["hold"], E.CHOICE + ":cost_basis", "负库存按账套记住不再问，成本每张单都问")

    def test_the_page_submit_dialog_asks_no_cost_and_offers_no_hold(self):
        """网页提交前（表单没存、单据号 preflight）：成本答案挂不到存好的单上，不问；关掉弹窗就是不提交，不给「先留下」。"""
        self.data(settings={"mainloc": "01", "negallow": "N", "negauth": "", "chkneglev": "2"})
        body = one("sales", False, item("ของใหม่ฟอร์ม", "", unit="ถัง"))
        body["ref"] = book_answers.FORM_REF
        held = self.ask(body)
        self.assertEqual([o["code"] for o in held["needed"][0]["options"]], [B.ALLOW])
        self.answer(held["needed"], B.ALLOW)
        self.assertNotIn("hold", self.ask(body), "不问成本")

    def test_an_existing_item_short_stops_only_where_someone_is_looking(self):
        self.data(settings={"mainloc": "01", "negallow": "N", "negauth": "", "chkneglev": "2"})
        body = one("sales", False, item("น้ำแข็งแพ็ค", "ICE-01", qty="9", unit="ถัง"))
        body["items"][0]["loccod"] = "01"
        self.assertEqual(self.ask(body)["hold"], book_answers.STOCK_INSUFFICIENT)
        self.assertNotIn("hold", self.prepare(deepcopy(body)), "后台直写照旧交 CC 按原样核")

    def test_an_unpostable_cash_account_on_a_cash_purchase(self):
        accounts = [{"code": code, "name": name, "type": "1"} for code, name in
                    ((CASH, "เงินสด"), (PETTY, "เงินสดย่อย"), (BANK, "ธนาคาร"))] + \
            [{"code": code, "name": code, "type": code[0]} for code in ("51-03-00-00", "21-01-01-00", "11-06-01-00")]
        self.data(cash_account="11-01-00-00", facts={"accounts": accounts, "mapping": {}, "catalog": {"isacc_candidates": []}})
        body = one("purchase", True, item("น้ำแข็งแพ็ค", "ICE-01"))
        held = self.ask(body)
        self.assertEqual(held["hold"], E.CHOICE + ":cash_account")
        self.assertEqual([o["code"] for o in held["needed"][0]["options"]][:2], [CASH, PETTY])
        self.answer(held["needed"], PETTY)
        self.assertEqual(self.prepare(deepcopy(body))["payload"]["cash_account"], PETTY)

    def test_the_operator_asks_only_unit_and_negative_and_writes_only_operator_keys(self):
        self.data(cash_account="", settings={"mainloc": "01", "negallow": "Y", "negauth": "", "chkneglev": "2"})
        body = one("sales", True, item("อื่น", "ICE-02", unit="แพ็ค"))
        with book_answers.operating():
            held = self.ask(body)
            self.assertEqual(held["hold"], E.CHOICE + ":unit")
            self.answer(held["needed"], "ถง")
            payload = self.prepare(deepcopy(body))["payload"]
        self.assertNotIn("guided", payload)
        self.assertNotIn("cash_account", payload)
        self.assertNotIn("cost_choice", payload["items"][0])
        self.assertEqual(payload["items"][0]["unit_code"], "ถง")
        direct = self.ask(body)
        self.assertEqual(direct["hold"], E.CHOICE + ":cost_basis,cash_account", "直写这张还要问成本与现金科目")


class Card(unittest.TestCase):
    def test_the_shared_card_takes_the_four_kinds_and_number_entries(self):
        options = [{"code": "ถง", "name": "ถง"}, {"code": "factor", "name": "x", "entry": dict(B.NUMBER_ENTRY)}]
        raw = {"kind": "unit", "subject": "K|unit|purchase|ICE-01|แพ็ค", "label": "น้ำแข็ง", "options": options}
        item = card.normalize_item(raw, "purchase")
        self.assertEqual(item["group"], card.STOCK)
        self.assertEqual(item["options"][1]["entry"], B.NUMBER_ENTRY)
        for kind in ("negative_stock", "cost_basis", "cash_account"):
            self.assertIn(kind, card.KINDS)
        merged = card.merge([{"doc_id": "P:1", "state": card.CHECK, "items": [raw]}])
        key = card.item_key("unit", raw["subject"])
        ok, errors = card.expand(merged, [{"key": key, "value": "factor:6"}])
        self.assertEqual(errors, [])
        for bad in ("factor", "factor:0", "factor:x", "kg"):
            _ok, errors = card.expand(merged, [{"key": key, "value": bad}])
            self.assertEqual([e["code"] for e in errors], [card.ERR_NOT_OFFERED], bad)


if __name__ == "__main__":
    unittest.main()
