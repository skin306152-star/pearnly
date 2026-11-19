"""ERP 库存单写进 MR.ERP（`pearnly_erp_portal.mrerp.stock` + `mrerp.push` 的库存一支，评估 2026-11-19）的离线检查。

沿用 `test_erp_mrerp_direct.py` 的内存 Frappe 替身与假执行（同一进程里导入它）。总开关在测试里打开：
- 出库 / 入库：排上 MR.ERP、领取后交共用层库存导入（假执行），载荷换成库存单位、带 MR.ERP 物料 / 仓库码、入库带单价、出库不带；
  出库按用途取记住的菜单；成功记 MR.ERP 单号；
- 调拨 / 调整：网页表单直写做好之前如实标失败（``ERR_MRERP_MODULE_UNAVAILABLE``），不导、不静默；
- 物料或仓库没有 MR.ERP 编码：停在单据上说清是哪一种；
- 负库存：这份账套设成不允许时，出库过账前按本公司各仓合计拦；允许 / 没读到 / 入库都不拦。
"""
from __future__ import annotations

import datetime
import json
import sys
import types
import unittest
from decimal import Decimal
from unittest.mock import patch

import test_erp_mrerp_direct as T  # noqa: E402  装好 Frappe 替身

# 库存一侧（`inventory.documents` 的用途表、`warehouses.names_of`）要完整的 Frappe：这里只给用到的那一点
_documents = types.ModuleType("pearnly_erp_portal.inventory.documents")
_documents.PURPOSES = {"in": ("return", "other"), "out": ("internal_use", "damage", "sample", "production", "other")}
_warehouses = types.ModuleType("pearnly_erp_portal.inventory.warehouses")
_warehouses.names_of = lambda company=None, **k: ["WH-1", "WH-2"]
sys.modules.update({"pearnly_erp_portal.inventory.documents": _documents,
                    "pearnly_erp_portal.inventory.warehouses": _warehouses})

from pearnly_erp_portal.mrerp import push, stock  # noqa: E402

STOCK = push.STOCK
STOCK_CALLS = []


def body(name, **extra):
    """`express.stock_entry.document` 的形状（这里直接给）。"""
    out = {"ref": f"{name}@x", "direction": "stock", "doctype": "OU", "date": "2026-11-03", "number": name,
           "remark": "เบิกใช้", "loccod": "0001",
           "lines": [{"code": "P0001", "name": "x", "qty": "2", "unit": "กล่อง", "loccod": "0001", "factor": "12",
                      "product": "PRD-1"}]}
    out.update(extra)
    return out


BODIES = {}


def document(doctype, name):
    return BODIES[name]


class _Stock:
    def __init__(self, outcome):
        self.outcome = outcome

    def __enter__(self):
        module = types.ModuleType("pearnly_core.mrerp.push.stock")

        def execute(payload, target, secrets, system_url):
            STOCK_CALLS.append({"payload": payload, "target": target})
            return self.outcome

        module.execute = execute
        self.inner = T._Module({"status": "failed", "reason": "invoice path must not run"})
        self.inner.__enter__()
        self.patch = patch.dict(sys.modules, {"pearnly_core.mrerp.push.stock": module})
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()
        self.inner.__exit__(*exc)


def entry(name="OUT-1", direction="out", purpose=None, **extra):
    T.DB.table(STOCK)[name] = {"name": name, "status": "submitted", "direction": direction, "purpose": purpose,
                               "warehouse": "WH-1", "posting_date": "2026-11-03", "ref_type": None,
                               "creation": datetime.datetime(2026, 11, 3, 9), "ledger_state": None, "ledger_reason": "",
                               "owner": "boss@x", **extra}
    BODIES[name] = body(name)
    return T.Row(T.DB.table(STOCK)[name])


