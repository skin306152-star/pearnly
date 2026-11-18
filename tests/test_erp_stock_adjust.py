"""物料调整（岳父 11-06，评估 docs/work/2026-11-06-erp-stock-adjust-评估.md，主会话审定）。内存替身，不连库。

- 一张单正负混录：数量不能为 0；减少的行不存价；增加的行能看成本的人给的价照存，人亲手填 / 改过的才记 `unit_cost_manual`；
  看不到成本的人给的价不算，按当前均价；服务端自己生成的行（盘点估值）照存、不算人填；
- 一张单一个仓库：行上的仓一律换成单头的；盘点生成的调整单里在别的仓找到的货保留自己的仓；
- 单的种类：方向 adjust 与历史盘点 AD 单（ref_type = count）都归物料调整；列表权限按种类拼；
- Express：人建的物料调整单写增减调整 JU（4-3-1，11-18 主会话定 D1），盘点生成的调整单与历史盘点写盘点调整 TK；都按「存货 × 仓」
  轧差（仓位挪动净 0 不写）；TK 只带人填的单价，JU 带页面上的单价（原生调增要人填价）；表单预检同样分。
"""
import pathlib
import sys
import types
import unittest
from decimal import Decimal
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "apps/pearnly_core"), str(ROOT / "apps/pearnly_erp_portal")]

import cc_desktop_fake as fake  # noqa: E402

frappe = fake.install()
utils = sys.modules.setdefault("frappe.utils", types.ModuleType("frappe.utils"))
for name, value in (("nowdate", lambda: "2026-11-06"), ("now", lambda: "2026-11-06 10:00:00"),
                    ("flt", lambda v, *a: float(v or 0)), ("getdate", lambda v=None: v)):
    setattr(utils, name, getattr(utils, name, value))
frappe.utils = utils

from pearnly_erp_portal.express import stock_entry  # noqa: E402
from pearnly_erp_portal.inventory import access, documents  # noqa: E402


class Refused(Exception):
    pass


def refuse(message, *args, **kwargs):
    raise Refused(message)


class AdjustLines(unittest.TestCase):
    def lines(self, rows, *, visible=True, trusted=False, default=Decimal("12.50")):
        prepared = [{**row, "warehouse": "W1", "location": row.get("location", "")} for row in rows]
        with mock.patch.object(documents.guard, "cost_visible", lambda: visible), \
                mock.patch.object(documents, "default_unit_cost", lambda *a, **k: default), \
                mock.patch.object(documents.frappe, "throw", refuse), \
                mock.patch.object(documents, "_", lambda text: text):
            return documents._adjust_lines(prepared, rows, trusted=trusted)

    def test_zero_quantity_is_refused(self):
        with self.assertRaises(Refused) as raised:
            self.lines([{"product": "A", "qty": 0}])
        self.assertEqual(str(raised.exception), documents.ZERO_QTY)

    def test_a_decrease_keeps_no_price_and_no_hand_mark(self):
        [line] = self.lines([{"product": "A", "qty": -100, "unit_cost": 9, "unit_cost_manual": 1}])
        self.assertEqual((line["qty"], line["unit_cost"], line["unit_cost_manual"]), (Decimal(-100), None, False))

    def test_a_price_typed_by_a_person_who_sees_costs_is_kept_and_marked(self):
        [line] = self.lines([{"product": "A", "qty": 5, "unit_cost": 20, "unit_cost_manual": 1}])
        self.assertEqual((line["unit_cost"], line["unit_cost_manual"]), (20, True))

    def test_a_shown_default_price_is_kept_but_not_marked(self):
        [line] = self.lines([{"product": "A", "qty": 5, "unit_cost": 12.5, "unit_cost_manual": 0}])
        self.assertEqual((line["unit_cost"], line["unit_cost_manual"]), (12.5, False))

    def test_an_increase_without_a_price_takes_the_current_average(self):
        [line] = self.lines([{"product": "A", "qty": 5}])
        self.assertEqual((line["unit_cost"], line["unit_cost_manual"]), (Decimal("12.50"), False))

    def test_a_price_from_someone_who_cannot_see_costs_is_ignored(self):
        [line] = self.lines([{"product": "A", "qty": 5, "unit_cost": 999, "unit_cost_manual": 1}], visible=False)
        self.assertEqual((line["unit_cost"], line["unit_cost_manual"]), (Decimal("12.50"), False))

    def test_a_count_valuation_is_kept_and_never_counts_as_typed(self):
        [line] = self.lines([{"product": "A", "qty": 5, "unit_cost": 7, "unit_cost_manual": 1}], visible=False, trusted=True)
        self.assertEqual((line["unit_cost"], line["unit_cost_manual"]), (7, False))

    def test_mixed_signs_in_one_document(self):
        out = self.lines([{"product": "A", "qty": 3}, {"product": "B", "qty": -2}])
        self.assertEqual([line["qty"] for line in out], [Decimal(3), Decimal(-2)])


