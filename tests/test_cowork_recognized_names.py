#!/usr/bin/env python3
"""识别入库时按税号统一买卖方名称、清掉不是品名的品名、按记忆补空品名（`contracts/recognized_names.py`、
`history/names.py`）。

样本是 2026-10-02 testcowork 上 Big C 27 页销项（同一卖方读出 8 种写法、被章盖住的品名读成编号 / 单位 / 邮编），
规则按类，不针对这家公司。在产品镜像里跑（要能 import Frappe）；数据库是内存里的字典。
"""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_cowork"), str(ROOT / "apps/pearnly_core")]

from pearnly_cowork.contracts import recognized_names as rules  # noqa: E402
from pearnly_cowork.history import names  # noqa: E402

WS = "WS-1"
SELLER = "0105546015062"
BUYER = "0107536000633"
# (卖方名, 买方名, 买方税号, 第一行品名)：Big C 27 页的原样识别结果。
BIG_C = [
    ("บริษัท ซินเซียร์ ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "4"),
    ("บริษัท ซินเซียไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "ถุง"),
    ("บริษัท ซินเซียร์ ไอซ์ จำกัด", "บมจ. บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", "0107536000632", "10500"),
    ("บริษัท ชินเชียร์ไลจ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์(สำนักงานใหญ่)", BUYER, "น้ำแข็ง"),
    ("บริษัท ชินเชียร์ไลซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์(สำนักงานใหญ่)", BUYER, "ถุง"),
    ("บริษัท ชินเชียร์ ไอซ์ จำกัด", "บมจ บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ซินเชียร์ ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งหลอด"),
    ("บริษัท ซินเชียร์ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ซินเซียร์ ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ชินเชียร์ ไลจ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ชินเชียร์ ไลซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "4"),
    ("บริษัท ซินเซียร์ ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ชินเชียร์ ไลซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำมันก๊าด"),
    ("บริษัท ชินเชียร์ไลซ์ จำกัด", "บมจ บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำดื่ม"),
    ("บริษัท ซินเซียร์ ไอซ์ จำกัด", "บมจ บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งหลอด"),
    ("บริษัท ชินเชียร์ ไอซ์ จำกัด", "บมจ บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ชินเชียร์ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งหลอด"),
    ("บริษัท ชินเชียร์ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ซินเซียร์ ไลซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพค"),
    ("บริษัท ซินเชียร์ ไลซ์ จำกัด", "บมจ บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแห้ง"),
    ("บริษัท ซินเซียร์ ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ชินเชียร์ ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งหลอด"),
    ("บริษัท ชินเชียร์ ไอซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ซินเซียไลซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "ถุง"),
    ("บริษัท ซินเซียร์ ไอซ์ จำกัด", "บมจ บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ซินเซียร์ ไลซ์ จำกัด", "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
    ("บริษัท ชินเชียร์ ไอซ์ จำกัด", "บมจ บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)", BUYER, "น้ำแข็งแพ็ค"),
]
SINCERE = "บริษัท ซินเซียร์ ไอซ์ จำกัด"
BIG_C_NAME = "บมจ.บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)"


def invoice(seller=SINCERE, buyer=BIG_C_NAME, buyer_tax=BUYER, item="น้ำแข็งแพ็ค", price="6.70", seller_tax=SELLER):
    return [{"page_index": 1, "fields": {
        "direction": "sales", "invoice_number": "IV69/00512", "seller_name": seller, "seller_tax": seller_tax,
        "buyer_name": buyer, "buyer_tax": buyer_tax, "subtotal": "268.00", "vat": "18.76", "total_amount": "286.76",
        "items": [{"name": item, "qty": "40.00", "price": price, "subtotal": "268.00"}]}}]


def big_c():
    return [invoice(seller, buyer, tax, item) for seller, buyer, tax, item in BIG_C]


class Rules(unittest.TestCase):
    def test_a_name_follows_only_a_tax_id_with_a_valid_check_digit(self):
        self.assertEqual(rules.tax_key("0-1075-36000-63-3"), BUYER)
        self.assertEqual(rules.tax_key("105546015062"), SELLER)  # 掉了开头 0、补上后校验位对
        self.assertEqual(rules.tax_key("0107536000632"), "")     # 第 3 页末位读错：不能当身份用
        self.assertEqual(rules.tax_key(""), "")

    def test_one_batch_settles_on_its_most_common_spelling(self):
        plan = rules.plan_batch([pages[0]["fields"] for pages in big_c()])
        seller = plan[("seller", SELLER)]
        # ซินเซียร์ ไอซ์ 与 ชินเชียร์ ไอซ์ 各 7 次：一样多取先出现的；其余 7 种都并进来。
        self.assertEqual(seller["name"], SINCERE)
        self.assertEqual(len(seller["variants"]), 9)
        # 买方只是标点空格不同：同一种写法，取最常见的原文。税号读错的第 3 页不参与。
        self.assertEqual(plan[("buyer", BUYER)]["name"], BIG_C_NAME)
        self.assertNotIn(("buyer", "0107536000632"), plan)

    def test_a_consistent_batch_has_nothing_to_settle(self):
        self.assertEqual(rules.plan_batch([invoice()[0]["fields"], invoice()[0]["fields"]]), {})

    def test_spellings_differ_by_letters_not_by_punctuation(self):
        self.assertEqual(rules.name_key("บมจ.บิ๊กซี (HQ)"), rules.name_key("บมจ บิ๊กซี HQ"))
        self.assertNotEqual(rules.name_key("ซินเซียร์ ไอซ์"), rules.name_key("ซินเซียร์ ไลซ์"))

    def test_a_code_a_number_or_a_bare_unit_is_not_an_item_name(self):
        for value in ("4", "10500", "ถุง", " แพ็ค ", "PCS", "kg.", "#12-3"):
            self.assertTrue(rules.placeholder_item_name(value), value)
        for value in ("", "น้ำแข็งหลอด", "น้ำแข็งแพ็ค", "ถุงพลาสติก", "Coke 325ml", "A4 paper"):
            self.assertFalse(rules.placeholder_item_name(value), value)

    def test_prices_compare_at_two_decimals(self):
        self.assertEqual((rules.price_key("6.7"), rules.price_key("1,250"), rules.price_key("x")), ("6.70", "1250.00", ""))
        self.assertEqual(rules.price_key("0"), "")


class Row(dict):
    def __getattr__(self, key):
        if key == "flags":
            return self.setdefault("_flags", SimpleNamespace())
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    def is_new(self):
        return self.get("_new", False)

    def insert(self, **kwargs):
        Store.current.rows[self["record_key"]] = self
        self["_new"] = False

    save = insert


class Store:
    current = None

    def __init__(self):
        self.rows = {}
        Store.current = self

    def get_value(self, doctype, name, field, for_update=False):
        return name if name in self.rows else None

    def get_doc(self, body_or_doctype, name=None, for_update=False):
        if isinstance(body_or_doctype, dict):
            return Row(body_or_doctype, _new=True)
        return self.rows[name]

    def sql(self, query, values):
        if "WHERE name=%s" in query:
            row = self.rows.get(values[0])
            return [(row["value"],)] if row else []
        workspace, tax, price = values
        found = []
        for row in self.rows.values():
            if (row["workspace"], row["kind"], row["tax_id"], row["price"]) == (workspace, "item", tax, price):
                if row["value"] not in found:
                    found.append(row["value"])
        return [(value,) for value in found[:2]]


class Memory(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        for target in ("require", "require_scope"):
            p = patch.object(names.scope, target, lambda *a, **k: None); p.start(); self.addCleanup(p.stop)
        p = patch.object(names.frappe, "db", self.store); p.start(); self.addCleanup(p.stop)
        p = patch.object(names.frappe, "get_doc", self.store.get_doc); p.start(); self.addCleanup(p.stop)
        # 存档点包装不是这里要测的。
        p = patch.object(names, "_optional", lambda fn, fallback, label: fn()); p.start(); self.addCleanup(p.stop)

    def batch(self):
        records = big_c()
        plan = names.plan(records)
        return [names.prepare(WS, pages, batch_plan=plan)[0]["fields"] for pages in records]

    def test_the_whole_batch_carries_one_seller_name(self):
        saved = self.batch()
        self.assertEqual({fields["seller_name"] for fields in saved}, {SINCERE})
        self.assertEqual({fields["buyer_name"] for fields in saved}, {BIG_C_NAME, "บมจ. บิ๊กซี ซูเปอร์เซ็นเตอร์ (สำนักงานใหญ่)"})

    def test_codes_units_and_numbers_are_cleared_not_kept_as_item_names(self):
        saved = self.batch()
        cleared = [index + 1 for index, fields in enumerate(saved) if fields["items"][0]["name"] == ""]
        self.assertEqual(cleared, [1, 2, 3, 5, 11, 24])
        # 金额、数量照旧，只是品名空着。
        self.assertEqual(saved[0]["items"][0]["subtotal"], "268.00")

    def test_the_input_pages_are_never_changed(self):
        records = big_c()
        names.prepare(WS, records[3], batch_plan=names.plan(records))
        self.assertEqual(records[3][0]["fields"]["seller_name"], "บริษัท ชินเชียร์ไลจ์ จำกัด")

    def test_a_name_a_person_corrected_wins_from_then_on(self):
        decided = names.learn(WS, invoice(), invoice(seller="บริษัท ซินเซียร์ ไอซ์ จำกัด (สำนักงานใหญ่)"))
        self.assertEqual(decided, {SELLER: "บริษัท ซินเซียร์ ไอซ์ จำกัด (สำนักงานใหญ่)"})
        saved = self.batch()
        self.assertEqual({fields["seller_name"] for fields in saved}, {"บริษัท ซินเซียร์ ไอซ์ จำกัด (สำนักงานใหญ่)"})
        # 单张进来（LINE 等没有整批的入口）也按记住的名称。
        single = names.prepare(WS, invoice(seller="บริษัท ชินเชียร์ ไลซ์ จำกัด"))[0]["fields"]
        self.assertEqual(single["seller_name"], "บริษัท ซินเซียร์ ไอซ์ จำกัด (สำนักงานใหญ่)")

    def test_an_unchanged_name_or_an_invalid_tax_id_teaches_nothing(self):
        self.assertEqual(names.learn(WS, invoice(), invoice()), {})
        self.assertEqual(names.learn(WS, invoice(buyer_tax="0107536000632"),
                                     invoice(buyer="Big C", buyer_tax="0107536000632")), {})
        self.assertEqual(self.store.rows, {})

    def test_an_item_name_a_person_filled_in_comes_back_for_the_same_seller_and_price(self):
        names.learn(WS, invoice(item=""), invoice(item="น้ำแข็งแพ็ค"))
        saved = self.batch()
        self.assertEqual([fields["items"][0]["name"] for fields in saved][:3], ["น้ำแข็งแพ็ค"] * 3)
        # 其他价钱、其他卖方不补；模型读出的品名不覆盖（只补空的）。
        other = names.prepare(WS, invoice(item="4", price="12.00"))[0]["fields"]
        self.assertEqual(other["items"][0]["name"], "")
        kept = names.prepare(WS, invoice(item="น้ำแข็งหลอด"))[0]["fields"]
        self.assertEqual(kept["items"][0]["name"], "น้ำแข็งหลอด")

    def test_two_different_names_at_one_price_fill_nothing(self):
        names.learn(WS, invoice(item=""), invoice(item="น้ำแข็งแพ็ค"))
        names.learn(WS, invoice(item=""), invoice(item="น้ำแข็งหลอด"))
        self.assertEqual(names.prepare(WS, invoice(item="4"))[0]["fields"]["items"][0]["name"], "")

    def test_a_placeholder_typed_back_is_not_learned(self):
        names.learn(WS, invoice(item=""), invoice(item="ถุง"))
        self.assertEqual(self.store.rows, {})

    def test_drafts_of_the_same_original_follow_the_decided_name(self):
        decided = {SELLER: SINCERE}
        followed = names.renamed(invoice(seller="บริษัท ชินเชียร์ ไลซ์ จำกัด"), decided)
        self.assertEqual(followed[0]["fields"]["seller_name"], SINCERE)
        self.assertIsNone(names.renamed(invoice(), decided))
        self.assertIsNone(names.renamed(invoice(seller="Other", seller_tax="0105536080112"), decided))


if __name__ == "__main__":
    unittest.main()
