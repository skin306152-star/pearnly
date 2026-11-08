"""收发存导出（照 MR.ERP 三种报表）：真代码 + 只读的假站点查询，期望值按 MR.ERP 源码算法手算。

评估：docs/work/2026-11-08-stock-card-export-评估.md。MR.ERP 算法（report/50/all/57.php、51/59.php、52/198.php）：
结余由报表逐笔滚（数量、金额相加减），结余单价 = 数量 0 → 0，否则 round(金额 / 数量, 2)；汇总收 / 发单价 = round(和金额 / 和数量, 2)；
小计合计不带单价；同一天先收后发；结转 ≠ 0 或期内有行的物料才出现。

账（物料 P1 = 代码 A001，两个仓 W1 / W2；期间 2026-10-01 ~ 2026-10-31），成本引擎按仓位滚移动平均：
  09-10 W1 采购入 10 @5.00 = 50.00；09-20 W1 销售出 4（@5.00 = 20.00）→ 期初 W1 6 / 30.00
  10-03 09:00 W1 出库 2（@5.00 = 10.00）；10-03 10:00 W1 采购入 6 @6.00 = 36.00（W1 均价 → 5.60）
  10-10 调拨 TR-1：W1 调出 5（@5.60 = 28.00）、W2 调入 5 @5.60 = 28.00
  10-15 W2 销售出 2（@5.60 = 11.20）；10-20 W2 采购入 3 @7.00 = 21.00；11-02 W1 采购入（区间外）
合并（手算）：期初 6 / 5.00 / 30.00
  10-03 收 6 @6.00 36.00 → 12 / 5.50 / 66.00；10-03 发 2 @5.00 10.00 → 10 / 5.60 / 56.00（同一天先收后发）
  10-10 收 5 @5.60 28.00 → 15 / 5.60 / 84.00；10-10 发 5 @5.60 28.00 → 10 / 5.60 / 56.00
  10-15 发 2 @5.60 11.20 → 8 / 5.60 / 44.80；10-20 收 3 @7.00 21.00 → 11 / 5.98 / 65.80（65.80 / 11 = 5.9818）
  收 14 / 85.00，发 9 / 49.20；汇总单价 收 85 / 14 = 6.07、发 49.2 / 9 = 5.47
"""
from datetime import date, datetime
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/inventory"
sys.path.insert(0, str(DOMAIN.parents[2] / "pearnly_core"))  # pearnly_core.desk.list_filters（纯函数）
D = Decimal


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def thai_texts() -> dict:
    texts = {}
    for line in (DOMAIN.parent / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 5 and parts[4].startswith("trade.export."):
            texts[parts[4]] = parts[2]
    return texts


class Rows(unittest.TestCase):
    """纯算法（不依赖站点）。"""

    def setUp(self):
        self.rows = load("card_rows_pure", DOMAIN / "card_rows.py")

    def move(self, day, doc, qty, price, amount, seq, position=("W1", "")):
        return self.rows.Movement(day, doc, D(qty), None if price is None else D(price),
                                  None if amount is None else D(amount), seq, position)

    def test_zero_balance_price_is_zero_and_totals_carry_no_price(self):
        item = self.rows.block([self.rows.Opening(("W1", ""), D(2), D("3.00"))],
                               [self.move("2026-10-01", "S1", -2, "1.50", "3.00", 0)])
        self.assertEqual((item.closing_qty, item.closing_price, item.closing_amount), (D(0), D(0), D("0.00")))
        self.assertEqual((item.out_qty, item.out_amount, item.out_price), (D(2), D("3.00"), D("1.50")))
        self.assertEqual(item.in_price, D(0))  # 没有收：数量 0 → 单价 0

    def test_unknown_cost_blanks_amounts_until_established(self):
        item = self.rows.block([], [self.move("2026-10-01", "R1", 3, None, None, 0),
                                    self.move("2026-10-02", "P1", 2, "4.00", "8.00", 1)])
        first, second = item.lines
        self.assertEqual((first.bal_qty, first.bal_amount, first.bal_price), (D(3), None, None))
        self.assertEqual((second.bal_qty, second.bal_amount, second.bal_price), (D(5), D("8.00"), D("1.60")))
        self.assertIsNone(item.in_amount)       # 有一笔未知，小计金额整格留空
        self.assertIsNone(item.in_price)

    def test_hidden_costs_keep_quantities(self):
        item = self.rows.block([self.rows.Opening(("W1", ""), D(1), D("5.00"))],
                               [self.move("2026-10-01", "P1", 2, "4.00", "8.00", 0)], costs=False)
        self.assertEqual((item.opening_qty, item.opening_amount, item.closing_qty, item.closing_amount),
                         (D(1), None, D(3), None))
        self.assertEqual((item.lines[0].unit_price, item.lines[0].amount, item.lines[0].bal_price), (None, None, None))

    def test_shown_rules(self):
        empty = self.rows.block([self.rows.Opening(("W1", ""), D(0), D("0.00"))], [])
        self.assertFalse(empty.shown_in_detail())
        self.assertFalse(empty.shown_in_summary())
        carried = self.rows.block([self.rows.Opening(("W1", ""), D(5), D("10.00"))], [])
        self.assertTrue(carried.shown_in_detail())
        self.assertTrue(carried.shown_in_summary())

    def test_ranges_ignore_case_and_open_ends(self):
        self.assertTrue(self.rows.in_range("a002", "A001", "A003"))
        self.assertFalse(self.rows.in_range("B001", "A001", "A999"))
        self.assertTrue(self.rows.in_range("Z", "", None))


class Export(unittest.TestCase):
    """导出模块跑在假站点上（流水、物料、仓库只读）；成本引擎、报表取数都是真代码。"""

    def setUp(self):
        self.movements, self.required = [], []
        self.cost_visible = True
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        self.contracts = load("card_export_contracts", DOMAIN / "contracts.py")
        texts = thai_texts()
        frappe = ModuleType("frappe")
        frappe._ = lambda value: texts.get(value, value)
        frappe._dict = SimpleNamespace
        frappe.local = SimpleNamespace(lang="th")
        frappe.throw = self.throw
        frappe.get_list = self.get_list
        frappe.get_all = self.get_all
        frappe.db = SimpleNamespace(get_value=self.get_value, count=self.count)
        utils = ModuleType("frappe.utils")
        utils.getdate = lambda value: value if isinstance(value, date) else date.fromisoformat(str(value))
        utils.nowdate = lambda: "2026-11-08"
        utils.now_datetime = lambda: datetime(2026, 11, 8, 14, 30, 5)
        frappe.utils = utils
        access = ModuleType("pearnly_erp_portal.access")
        access.guard = SimpleNamespace(require=self.required.append, cost_visible=lambda: self.cost_visible)
        access.listing = load("card_export_listing", DOMAIN.parent / "access/listing.py")
        inventory = ModuleType("pearnly_erp_portal.inventory")
        inventory.contracts = self.contracts
        inventory.warehouses = SimpleNamespace(
            resolve=lambda value, **kwargs: value, scope_filter=lambda: {"warehouse": ["in", ["W1", "W2"]]},
            code_of=lambda row: (row.get("express_code") or row.get("warehouse_code") or "").strip())
        masters = ModuleType("pearnly_erp_portal.masters")
        masters.company = SimpleNamespace(active_company=lambda: "CO1")
        sys.modules.update({"frappe": frappe, "frappe.utils": utils, "pearnly_erp_portal.access": access,
                            "pearnly_erp_portal.inventory": inventory, "pearnly_erp_portal.masters": masters,
                            "pearnly_erp_portal.masters.company": masters.company})
        self.report = load("pearnly_erp_portal.inventory.report", DOMAIN / "report.py")
        self.report._position_sums = self.position_sums
        inventory.report = self.report
        inventory.card_rows = load("pearnly_erp_portal.inventory.card_rows", DOMAIN / "card_rows.py")
        self.export = load("pearnly_erp_portal.inventory.card_export", DOMAIN / "card_export.py")
        self.products = {"P1": ("A001", "ปากกา", "ด้าม"), "P2": ("A002", "ดินสอ", "แท่ง"),
                         "P3": ("A003", "ยางลบ", "ก้อน"), "P4": ("B001", "ไม้บรรทัด", "อัน")}
        self.stores = {"W1": ("WH01", "คลังใหญ่"), "W2": ("WH02", "คลังสาขา")}
        self.book()

    # ── 假站点 ──
    def throw(self, message, *args):
        raise ValueError(message)

    def holds(self, row, condition):
        key, operation, value = condition
        have = str(row.get(key)) if key == "posting_date" else row.get(key)
        return {"=": lambda: have == value, "in": lambda: have in value, "<=": lambda: have <= value,
                ">=": lambda: have >= value, "<": lambda: have < value}[operation]()

    def conditions(self, filters):
        return [[key, *value] if isinstance(value, list) else [key, "=", value] for key, value in filters.items()] \
            if isinstance(filters, dict) else filters

    def get_list(self, doctype, filters, fields, **kwargs):
        self.assertEqual(doctype, "ERP Stock Movement")
        rows = [row for row in self.movements if all(self.holds(row, c) for c in self.conditions(filters))]
        return sorted(rows, key=lambda row: (str(row["posting_date"]), row["posting_time"], row["index"]))

    def get_all(self, doctype, filters, fields, limit_page_length=None, **kwargs):
        names = filters["name"][1]
        self.assertTrue(limit_page_length and limit_page_length >= len(names))
        if doctype == "ERP Product":
            return [{"name": n, "code": self.products[n][0], "express_code": "", "product_name": self.products[n][1],
                     "unit": self.products[n][2], "base_unit": self.products[n][2]} for n in names if n in self.products]
        self.assertEqual(doctype, "ERP Warehouse")
        return [{"name": n, "warehouse_code": self.stores[n][0], "express_code": "", "warehouse_name": self.stores[n][1]}
                for n in names if n in self.stores]

    def get_value(self, doctype, name, fields, as_dict=False):
        self.assertEqual((doctype, name), ("ERP Company", "CO1"))
        return SimpleNamespace(company_name="บริษัท ทดสอบ จำกัด", tax_id="0105555000001")

    def count(self, doctype, filters):
        return len([row for row in self.movements if all(self.holds(row, c) for c in filters)])

    def position_sums(self, query, date_from):
        out = {}
        for row in self.report._movements(**query):
            out[(row["product"], row["warehouse"], row.get("location") or "")] = {}
        return out

    def add(self, day, time, product, warehouse, qty, cost=None, kind="stock_in", doc=None):
        self.movements.append({"name": f"STM-{len(self.movements):05d}", "product": product, "warehouse": warehouse,
            "location": "", "posting_date": date.fromisoformat(day), "posting_time": time, "index": len(self.movements),
            "movement_type": kind, "ref_type": None, "ref_id": None, "doc_no": doc or f"D{len(self.movements)}",
            "qty": abs(qty), "qty_base": qty, "unit": "pcs", "unit_cost": cost or 0, "cost_known": int(cost is not None),
            "reason": ""})

    def book(self):
        self.add("2026-09-10", "10:00:00", "P1", "W1", 10, 5, "purchase_in", "PI-01")
        self.add("2026-09-20", "10:00:00", "P1", "W1", -4, None, "sale_out", "SI-01")
        self.add("2026-10-03", "09:00:00", "P1", "W1", -2, None, "stock_out", "IS-01")
        self.add("2026-10-03", "10:00:00", "P1", "W1", 6, 6, "purchase_in", "PI-02")
        self.add("2026-10-10", "10:00:00", "P1", "W1", -5, None, "transfer_out", "TR-1")
        self.add("2026-10-10", "10:00:00", "P1", "W2", 5, "5.60", "transfer_in", "TR-1")
        self.add("2026-10-15", "10:00:00", "P1", "W2", -2, None, "sale_out", "SI-02")
        self.add("2026-10-20", "10:00:00", "P1", "W2", 3, 7, "purchase_in", "PI-03")
        self.add("2026-11-02", "10:00:00", "P1", "W1", 9, 9, "purchase_in", "PI-04")
        self.add("2026-10-05", "10:00:00", "P2", "W1", 3, None, "stock_in", "IR-01")      # 成本从未确立
        self.add("2026-09-01", "10:00:00", "P3", "W1", 2, 1, "purchase_in", "PI-05")
        self.add("2026-09-02", "10:00:00", "P3", "W1", -2, None, "sale_out", "SI-03")    # 期初 0 / 0：不出现
        self.add("2026-09-05", "10:00:00", "P4", "W2", 5, 2, "purchase_in", "PI-06")     # 只有结转

    def params(self, report, **kwargs):
        return self.export._params(report=report, format="xlsx", date_from="2026-10-01", date_to="2026-10-31", **kwargs)

    def layout(self, report, **kwargs):
        params = self.params(report, **kwargs)
        return self.export._layout(params, self.export._selection(params), costs=self.cost_visible)

    @staticmethod
    def values(row):
        return [cell["value"] for cell in row["cells"]]

    # ── 合并全部仓（57.php）──
    def test_all_warehouses_detail_matches_hand_computed_mrerp(self):
        layout = self.layout("all", product_from="P1", product_to="P1")
        self.assertEqual(self.required, ["report.read"])
        self.assertEqual(layout["lines"], [("บริษัท ทดสอบ จำกัด", ""), ("รายงานรับ-จ่ายสินค้า", "เลขประจำตัวผู้เสียภาษี  0105555000001"),
                                           ("วันที่  01/10/2569  ถึง  31/10/2569", ""), ("รหัสสินค้า  A001  ถึง  A001", "")])
        body = layout["body"]
        self.assertEqual(self.values(body[0]), ["A001  ปากกา", "หน่วย : ด้าม"])
        self.assertEqual(self.values(body[1]), ["ยอดยกมา", None, D(6), D("5.00"), D("30.00")])
        lines = [self.values(row) for row in body[2:8]]
        self.assertEqual(lines, [
            ["03/10/2569", "PI-02", D(6), D("6.00"), D("36.00"), None, None, None, D(12), D("5.50"), D("66.00")],
            ["03/10/2569", "IS-01", None, None, None, D(2), D("5.00"), D("10.00"), D(10), D("5.60"), D("56.00")],
            ["10/10/2569", "TR-1", D(5), D("5.60"), D("28.00"), None, None, None, D(15), D("5.60"), D("84.00")],
            ["10/10/2569", "TR-1", None, None, None, D(5), D("5.60"), D("28.00"), D(10), D("5.60"), D("56.00")],
            ["15/10/2569", "SI-02", None, None, None, D(2), D("5.60"), D("11.20"), D(8), D("5.60"), D("44.80")],
            ["20/10/2569", "PI-03", D(3), D("7.00"), D("21.00"), None, None, None, D(11), D("5.98"), D("65.80")]])
        self.assertEqual(self.values(body[8]), ["รวม  6  รายการ", D(14), None, D("85.00"), D(9), None, D("49.20"),
                                                None, None, None])
        self.assertEqual(body[9]["kind"], "gap")
        self.assertEqual(self.values(body[10]), ["รวมทั้งสิ้น  1  สินค้า", D(14), None, D("85.00"), D(9), None,
                                                 D("49.20"), D(11), None, D("65.80")])

    def test_all_warehouses_lists_products_by_code_and_skips_empty(self):
        body = self.layout("all")["body"]
        titles = [row["cells"][0]["value"] for row in body if row["kind"] == "product"]
        self.assertEqual(titles, ["A001  ปากกา", "A002  ดินสอ", "B001  ไม้บรรทัด"])  # A003 结转 0、期内无行
        grand = body[-1]
        self.assertEqual(self.values(grand)[0], "รวมทั้งสิ้น  3  สินค้า")
        self.assertIsNone(self.values(grand)[3])       # A002 成本未知：合计收金额留空，不当 0

    # ── 汇总（59.php）──
    def test_summary_one_row_per_product(self):
        body = self.layout("summary")["body"]
        self.assertEqual(self.values(body[0]), ["A001", "ปากกา", D(6), D("5.00"), D("30.00"), D(14), D("6.07"), D("85.00"),
                                                D(9), D("5.47"), D("49.20"), D(11), D("5.98"), D("65.80")])
        self.assertEqual(self.values(body[1]), ["A002", "ดินสอ", D(0), D(0), D("0.00"), D(3), None, None,
                                                D(0), D(0), D("0.00"), D(3), None, None])
        self.assertEqual(self.values(body[2]), ["B001", "ไม้บรรทัด", D(5), D("2.00"), D("10.00"), D(0), D(0), D("0.00"),
                                                D(0), D(0), D("0.00"), D(5), D("2.00"), D("10.00")])
        self.assertEqual(self.values(body[3])[:2], ["รวมทั้งสิ้น  3  สินค้า", D(11)])
        self.assertEqual(len(body), 4)

    # ── 分仓（198.php）──
    def test_by_warehouse_groups_store_then_product(self):
        body = self.layout("by_warehouse", product_from="P1", product_to="P1")["body"]
        kinds = [row["kind"] for row in body]
        self.assertEqual(kinds, ["store", "product", "carried", "line", "line", "line", "subtotal", "note", "store-total",
                                 "note", "gap", "store", "product", "carried", "line", "line", "line", "subtotal", "note",
                                 "store-total", "note", "gap", "grand", "note"])
        self.assertEqual(self.values(body[0]), ["WH01  คลังใหญ่"])
        self.assertEqual(self.values(body[5])[8:], [D(5), D("5.60"), D("28.00")])
        self.assertEqual(self.values(body[6]), ["รวมสินค้า", D(6), None, D("36.00"), D(7), None, D("38.00"), None, None, None])
        self.assertEqual(self.values(body[7]), ["รายละเอียดรวมสินค้า  3  รายการ"])
        self.assertEqual(self.values(body[8]), ["รวมคลัง", D(6), None, D("36.00"), D(7), None, D("38.00"), D(5), None,
                                                D("28.00")])
        self.assertEqual(self.values(body[9]), ["รายละเอียดรวมคลัง  3  รายการ  1  สินค้า"])
        self.assertEqual(self.values(body[13]), ["ยอดยกมา", None, D(0), D(0), D("0.00")])
        self.assertEqual(self.values(body[16])[8:], [D(6), D("6.30"), D("37.80")])
        self.assertEqual(self.values(body[22]), ["รวมทุกคลัง", D(14), None, D("85.00"), D(9), None, D("49.20"), D(11), None,
                                                 D("65.80")])
        self.assertEqual(self.values(body[23]), ["รายละเอียดรวมทุกคลัง  6  รายการ  2  สินค้า  2  คลัง"])

    def test_by_warehouse_range_keeps_one_store(self):
        layout = self.layout("by_warehouse", warehouse_from="W2", warehouse_to="W2")
        self.assertEqual(layout["lines"][2], ("รหัสคลังสินค้า  WH02  ถึง  WH02", ""))
        stores = [row["cells"][0]["value"] for row in layout["body"] if row["kind"] == "store"]
        self.assertEqual(stores, ["WH02  คลังสาขา"])

    # ── 权限、参数、语言 ──
    def test_hidden_costs_blank_every_amount(self):
        self.cost_visible = False
        row = self.layout("summary")["body"][0]
        self.assertEqual(self.values(row)[2:5], [D(6), None, None])

    def test_rejects_bad_ranges(self):
        for kwargs in ({"report": "x", "format": "xlsx"}, {"report": "all", "format": "doc"}):
            with self.assertRaises(ValueError):
                self.export._params(date_from="2026-10-01", date_to="2026-10-31", **kwargs)
        with self.assertRaises(ValueError):
            self.export._params(report="all", format="xlsx", date_from="2026-10-31", date_to="2026-10-01")
        with self.assertRaises(ValueError):
            self.layout("all", product_from="P4", product_to="P1")

    def test_size_counts_period_rows_and_products(self):
        self.assertEqual(self.export.size(report="summary", format="pdf", date_from="2026-10-01", date_to="2026-10-31"), 4)
        self.assertEqual(self.export.size(report="all", format="pdf", date_from="2026-10-01", date_to="2026-10-31",
                                          product_from="P1", product_to="P1"), 6 + 1)

    def test_gregorian_dates_outside_thai(self):
        self.export.frappe.local.lang = "zh"
        self.assertEqual(self.layout("all", product_from="P1", product_to="P1")["body"][2]["cells"][0]["value"], "03/10/2026")
        self.assertEqual(self.export._filename("all", "pdf"), "รายงานรับ-จ่ายสินค้า [08112026_143005].pdf")
        self.export.frappe.local.lang = "th"
        self.assertEqual(self.export._filename("by_warehouse", "xlsx"),
                         "รายงานรับ-จ่ายสินค้า - แยกคลัง [08112569_143005].xlsx")

    def test_pdf_html_repeats_header_and_escapes(self):
        self.products["P1"] = ("A001", "<b>ปากกา</b>", "ด้าม")
        printing = ModuleType("pearnly_erp_portal.printing")
        printing.context = SimpleNamespace(fonts_css=lambda: "", FONT_FAMILY="PearnlyPrint")
        with patch.dict(sys.modules, {"pearnly_erp_portal.printing": printing}):
            text = self.export._html(self.layout("all", product_from="P1", product_to="P1"))
        self.assertIn("<thead>", text)
        self.assertIn("รายงานรับ-จ่ายสินค้า", text.split("<tbody>")[0])   # 表头在 thead 里，每页重复
        self.assertIn("&lt;b&gt;ปากกา&lt;/b&gt;", text)
        self.assertIn("65.80", text)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl 在生产镜像里（候选检查跑）")
    def test_xlsx_cells_read_back(self):
        from io import BytesIO
        from openpyxl import load_workbook

        sheet = load_workbook(BytesIO(self.export._xlsx(self.layout("summary")))).active
        self.assertEqual(sheet["A1"].value, "บริษัท ทดสอบ จำกัด")
        self.assertEqual(sheet["K2"].value, "เลขประจำตัวผู้เสียภาษี  0105555000001")
        self.assertEqual((sheet["C5"].value, sheet["F5"].value, sheet["C6"].value), ("ยอดยกมา", "มูลค่ารับ", "จำนวน"))
        self.assertEqual([sheet.cell(7, column).value for column in range(1, 15)],
                         ["A001", "ปากกา", 6, 5, 30, 14, 6.07, 85, 9, 5.47, 49.2, 11, 5.98, 65.8])
        self.assertEqual(sheet["G7"].number_format, "#,##0.00")
        self.assertIn("A5:A6", {str(merged) for merged in sheet.merged_cells.ranges})
        self.assertEqual(sheet.page_setup.orientation, "portrait")
        self.assertEqual(sheet.oddHeader.right.text, "&P/&N")


if __name__ == "__main__":
    unittest.main()