class OneWarehouse(unittest.TestCase):
    def test_lines_follow_the_header_warehouse(self):
        rows = documents._own_warehouse([{"product": "A", "warehouse": "W2"}, {"product": "B"}], "W1")
        self.assertEqual([row["warehouse"] for row in rows], ["W1", "W1"])

    def test_a_count_adjustment_keeps_goods_found_in_another_warehouse(self):
        rows = documents._own_warehouse([{"product": "A", "warehouse": "W2"}, {"product": "B"}], "W1", stock_count="STC-1")
        self.assertEqual([row["warehouse"] for row in rows], ["W2", "W1"])


class LineUnits(unittest.TestCase):
    """行上没写单位的补物料库存单位（11-07 线上 testerp：盘点生成的调整单草稿 IA2569-000001 单位一格空）。"""

    PRODUCTS = {"A": types.SimpleNamespace(name="A", base_unit="ชิ้น", unit="กล่อง"),
                "B": types.SimpleNamespace(name="B", base_unit="", unit="ขวด")}

    def get_all(self, doctype, filters=None, **kwargs):
        self.asked.append((doctype, list(filters["name"][1])))
        return [self.PRODUCTS[name] for name in filters["name"][1] if name in self.PRODUCTS]

    def setUp(self):
        self.asked = []
        patcher = mock.patch.object(documents.frappe, "get_all", self.get_all, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_generated_lines_get_the_stock_unit(self):
        rows = documents._with_units([{"product": "A", "qty": -2}, {"product": "B", "qty": 1, "unit": ""}])
        self.assertEqual([row["unit"] for row in rows], ["ชิ้น", "ขวด"])

    def test_a_unit_on_the_line_is_kept(self):
        rows = documents._with_units([{"product": "A", "qty": 1, "unit": "กล่อง"}])
        self.assertEqual(rows[0]["unit"], "กล่อง")
        self.assertEqual(self.asked, [])

    def test_old_lines_without_a_unit_show_the_stock_unit(self):
        self.assertEqual(documents.stock_units(["A", "B", "A", None]), {"A": "ชิ้น", "B": "ขวด"})
        self.assertEqual(self.asked, [("ERP Product", ["A", "B"])])


class Kinds(unittest.TestCase):
    def test_old_count_entries_belong_to_adjustments(self):
        self.assertEqual(access.entry_kind("in", "count"), "adjust")
        self.assertEqual(access.entry_kind("out", "count"), "adjust")
        self.assertEqual(access.entry_kind("adjust"), "adjust")
        self.assertEqual(access.entry_kind("in", "purchase"), "in")
        self.assertEqual(access.entry_kind("out", None), "out")

    def query(self, held):
        with mock.patch.object(access.guard, "has", lambda capability, user=None: capability in held), \
                mock.patch.object(access.company, "active_company", lambda user=None: "CO-1"), \
                mock.patch.object(access.frappe.db, "escape", lambda value: f"'{value}'", create=True):
            return access.entry_query("u@x")

    def test_in_out_lists_leave_old_count_entries_out(self):
        sql = self.query({"stock.in", "stock.out"})
        self.assertIn("ifnull(`tabERP Stock Entry`.`ref_type`, '') != 'count'", sql)
        self.assertNotIn("'adjust'", sql)

    def test_adjust_list_takes_old_count_entries(self):
        sql = self.query({"stock.adjust"})
        self.assertIn("`tabERP Stock Entry`.`ref_type` = 'count'", sql)
        self.assertIn("`direction` = 'adjust'", sql)

    def test_count_staff_see_only_adjustments_made_from_counts(self):
        sql = self.query({"count.write"})
        self.assertIn("`stock_count`, '') != ''", sql)
        self.assertNotIn("ref_type` = 'count'", sql)

    def test_nobody_else_sees_entries(self):
        self.assertEqual(self.query(set()), "1=0")


class ExpressTk(unittest.TestCase):
    def line(self, qty, *, code="X", loccod="L1", manual=None):
        item = {"code": code, "name": code, "qty": str(qty), "unit": "pc", "loccod": loccod, "product": "P-" + code}
        if manual is not None:
            item["manual_cost"] = Decimal(manual)
        return item

    def test_a_manual_adjustment_is_ju_and_a_count_adjustment_is_tk(self):
        self.assertEqual(stock_entry.kind({"direction": "adjust"}), stock_entry.ADJUST)
        self.assertEqual(stock_entry.kind({"direction": "adjust", "stock_count": "STC-1"}), stock_entry.COUNT)
        self.assertEqual(stock_entry.kind({"direction": "in", "ref_type": "count"}), stock_entry.COUNT)
        self.assertEqual(stock_entry.kind({"direction": "in"}), stock_entry.ADJUST)

    def test_a_location_move_in_one_warehouse_nets_to_nothing(self):
        self.assertEqual(stock_entry._net([self.line(-5), self.line(5)]), [])

    def test_only_a_typed_price_goes_to_express(self):
        [typed] = stock_entry._net([self.line(4, manual="15")])
        [default] = stock_entry._net([self.line(4)])
        self.assertEqual(typed["unit_cost"], "15")
        self.assertNotIn("unit_cost", default)
        self.assertNotIn("manual_cost", typed)

    def test_a_mixed_or_conflicting_price_is_left_to_express(self):
        [mixed] = stock_entry._net([self.line(2, manual="15"), self.line(3)])
        [conflict] = stock_entry._net([self.line(2, manual="15"), self.line(3, manual="16")])
        self.assertEqual(mixed["qty"], "5")
        self.assertNotIn("unit_cost", mixed)
        self.assertNotIn("unit_cost", conflict)

    def test_decreases_and_other_warehouses_stay_apart(self):
        out = stock_entry._net([self.line(-100), self.line(3, loccod="L2", manual="9")])
        self.assertEqual([(row["qty"], row["loccod"], row.get("unit_cost")) for row in out],
                         [("-100", "L1", None), ("3", "L2", "9")])

    def test_the_form_preflight_asks_about_the_same_book(self):
        with mock.patch.object(stock_entry.frappe.db, "get_value", lambda *a, **k: None, create=True):
            manual = stock_entry.form_body({"direction": "adjust", "posting_date": "2026-11-06", "lines": []})
            counted = stock_entry.form_body({"direction": "adjust", "stock_count": "STC-1", "posting_date": "2026-11-06",
                                             "lines": []})
        self.assertEqual((manual["doctype"], counted["doctype"]), (stock_entry.ADJUST, stock_entry.COUNT))


class IssueBook(unittest.TestCase):
    """出库按用途选领用簿（D12）：内部领用 OU、报损 ZZ、样品 XS、生产领料 PD，其他 / 不填 OU；这份账套没有那一本 → 问一次（选项是
    这份账套领用类的每一本），选过的那本还在就用它；CC 桌面版没报簿清单（旧版）时照默认前缀交给电脑。"""
    LISTED = [{"prefix": "OU", "doctype": "OU", "name": "จ่ายสินค้าใช้ภายใน"}, {"prefix": "ZZ", "doctype": "OU", "name": "ตัดสินค้าชำรุด"},
              {"prefix": "JU", "doctype": "JU", "name": "ปรับปรุงเพิ่ม/ลดสินค้า"}]

    def book(self, purpose, listed=LISTED, chosen=""):
        body = {"doctype": stock_entry.ISSUE, "purpose": purpose, "number": "IS2569-000001"}
        data = {"facts": {"stock_book_list": listed}} if listed is not None else {"facts": {}}
        with mock.patch.object(stock_entry.choices, "chosen", lambda *a: chosen):
            return stock_entry._book(body, data, "CO-1")

    def test_a_purpose_picks_its_book(self):
        self.assertEqual(self.book("damage"), ("ZZ", None))
        self.assertEqual(self.book("internal_use"), ("OU", None))
        self.assertEqual(self.book(""), ("OU", None))
        self.assertEqual(self.book("other"), ("OU", None))

    def test_a_missing_book_is_asked_once_among_this_ledgers_issue_books(self):
        prefix, asked = self.book("sample")
        self.assertEqual(asked["kind"], stock_entry.STOCK_BOOK)
        self.assertEqual(asked["subject"], "stock_book:sample")
        self.assertEqual([row["code"] for row in asked["options"]], ["OU", "ZZ"])  # 只列领用类
        self.assertEqual(self.book("sample", chosen="ZZ"), ("ZZ", None))
        self.assertEqual(self.book("sample", chosen="XX")[1]["kind"], stock_entry.STOCK_BOOK)  # 选过的那本不在了：重新问

    def test_an_old_cc_desktop_without_the_list_gets_the_default_prefix(self):
        self.assertEqual(self.book("production", listed=None), ("PD", None))

    def test_other_doctypes_keep_their_own_book(self):
        with mock.patch.object(stock_entry.choices, "chosen", lambda *a: ""):
            self.assertEqual(stock_entry._book({"doctype": stock_entry.ADJUST}, {}, "CO-1"), ("JU", None))


if __name__ == "__main__":
    unittest.main()
