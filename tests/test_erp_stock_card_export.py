"""收发存导出（照 MR.ERP 三种报表）：真代码 + 只读的假站点查询，期望值按 MR.ERP 源码算法手算。

评估：docs/work/2026-11-08-stock-card-export-评估.md。MR.ERP 算法（report/50/all/57.php、51/59.php、52/198.php）：
结余由报表逐笔滚（数量、金额相加减），结余单价 = 数量 0 → 0，否则 round(金额 / 数量, 2)；汇总收 / 发单价 = round(和金额 / 和数量, 2)；
小计合计不带单价；同一天先收后发；结转 ≠ 0 或期内有行的物料才出现。

账（物料 P1 = 代码 A001，两个仓 W1 / W2；期间 2026-10-01 ~ 2026-10-31）。行上的单价、金额照 MR.ERP 成本引擎
（component/php/stkcardpc.php，主会话 11-09）：一个物料全公司一个移动平均池，按报表顺序（日期 → 同日先收后发 → 单号）滚：
  09-10 W1 采购入 10 @5.00 = 50.00 → 池 10 / 50.00 / 5.00；09-20 W1 销售出 4 @5.00 = 20.00 → 6 / 30.00 / 5.00（期初）
  10-03 W1 采购入 6 @6.00 = 36.00 → 12 / 66.00 / 5.50；10-03 W1 出库 2 @5.50 = 11.00 → 10 / 55.00 / 5.50
  （真实时间是先出后进，MR.ERP 同一天先收后发，出库按 5.50）
  10-10 调拨 TR-1：W2 调入 5 按池均价 @5.50 = 27.50 → 15 / 82.50；W1 调出 5 @5.50 = 27.50 → 10 / 55.00（同池相抵）
  10-15 W2 销售出 2 @5.50 = 11.00 → 8 / 44.00；10-20 W2 采购入 3 @7.00 = 21.00 → 11 / 65.00 / 5.91；11-02（区间外）
合并：期初 6 / 5.00 / 30.00；收 14 / 84.50，发 9 / 49.50，期末 11 / 5.91 / 65.00；汇总单价 收 84.5 / 14 = 6.04、发 49.5 / 9 = 5.50
分仓：W1 期初 6 / 30.00，收 6 / 36.00，发 7 / 38.50，期末 5 / 27.50；W2 收 8 / 48.50，发 2 / 11.00，期末 6 / 6.25 / 37.50
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


LANGUAGES = ("en", "zh", "th", "ja")


def glossary() -> dict:
    """术语表里 `trade.export.*` 每条四种语言的写法：{键: {语言: 文字}}。"""
    texts = {}
    for line in (DOMAIN.parent / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 5 and parts[4].startswith("trade.export."):
            texts[parts[4]] = dict(zip(LANGUAGES, parts[:4]))
    return texts


# MR.ERP 模板里的泰文原文（report/50/all/57.php、51/59.php、52/198.php；评估 §A-7 ~ §A-9）
MRERP_THAI = {
    "trade.export.title.all": "รายงานรับ-จ่ายสินค้า", "trade.export.title.summary": "รายงานรับ-จ่ายสินค้า - สรุป",
    "trade.export.title.byWarehouse": "รายงานรับ-จ่ายสินค้า:แยกคลัง", "trade.export.taxId": "เลขประจำตัวผู้เสียภาษี  {0}",
    "trade.export.dateRange": "วันที่  {0}  ถึง  {1}", "trade.export.productRange": "รหัสสินค้า  {0}  ถึง  {1}",
    "trade.export.warehouseRange": "รหัสคลังสินค้า  {0}  ถึง  {1}", "trade.export.voucher": "ใบสำคัญ",
    "trade.export.receipts": "รายการรับ", "trade.export.issues": "รายการจ่าย", "trade.export.balance": "คงเหลือ",
    "trade.export.date": "วันที่", "trade.export.number": "เลขที่", "trade.export.qty": "จำนวน",
    "trade.export.unitPrice": "ราคาต่อหน่วย", "trade.export.receiptValue": "มูลค่ารับ", "trade.export.issueValue": "มูลค่าจ่าย",
    "trade.export.balanceValue": "มูลค่าคงเหลือ", "trade.export.amount": "จำนวนเงิน", "trade.export.carried": "ยอดยกมา",
    "trade.export.code": "รหัสสินค้า", "trade.export.name": "ชื่อสินค้า", "trade.export.unit": "หน่วย : {0}",
    "trade.export.rows": "รวม  {0}  รายการ", "trade.export.grand": "รวมทั้งสิ้น  {0}  สินค้า",
    "trade.export.productTotal": "รวมสินค้า", "trade.export.productDetail": "รายละเอียดรวมสินค้า  {0}  รายการ",
    "trade.export.warehouseTotal": "รวมคลัง", "trade.export.warehouseDetail": "รายละเอียดรวมคลัง  {0}  รายการ  {1}  สินค้า",
    "trade.export.allTotal": "รวมทุกคลัง",
    "trade.export.allDetail": "รายละเอียดรวมทุกคลัง  {0}  รายการ  {1}  สินค้า  {2}  คลัง",
    # 筛选校验（rpstkmm/component/javascript/rpstkmmform.js、rpstkmmstoreform.js 的 alert 原文）
    "trade.export.error.dateRequired": 'กรุณาเลือก "วันที่" ให้ครบ',
    "trade.export.error.dateOrder": '"วันที่" ต้องเรียงลำดับจากน้อยไปมาก',
    "trade.export.error.productOrder": '"รหัสสินค้า" ต้องเรียงลำดับจากน้อยไปมาก',
    "trade.export.error.warehouseOrder": '"รหัสคลังสินค้า" ต้องเรียงลำดับจากน้อยไปมาก',
}


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
        texts = glossary()
        frappe = ModuleType("frappe")
        # 按导出那一刻的语言取词（与 Frappe 的 `_()` 一样读 `frappe.local.lang`）
        self.texts = lambda value: texts.get(value, {}).get((frappe.local.lang or "")[:2], value)
        frappe._ = self.texts
        frappe._dict = SimpleNamespace
        frappe.local = SimpleNamespace(lang="th")
        frappe.throw = self.throw
        frappe.get_list = self.get_list
        frappe.get_all = self.get_all
        frappe.db = SimpleNamespace(get_value=self.get_value, count=self.count, sql=self.sql)
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
            names_of=lambda company=None: sorted(self.stores),
            code_of=lambda row: (row.get("express_code") or row.get("warehouse_code") or "").strip(),
            # 与真函数同口径（仓库页「รหัสคลัง」那一栏）：Express 码 → 手填编号 → 系统号
            display_code=lambda row: (row.get("express_code") or row.get("warehouse_code") or row.get("name") or "").strip())
        products = ModuleType("pearnly_erp_portal.products")
        products.categories = SimpleNamespace(names=lambda values: {}, spec=lambda label, attribute: "")
        masters = ModuleType("pearnly_erp_portal.masters")
        masters.company = SimpleNamespace(active_company=lambda: "CO1")
        sys.modules.update({"frappe": frappe, "frappe.utils": utils, "pearnly_erp_portal.access": access,
                            "pearnly_erp_portal.inventory": inventory, "pearnly_erp_portal.masters": masters,
                            "pearnly_erp_portal.masters.company": masters.company,
                            "pearnly_erp_portal.products": products})
        inventory.card_rows = load("pearnly_erp_portal.inventory.card_rows", DOMAIN / "card_rows.py")
        self.report = load("pearnly_erp_portal.inventory.report", DOMAIN / "report.py")
        self.report._position_sums = self.position_sums
        inventory.report = self.report
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
        if doctype == "ERP Product":   # 页面的商品名（`report._product_names`）
            return [{"name": n, "product_name": self.products[n][1], "unit": self.products[n][2],
                     "base_unit": self.products[n][2], "category": None, "attribute": None}
                    for n in filters["name"][1] if n in self.products]
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
        if doctype == "ERP Warehouse":   # 页面顶部的仓库名
            return self.stores[name][1]
        self.assertEqual((doctype, name), ("ERP Company", "CO1"))
        return SimpleNamespace(company_name="บริษัท ทดสอบ จำกัด", tax_id="0105555000001")

    def sql(self, query, values=None, **kwargs):
        """物料主档的首末代码（当前公司看得到的：本地档 + 本公司 Express 档）；按库的排序规则，这里用不分大小写近似。"""
        self.assertIn("ERP Product", query)
        self.assertEqual(list(values), ["CO1"])
        codes = sorted((code for code, _name, _unit in self.products.values()), key=str.casefold)
        return [(codes[0], codes[-1])] if codes else [(None, None)]

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
            ["03/10/2569", "IS-01", None, None, None, D(2), D("5.50"), D("11.00"), D(10), D("5.50"), D("55.00")],
            ["10/10/2569", "TR-1", D(5), D("5.50"), D("27.50"), None, None, None, D(15), D("5.50"), D("82.50")],
            ["10/10/2569", "TR-1", None, None, None, D(5), D("5.50"), D("27.50"), D(10), D("5.50"), D("55.00")],
            ["15/10/2569", "SI-02", None, None, None, D(2), D("5.50"), D("11.00"), D(8), D("5.50"), D("44.00")],
            ["20/10/2569", "PI-03", D(3), D("7.00"), D("21.00"), None, None, None, D(11), D("5.91"), D("65.00")]])
        self.assertEqual(self.values(body[8]), ["รวม  6  รายการ", D(14), None, D("84.50"), D(9), None, D("49.50"),
                                                None, None, None])
        self.assertEqual(body[9]["kind"], "gap")
        self.assertEqual(self.values(body[10]), ["รวมทั้งสิ้น  1  สินค้า", D(14), None, D("84.50"), D(9), None,
                                                 D("49.50"), D(11), None, D("65.00")])

    def test_all_warehouses_lists_products_by_code_and_skips_empty(self):
        body = self.layout("all")["body"]
        titles = [row["cells"][0]["value"] for row in body if row["kind"] == "product"]
        self.assertEqual(titles, ["A001  ปากกา", "A002  ดินสอ", "B001  ไม้บรรทัด"])  # A003 结转 0、期内无行
        grand = body[-1]
        self.assertEqual(self.values(grand)[0], "รวมทั้งสิ้น  3  สินค้า")
        self.assertEqual(self.values(grand)[3], D("84.50"))   # A002 没有自带成本的收：按池均价 0 入，记 0，不留空

    # ── 汇总（59.php）──
    def test_summary_one_row_per_product(self):
        body = self.layout("summary")["body"]
        self.assertEqual(self.values(body[0]), ["A001", "ปากกา", D(6), D("5.00"), D("30.00"), D(14), D("6.04"), D("84.50"),
                                                D(9), D("5.50"), D("49.50"), D(11), D("5.91"), D("65.00")])
        self.assertEqual(self.values(body[1]), ["A002", "ดินสอ", D(0), D(0), D("0.00"), D(3), D(0), D("0.00"),
                                                D(0), D(0), D("0.00"), D(3), D(0), D("0.00")])
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
        self.assertEqual(self.values(body[5])[8:], [D(5), D("5.50"), D("27.50")])
        self.assertEqual(self.values(body[6]), ["รวมสินค้า", D(6), None, D("36.00"), D(7), None, D("38.50"), None, None, None])
        self.assertEqual(self.values(body[7]), ["รายละเอียดรวมสินค้า  3  รายการ"])
        self.assertEqual(self.values(body[8]), ["รวมคลัง", D(6), None, D("36.00"), D(7), None, D("38.50"), D(5), None,
                                                D("27.50")])
        self.assertEqual(self.values(body[9]), ["รายละเอียดรวมคลัง  3  รายการ  1  สินค้า"])
        self.assertEqual(self.values(body[13]), ["ยอดยกมา", None, D(0), D(0), D("0.00")])
        self.assertEqual(self.values(body[16])[8:], [D(6), D("6.25"), D("37.50")])
        self.assertEqual(self.values(body[22]), ["รวมทุกคลัง", D(14), None, D("84.50"), D(9), None, D("49.50"), D(11), None,
                                                 D("65.00")])
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

    # ── 语言：文件跟导出人的网页语言（10-08 口径；主会话 11-08 要锁住）──
    def test_glossary_has_every_language_and_thai_is_mrerp(self):
        texts = glossary()
        self.assertEqual(set(MRERP_THAI) - set(texts), set())
        for key, row in texts.items():
            for lang in LANGUAGES:
                self.assertTrue(row[lang].strip() and row[lang] != key, (key, lang))   # 不落回键名
            self.assertNotEqual(row["zh"], row["th"], key)
            self.assertNotEqual(row["en"], row["th"], key)
        for key, thai in MRERP_THAI.items():
            self.assertEqual(texts[key]["th"], thai, key)

    def export_texts(self, lang, report="all"):
        self.export.frappe.local.lang = lang
        layout = self.layout(report, product_from="P1", product_to="P1")
        return {"title": layout["lines"][1][0], "tax": layout["lines"][1][1], "dates": layout["lines"][2][0],
                "head": [cell["value"] for cell in layout["head"][1]["cells"]],
                "carried": next(r for r in layout["body"] if r["kind"] == "carried")["cells"][0]["value"],
                "day": next(r for r in layout["body"] if r["kind"] == "line")["cells"][0]["value"],
                "file": self.export._filename(report, "xlsx")}

    def test_chinese_and_thai_exports_differ_in_text_and_year(self):
        zh, th = self.export_texts("zh"), self.export_texts("th")
        self.assertEqual(th, {"title": "รายงานรับ-จ่ายสินค้า", "tax": "เลขประจำตัวผู้เสียภาษี  0105555000001",
                              "dates": "วันที่  01/10/2569  ถึง  31/10/2569",
                              "head": ["วันที่", "เลขที่", "จำนวน", "ราคาต่อหน่วย", "มูลค่ารับ", "จำนวน", "ราคาต่อหน่วย",
                                       "มูลค่าจ่าย", "จำนวน", "ราคาต่อหน่วย", "มูลค่าคงเหลือ"],
                              "carried": "ยอดยกมา", "day": "03/10/2569", "file": "รายงานรับ-จ่ายสินค้า [08112569_143005].xlsx"})
        self.assertEqual(zh, {"title": "收发存报表", "tax": "纳税人识别号  0105555000001", "dates": "日期  01/10/2026  至  31/10/2026",
                              "head": ["日期", "单号", "数量", "单价", "收入金额", "数量", "单价", "发出金额", "数量", "单价", "结存金额"],
                              "carried": "上期结转", "day": "03/10/2026", "file": "收发存报表 [08112026_143005].xlsx"})
        en = self.export_texts("en", "by_warehouse")
        self.assertEqual((en["title"], en["day"][-4:], en["file"]),
                         ("Stock Receipt-Issue Report: By Warehouse", "2026",
                          "Stock Receipt-Issue Report - By Warehouse [08112026_143005].xlsx"))
        self.assertEqual(self.export_texts("th", "by_warehouse")["file"], "รายงานรับ-จ่ายสินค้า - แยกคลัง [08112569_143005].xlsx")

    def test_background_export_keeps_the_requesters_language(self):
        """走后台的那条路：真的 `pearnly_core.exports.service`（发起时记下语言，后台任务按它跑），换成内存里的假站点。"""
        import json as jsonlib
        import os
        import tempfile

        frappe = self.export.frappe
        jobs, folder, queued = {}, tempfile.mkdtemp(), []

        class Row(dict):
            __getattr__ = dict.get

        def get_value(doctype, name, fields, as_dict=False, **kwargs):
            if doctype != "Pearnly Export":
                return self.get_value(doctype, name, fields, as_dict=as_dict)
            row = jobs.get(name)
            return Row({field: row.get(field) for field in fields}) if row and as_dict else (row or {}).get(fields)

        def set_value(doctype, name, field, value=None, **kwargs):
            jobs[name].update(field if isinstance(field, dict) else {field: value})

        class Doc(dict):
            def insert(self, **kwargs):
                jobs[self["name"]] = dict(self)

        modules = {"pearnly_erp_portal.inventory.card_export": self.export}
        frappe.conf = {"pearnly_export_sync_rows": 0}                     # 每个导出都排后台
        frappe.session = SimpleNamespace(user="stock@example.com")
        frappe.get_doc = Doc
        frappe.get_attr = lambda path: getattr(modules[path.rpartition(".")[0]], path.rpartition(".")[2])
        frappe.enqueue = lambda method, **kwargs: queued.append(kwargs["job"])
        frappe.db = SimpleNamespace(get_value=get_value, set_value=set_value, count=self.count, commit=lambda: None,
                                    rollback=lambda **kwargs: None)
        frappe.set_user = lambda user: setattr(frappe.session, "user", user)
        frappe.get_site_path = lambda *parts: os.path.join(folder, *parts[1:])
        frappe.publish_realtime = lambda *args, **kwargs: None
        frappe.log_error = lambda **kwargs: None
        frappe.ValidationError = frappe.PermissionError = frappe.DoesNotExistError = type("Refused", (Exception,), {})
        frappe.local.response, frappe.local.message_log = {}, []
        utils = sys.modules["frappe.utils"]
        utils.add_to_date = lambda value, seconds=0: value
        utils.get_datetime = lambda value: value
        core = DOMAIN.parents[2] / "pearnly_core/pearnly_core/exports"
        load("pearnly_core.exports.disposition", core / "disposition.py")
        service = load("pearnly_core.exports.service", core / "service.py")
        # Excel 读回要 openpyxl（生产镜像里有）；本机没有时把版面文字原样写成 JSON 代替表格
        try:
            from openpyxl import load_workbook
        except ImportError:
            load_workbook = None
            self.export._xlsx = lambda layout: jsonlib.dumps(layout, default=str, ensure_ascii=False).encode()

        def texts(content):
            if load_workbook is None:
                return jsonlib.dumps(jsonlib.loads(content), ensure_ascii=False)
            from io import BytesIO
            return jsonlib.dumps([[cell.value for cell in row] for row in load_workbook(BytesIO(content)).active.iter_rows()],
                                 default=str, ensure_ascii=False)

        args = {"report": "all", "format": "xlsx", "date_from": "2026-10-01", "date_to": "2026-10-31",
                "product_from": "P1", "product_to": "P1"}
        seen = {}
        for asked, worker in (("zh", "th"), ("th", "zh")):
            frappe.local.lang = asked                                       # 发起人的网页语言
            job = service.deliver(count="pearnly_erp_portal.inventory.card_export.size",
                                  build="pearnly_erp_portal.inventory.card_export.build", args=args,
                                  filename="stock-card.xlsx", content_type=self.export.content_type("xlsx"))["export_job"]
            self.assertEqual(jobs[job]["lang"], asked)
            frappe.local.lang = worker                                      # 后台进程原本是别的语言
            service.run(job)
            row = jobs[job]
            self.assertEqual(row["status"], "done", row.get("error"))
            with open(service.path_of(row["file_key"]), "rb") as handle:
                seen[asked] = (row["filename"], texts(handle.read()))
        self.assertEqual(seen["zh"][0], "收发存报表 [08112026_143005].xlsx")
        self.assertEqual(seen["th"][0], "รายงานรับ-จ่ายสินค้า [08112569_143005].xlsx")
        for text in ("收发存报表", "上期结转", "日期  01/10/2026  至  31/10/2026", "03/10/2026"):
            self.assertIn(text, seen["zh"][1])
        for text in ("รายงานรับ-จ่ายสินค้า", "ยอดยกมา", "วันที่  01/10/2569  ถึง  31/10/2569", "03/10/2569"):
            self.assertIn(text, seen["th"][1])
        self.assertNotIn("ยอดยกมา", seen["zh"][1])
        self.assertNotIn("上期结转", seen["th"][1])
        self.assertEqual(len(queued), 2)

    # ── 与 MR.ERP 对齐的几处（主会话 11-09）──
    def test_errors_name_the_field_like_mrerp(self):
        """① 报错说出是哪一栏（rpstkmmform.js / rpstkmmstoreform.js 的原文）。"""
        cases = [({"date_from": "", "date_to": "2026-10-31"}, 'กรุณาเลือก "วันที่" ให้ครบ'),
                 ({"date_from": "2026-10-31", "date_to": "2026-10-01"}, '"วันที่" ต้องเรียงลำดับจากน้อยไปมาก')]
        for dates, message in cases:
            with self.assertRaises(ValueError) as caught:
                self.export._params(report="all", format="xlsx", **dates)
            self.assertEqual(str(caught.exception), message)
        with self.assertRaises(ValueError) as caught:
            self.layout("all", product_from="P4", product_to="P1")
        self.assertEqual(str(caught.exception), '"รหัสสินค้า" ต้องเรียงลำดับจากน้อยไปมาก')
        with self.assertRaises(ValueError) as caught:
            self.layout("by_warehouse", warehouse_from="W2", warehouse_to="W1")
        self.assertEqual(str(caught.exception), '"รหัสคลังสินค้า" ต้องเรียงลำดับจากน้อยไปมาก')

    def test_warehouse_without_code_uses_the_warehouse_page_code(self):
        """② 仓没填编号：用仓库页「รหัสคลัง」那一栏的系统号，不拿仓名顶替（11-09 testerp 印成「Test repository Test repository」）。"""
        self.stores["W2"] = ("", "คลังสาขา")
        layout = self.layout("by_warehouse", warehouse_from="W2", warehouse_to="W2")
        self.assertEqual(layout["lines"][2], ("รหัสคลังสินค้า  W2  ถึง  W2", ""))
        stores = [row["cells"][0]["value"] for row in layout["body"] if row["kind"] == "store"]
        self.assertEqual(stores, ["W2  คลังสาขา"])

    def test_open_ranges_print_the_master_first_and_last_codes(self):
        """③ 物料 / 仓留空：表头写主档的第一个 ~ 最后一个代码（MR.ERP 筛选页默认值），不是有流水的首末。"""
        self.products["P9"] = ("Z999", "ไม่มีความเคลื่อนไหว", "ชิ้น")
        self.stores["W3"] = ("WH03", "คลังว่าง")
        self.assertEqual(self.layout("all")["lines"][3], ("รหัสสินค้า  A001  ถึง  Z999", ""))
        lines = self.layout("by_warehouse")["lines"]
        self.assertEqual(lines[2:4], [("รหัสคลังสินค้า  WH01  ถึง  WH03", ""), ("รหัสสินค้า  A001  ถึง  Z999", "")])

    def test_pdf_centres_quantities_like_mrerp(self):
        """④ PDF 数量居中、单价金额靠右（58.php / 60.php / 199.php 的 Cell 对齐）。"""
        printing = ModuleType("pearnly_erp_portal.printing")
        printing.context = SimpleNamespace(fonts_css=lambda: "", FONT_FAMILY="PearnlyPrint")
        with patch.dict(sys.modules, {"pearnly_erp_portal.printing": printing}):
            text = self.export._html(self.layout("summary"))
        row = text.split('<tr class="line">')[1].split("</tr>")[0]
        cells = row.split("<td ")[1:]
        self.assertIn('class="center">6.00<', cells[2])      # ยอดยกมา 数量
        self.assertIn('class="right">5.00<', cells[3])       # 单价
        self.assertIn('class="right">30.00<', cells[4])      # 金额
        self.assertIn('class="center">14.00<', cells[5])

    def test_half_cents_round_up_in_both_formats(self):
        """⑥ 舍入同 MR.ERP（PHP number_format，四舍五入）：2.125 → 2.13，PDF 与 Excel 一样，不取偶。"""
        self.products["P5"] = ("C001", "น้ำตาล", "กก.")
        self.add("2026-10-02", "10:00:00", "P5", "W1", D("2.125"), 1, "purchase_in", "PI-09")
        layout = self.layout("all", product_from="P5", product_to="P5")
        line = next(row for row in layout["body"] if row["kind"] == "line")
        self.assertEqual(self.values(line)[2], D("2.13"))
        printing = ModuleType("pearnly_erp_portal.printing")
        printing.context = SimpleNamespace(fonts_css=lambda: "", FONT_FAMILY="PearnlyPrint")
        with patch.dict(sys.modules, {"pearnly_erp_portal.printing": printing}):
            text = self.export._html(layout)
        self.assertIn(">2.13<", text)
        self.assertNotIn(">2.12<", text)

    # ── 发出成本照 MR.ERP（stkcardpc.php）：不再留空（11-09 testerp 沐浴露、瓶装水）──
    def summary_of(self, code):
        row = next(r for r in self.layout("summary")["body"] if r["cells"][0]["value"] == code)
        return self.values(row)[2:]

    def test_issue_from_a_location_that_never_had_cost_takes_the_pool_price(self):
        """沐浴露：有成本的货在空仓位，卖却从从没进过货的仓位 A 出（A 负库存）。MR.ERP 全公司一个价：按池均价发，不空。"""
        self.products["P6"] = ("C006", "ครีมอาบน้ำ", "ขวด")
        self.add("2026-09-01", "10:00:00", "P6", "W1", 66, "121.21", "purchase_in", "PI-60")
        self.add("2026-10-01", "10:00:00", "P6", "W1", -1, None, "sale_out", "SI-61")
        self.movements[-1]["location"] = "A"
        self.add("2026-10-07", "10:00:00", "P6", "W1", 158, 80, "adjust", "IA-62")
        self.assertEqual(self.summary_of("C006"), [
            D(66), D("121.21"), D("7999.86"), D(158), D("80.00"), D("12640.00"), D(1), D("121.21"), D("121.21"),
            D(223), D("92.01"), D("20518.65")])     # 7999.86 − 121.21 + 12640.00 = 20518.65；÷ 223 = 92.01

    def test_same_day_issue_before_receipt_is_priced_receipt_first(self):
        """瓶装水：同一天真实时间先从仓位 B（从没进过货）发 63，再往 B 收 53 @20。原来按仓位算：发出空着、结余却按 0 扣，
        结余单价被抬到 34.00（3060 / 90）。MR.ERP 全公司一个池、同日先收后发：发按 20.00。"""
        self.products["P7"] = ("C007", "น้ำดื่ม", "ขวด")
        self.add("2026-09-01", "10:00:00", "P7", "W1", 100, 20, "purchase_in", "PI-70")
        self.add("2026-10-04", "09:00:00", "P7", "W1", -63, None, "adjust", "AD-05")
        self.movements[-1]["location"] = "B"
        self.add("2026-10-04", "10:00:00", "P7", "W1", 53, 20, "adjust", "AD-07")
        self.movements[-1]["location"] = "B"
        body = self.layout("all", product_from="P7", product_to="P7")["body"]
        self.assertEqual([self.values(row) for row in body[2:4]], [
            ["04/10/2569", "AD-07", D(53), D("20.00"), D("1060.00"), None, None, None, D(153), D("20.00"), D("3060.00")],
            ["04/10/2569", "AD-05", None, None, None, D(63), D("20.00"), D("1260.00"), D(90), D("20.00"), D("1800.00")]])

    def test_issue_past_zero_takes_everything_left_and_first_issue_is_zero(self):
        """发到 ≤ 0：金额 = 发出前剩下的全部金额、单价 = 金额 / 数量，池归 0；之前没有结余就发 0 / 0（stkcardpc.php）。"""
        self.products["P8"] = ("C008", "สบู่", "ก้อน")
        self.add("2026-10-02", "10:00:00", "P8", "W1", -2, None, "sale_out", "SI-80")      # 没有前结余
        self.add("2026-10-03", "10:00:00", "P8", "W1", 7, 10, "purchase_in", "PI-81")     # 池 5 / 70.00 / 14.00
        self.add("2026-10-04", "10:00:00", "P8", "W1", -8, None, "sale_out", "SI-82")     # 发完 -3：转出 70.00
        self.add("2026-10-05", "10:00:00", "P8", "W1", 4, None, "stock_in", "RT-83")      # 退货入、没自带价：池均价 0
        body = self.layout("all", product_from="P8", product_to="P8")["body"]
        self.assertEqual([self.values(row)[2:8] for row in body[2:6]], [
            [None, None, None, D(2), D("0.00"), D("0.00")],
            [D(7), D("10.00"), D("70.00"), None, None, None],
            [None, None, None, D(8), D("8.75"), D("70.00")],
            [D(4), D("0.00"), D("0.00"), None, None, None]])
        self.assertEqual([self.values(row)[8:] for row in body[2:6]], [
            [D(-2), D("0.00"), D("0.00")], [D(5), D("14.00"), D("70.00")], [D(-3), D("0.00"), D("0.00")],
            [D(1), D("0.00"), D("0.00")]])

    # ── 收发存页面与导出同一份数（主会话 11-09：同一个功能不能出两套数）──
    def test_page_card_shows_the_same_numbers_as_the_export(self):
        """单品明细（全部仓）= 合并全部仓导出；点某个仓 = 分仓导出里那个仓。逐笔单价、金额、结余都一样，且是 MR.ERP 的数。"""
        dates = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
        card = self.report.card(product="P1", **dates)
        self.assertEqual((card["opening"]["qty"], card["opening"]["unit_cost"], card["opening"]["value"]), (6, 5, 30))
        number = lambda value: None if value is None else float(value)  # noqa: E731
        self.assertEqual([(row["doc_no"], row["qty"], number(row["unit_price"]), number(row["amount"]), row["bal_qty"],
                           number(row["bal_unit_cost"]), number(row["bal_value"])) for row in card["rows"]], [
            ("PI-02", 6, 6, 36, 12, 5.5, 66), ("IS-01", -2, 5.5, 11, 10, 5.5, 55), ("TR-1", 5, 5.5, 27.5, 15, 5.5, 82.5),
            ("TR-1", -5, 5.5, 27.5, 10, 5.5, 55), ("SI-02", -2, 5.5, 11, 8, 5.5, 44), ("PI-03", 3, 7, 21, 11, 5.91, 65)])
        self.assertEqual((card["closing"]["qty"], card["closing"]["unit_cost"], card["closing"]["value"]), (11, 5.91, 65))
        exported = [row for row in self.layout("all", product_from="P1", product_to="P1")["body"] if row["kind"] == "line"]
        self.assertEqual([[float(v) for v in self.values(row)[2:] if v is not None] for row in exported],
                         [[abs(r["qty"]), *map(number, (r["unit_price"], r["amount"])), r["bal_qty"],
                           *map(number, (r["bal_unit_cost"], r["bal_value"]))] for r in card["rows"]])
        store = self.report.card(product="P1", warehouse="W2", **dates)
        by_store = self.layout("by_warehouse", product_from="P1", product_to="P1")["body"]
        w2 = by_store[by_store.index(next(r for r in by_store if r["kind"] == "store" and r["cells"][0]["value"].startswith("WH02"))):]
        lines = [row for row in w2 if row["kind"] == "line"][:3]
        self.assertEqual([[float(v) for v in self.values(row)[2:] if v is not None] for row in lines],
                         [[abs(r["qty"]), *map(number, (r["unit_price"], r["amount"])), r["bal_qty"],
                           *map(number, (r["bal_unit_cost"], r["bal_value"]))] for r in store["rows"]])
        self.assertEqual((store["closing"]["qty"], store["closing"]["value"]), (6, 37.5))

    def test_page_summary_list_closing_matches_the_export(self):
        """汇总列表一行一个仓位：期初 / 收 / 发 / 期末数量与期末金额，和分仓导出里同一个仓的数一样。"""
        rows = {(r["product"], r["warehouse"]): r for r in self.report.summary(date_from="2026-10-01", date_to="2026-10-31")}
        w1, w2 = rows[("P1", "W1")], rows[("P1", "W2")]
        self.assertEqual((w1["opening_qty"], w1["in_qty"], w1["out_qty"], w1["closing_qty"], w1["closing_value"]),
                         (6, 6, 7, 5, 27.5))
        self.assertEqual((w2["opening_qty"], w2["in_qty"], w2["out_qty"], w2["closing_qty"], w2["closing_value"],
                          w2["closing_unit_cost"]), (0, 8, 2, 6, 37.5, 6.25))
        body = self.layout("by_warehouse", product_from="P1", product_to="P1")["body"]
        totals = [self.values(row) for row in body if row["kind"] == "store-total"]
        self.assertEqual([(float(t[7]), float(t[9])) for t in totals],
                         [(w1["closing_qty"], w1["closing_value"]), (w2["closing_qty"], w2["closing_value"])])
        self.assertEqual(rows[("P2", "W1")]["closing_value"], 0)     # 没自带成本的收：记 0，不留空

    def test_pdf_html_repeats_header_and_escapes(self):
        self.products["P1"] = ("A001", "<b>ปากกา</b>", "ด้าม")
        printing = ModuleType("pearnly_erp_portal.printing")
        printing.context = SimpleNamespace(fonts_css=lambda: "", FONT_FAMILY="PearnlyPrint")
        with patch.dict(sys.modules, {"pearnly_erp_portal.printing": printing}):
            text = self.export._html(self.layout("all", product_from="P1", product_to="P1"))
        self.assertIn("<thead>", text)
        self.assertIn("รายงานรับ-จ่ายสินค้า", text.split("<tbody>")[0])   # 表头在 thead 里，每页重复
        self.assertIn("&lt;b&gt;ปากกา&lt;/b&gt;", text)
        self.assertIn("65.00", text)

    @unittest.skipUnless(importlib.util.find_spec("openpyxl"), "openpyxl 在生产镜像里（候选检查跑）")
    def test_xlsx_cells_read_back(self):
        from io import BytesIO
        from openpyxl import load_workbook

        sheet = load_workbook(BytesIO(self.export._xlsx(self.layout("summary")))).active
        self.assertEqual(sheet["A1"].value, "บริษัท ทดสอบ จำกัด")
        self.assertEqual(sheet["K2"].value, "เลขประจำตัวผู้เสียภาษี  0105555000001")
        self.assertEqual((sheet["C5"].value, sheet["F5"].value, sheet["C6"].value), ("ยอดยกมา", "มูลค่ารับ", "จำนวน"))
        self.assertEqual([sheet.cell(7, column).value for column in range(1, 15)],
                         ["A001", "ปากกา", 6, 5, 30, 14, 6.04, 84.5, 9, 5.5, 49.5, 11, 5.91, 65])
        self.assertEqual(sheet["G7"].number_format, "#,##0.00")
        self.assertIn("A5:A6", {str(merged) for merged in sheet.merged_cells.ranges})
        self.assertEqual(sheet.page_setup.orientation, "portrait")
        self.assertEqual(sheet.oddHeader.right.text, "&P/&N")


class WarehouseCode(unittest.TestCase):
    """② 仓库编号服务端统一取（主会话 11-09）：仓库页「รหัสคลัง」那一栏与收发存导出用同一个值 `warehouses.display_code`。"""

    def setUp(self):
        modules = patch.dict(sys.modules)
        modules.start()
        self.addCleanup(modules.stop)
        rows = [{"name": "WH-00001", "warehouse_name": "คลัง Express", "warehouse_code": "", "express_code": "EXP1",
                 "company": "CO1", "is_active": 1, "note": ""},
                {"name": "WH-00002", "warehouse_name": "คลังมีรหัส", "warehouse_code": "WH01 ", "express_code": "",
                 "company": "CO1", "is_active": 1, "note": ""},
                {"name": "WH-00003", "warehouse_name": "Test repository", "warehouse_code": "", "express_code": "",
                 "company": "CO1", "is_active": 1, "note": ""}]
        frappe = ModuleType("frappe")
        frappe._ = lambda text: text
        frappe.get_list = lambda doctype, **kwargs: [dict(row) for row in rows]
        frappe.get_all = lambda doctype, **kwargs: [{"name": "CO1", "company_name": "บริษัท ทดสอบ จำกัด"}]
        frappe.whitelist = lambda *args, **kwargs: (lambda function: function)
        access = ModuleType("pearnly_erp_portal.access")
        access.guard = SimpleNamespace(require=lambda capability: None)
        masters = ModuleType("pearnly_erp_portal.masters")
        masters.company = SimpleNamespace(document_company=lambda company=None: "CO1", active_company=lambda: "CO1")
        sys.modules.update({"frappe": frappe, "pearnly_erp_portal.access": access, "pearnly_erp_portal.masters": masters,
                            "pearnly_erp_portal.masters.company": masters.company})
        self.warehouses = load("card_export_warehouses", DOMAIN / "warehouses.py")

    def test_the_list_carries_the_code_the_page_and_the_export_use(self):
        listed = self.warehouses.list_warehouses(include_inactive=True, limit=0)
        self.assertEqual([row["display_code"] for row in listed], ["EXP1", "WH01", "WH-00003"])   # 没填编号 → 系统号，不是仓名
        self.assertEqual([self.warehouses.display_code(row) for row in listed], ["EXP1", "WH01", "WH-00003"])

    def test_the_page_shows_the_server_value_and_the_export_calls_the_same_function(self):
        page = (DOMAIN.parent / "public/desk/warehouses.js").read_text(encoding="utf-8")
        column = page.split('fieldname: "display_code"', 1)
        self.assertEqual(len(column), 2, "仓库页编号那一栏要显示服务端的 display_code")
        self.assertNotIn("row?.name", column[1].split("}", 1)[0])                                # 前端不再自己拼
        self.assertIn("warehouses.display_code(row)", (DOMAIN / "card_export.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