@patch.object(T.mrerp, "MRERP_LEDGER_ENABLED", True)
class StockToMrErp(unittest.TestCase):
    def setUp(self):
        T.reset()
        STOCK_CALLS.clear()
        BODIES.clear()
        T.DB.table("ERP Warehouse")["WH-1"] = {"name": "WH-1", "company": "CO-1", "express_code": "0001"}

    def run_one(self, name, outcome=None):
        T.DB.table(STOCK)[name].update(ledger_state="queued", ledger_target="mrerp")
        with _Stock(outcome or {"status": "success", "docnum": "IS" + name, "menus": {}, "masters": {}}):
            push.run(STOCK, name, document)
        return T.DB.table(STOCK)[name]

    def test_a_submitted_issue_is_queued_for_mrerp_with_its_warehouse_company(self):
        row = entry()
        row.company = stock.company_of("OUT-1")
        push.queue(STOCK, row)
        saved = T.DB.table(STOCK)["OUT-1"]
        self.assertEqual((saved["ledger_state"], saved["ledger_target"]), ("queued", "mrerp"))

    def test_an_issue_is_imported_in_stock_units_without_a_price(self):
        entry()
        saved = self.run_one("OUT-1")
        self.assertEqual((saved["ledger_state"], saved["ledger_docnum"]), ("written", "ISOUT-1"))
        payload = STOCK_CALLS[0]["payload"]
        self.assertEqual(payload["kind"], "out")
        self.assertEqual(payload["lines"], [{"code": "P0001", "store": "0001", "qty": "24"}])  # 2 箱 × 12
        self.assertEqual((payload["number"], payload["date"], payload["notes"][0]), ("OUT-1", "2026-11-03", "เบิกใช้"))
        self.assertEqual(STOCK_CALLS[0]["target"], {"account_set": "15:1"})

    def test_a_receipt_carries_the_cost_per_stock_unit_and_one_without_a_cost_leaves_it_to_mrerp(self):
        entry("IN-1", direction="in")
        BODIES["IN-1"]["lines"][0]["unit_cost"] = "120"
        self.run_one("IN-1")
        self.assertEqual(STOCK_CALLS[0]["payload"]["lines"][0], {"code": "P0001", "store": "0001", "qty": "24",
                                                                 "price": "10"})
        entry("IN-2", direction="in")
        self.run_one("IN-2")
        self.assertNotIn("price", STOCK_CALLS[1]["payload"]["lines"][0])  # 共用层照原生补标准成本

    def test_the_issue_menu_follows_the_purpose(self):
        T.DB.table("ERP Company")["CO-1"]["mrerp_menus"] = json.dumps(
            {"15:1": {"stock_out.damage": {"idmenu": "34", "label": "x"}, "stock_out.other": {"idmenu": "33", "label": "y"}}})
        entry("OUT-2", purpose="damage")
        entry("OUT-3")
        self.run_one("OUT-2")
        self.run_one("OUT-3")
        self.assertEqual([call["payload"]["menu"] for call in STOCK_CALLS], ["34", "33"])

    def test_transfers_and_adjustments_are_marked_not_written_until_the_form_write_lands(self):
        for name, direction in (("TR-1", "transfer"), ("ADJ-1", "adjust")):
            entry(name, direction=direction)
            saved = self.run_one(name)
            with self.subTest(direction=direction):
                self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("failed", stock.FORM_PENDING))
        self.assertEqual(STOCK_CALLS, [])

    def test_an_item_or_warehouse_without_an_mrerp_code_holds_with_the_reason(self):
        entry("OUT-4")
        BODIES["OUT-4"]["lines"][0]["code"] = ""
        self.assertEqual(self.run_one("OUT-4")["ledger_reason"], stock.ITEM_UNMAPPED)
        entry("OUT-5")
        BODIES["OUT-5"]["unmapped_warehouses"] = ["WH-9"]
        self.assertEqual(self.run_one("OUT-5")["ledger_reason"], stock.WAREHOUSE_UNMAPPED)
        self.assertEqual(STOCK_CALLS, [])

    def test_a_failed_import_lands_on_the_entry(self):
        entry("OUT-6")
        saved = self.run_one("OUT-6", {"status": "failed", "reason": "ERR_MRERP_MENU_NOT_LINKED", "menus": {}, "masters": {}})
        self.assertEqual((saved["ledger_state"], saved["ledger_reason"]), ("failed", "ERR_MRERP_MENU_NOT_LINKED"))

    def test_menu_kinds_cover_every_issue_purpose(self):
        kinds = stock.kinds_of("out")
        for purpose in _documents.PURPOSES["out"]:
            self.assertIn(f"stock_out.{purpose}", kinds)
        self.assertEqual(stock.kinds_of("in"), ("stock_in",))


class _Line(T.Row):
    pass


@patch.object(T.mrerp, "MRERP_LEDGER_ENABLED", True)
class NegativeStock(unittest.TestCase):
    def setUp(self):
        T.reset()
        T.DB.table("ERP Product")["PRD-1"] = {"name": "PRD-1", "base_unit": "ชิ้น", "unit": "ชิ้น"}
        T.DB.table("ERP Stock Balance")["B1"] = {"product": "PRD-1", "warehouse": "WH-1", "qty": 3}
        T.DB.table("ERP Stock Balance")["B2"] = {"product": "PRD-1", "warehouse": "WH-2", "qty": 2}

    def observed(self, value):
        T.DB.table("ERP MRERP Connection")["C1"]["master_options"] = json.dumps(
            {"15:1": {"fields": {"stock_negative": value}, "observed_at": "2026-11-19"}})

    def doc(self, qty, direction="out"):
        return T.Row({"name": "", "direction": direction, "warehouse": "WH-1",
                      "lines": [_Line({"product": "PRD-1", "qty": qty, "unit": "ชิ้น"})]})

    def check(self, doc):
        with patch.object(stock, "_factor", lambda product, unit: Decimal("1")):
            stock.check_negative(doc, "CO-1")

    def test_not_allowed_blocks_more_than_all_warehouses_hold(self):
        self.observed("2")
        self.check(self.doc(5))  # 3 + 2：全部仓合计够
        with self.assertRaises(ValueError) as caught:
            self.check(self.doc(6))
        self.assertEqual(str(caught.exception), "Not enough stock on hand")

    def test_allowed_unread_or_a_receipt_is_not_checked(self):
        self.observed("1")
        self.check(self.doc(99))
        T.DB.table("ERP MRERP Connection")["C1"].pop("master_options", None)
        self.check(self.doc(99))
        self.observed("2")
        self.check(self.doc(99, direction="in"))

    def test_an_express_company_is_not_checked(self):
        self.observed("2")
        T.DB.table("ERP Company")["CO-1"]["express_enabled"] = 1
        self.check(self.doc(99))

    def test_the_setting_is_read_from_the_issue_form(self):
        from pearnly_core.mrerp import masters as master_rules

        self.assertEqual(master_rules.parse_stock_negative("<script>var ssunegative = 2;</script>"), "2")
        self.assertEqual(master_rules.parse_stock_negative("alert('กรุณา Login')"), "")
        kept = master_rules.observe({}, "15:1", {"stock_negative": "2"}, at="2026-11-19")
        self.assertEqual(master_rules.observed_for(kept, "15:1"), {"stock_negative": "2"})
        self.assertEqual(Decimal("1"), Decimal("1"))


if __name__ == "__main__":
    unittest.main()
