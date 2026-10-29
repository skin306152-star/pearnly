"""单据打印（领料单、收货单，岳父样板 1:1；第二批采购订单、报价 / 销售单、收银小票、交班小结、盘点，主会话 10-26 审定）：
纯规则与版面渲染，不连库。

- 每页 10 行、续页序号接着往下、页码；没有明细也出一页空表；
- 数量 / 金额 / 日期写法（公历 日/月/年）；
- 领料单用途勾选（内部领用打「ใช้งานภายใน」，报损 / 其他打「อื่นๆ」写原因）、收货单仓库勾选（主仓打「คลังหลัก」，其余「อื่นๆ」写仓库名）；
- 作废单才有「已作废」水印；ERP 没有的栏位留空，不冒充。
"""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_erp_portal")]

from pearnly_erp_portal.printing import contracts as C  # noqa: E402

try:  # 版面渲染要 jinja2：生产镜像里有（候选检查跑），开发检查的裸 Python 没有就只跑纯规则
    import jinja2
except ImportError:
    jinja2 = None
ENV = jinja2.Environment(loader=jinja2.FileSystemLoader(str(ROOT / "apps/pearnly_erp_portal"))) if jinja2 else None
COMPANY = {"name": "บริษัท ตัวอย่าง จำกัด", "address": "1 ถ.สุขุมวิท", "phone": "02-000-0000", "email": "a@b.co",
           "logo": "data:image/png;base64,AA=="}


def render(name, **ctx):
    return ENV.get_template(f"pearnly_erp_portal/printing/templates/{name}.html").render(ctx=ctx)


def rows(count):
    return [{"code": f"P{i}", "name": f"สินค้า {i}", "unit": "ชิ้น", "qty": str(i), "price": "1.00", "amount": "1.00"}
            for i in range(1, count + 1)]


class FontSurvivesThePdfPipeline(unittest.TestCase):
    """Frappe 出 PDF 前用 `scrub_urls` 展开地址，会给「冒号后紧跟 url(」补 ` !important`；补在 @font-face 的 src 上字体就作废、
    回落 Loma（10-26 实测）。内嵌字体的 src 要原样过这一步。要 Frappe：生产镜像里跑（候选检查），开发检查跳过。"""

    def test_the_embedded_font_src_is_left_valid(self):
        try:
            import frappe
            from frappe.utils import data
        except ImportError:
            self.skipTest("Frappe not installed")
        from unittest import mock

        from pearnly_erp_portal.printing import context

        with mock.patch.object(data, "get_url", lambda *args, **kwargs: "https://erp.example"), \
                mock.patch.object(context.frappe, "get_app_path",
                                  lambda app, *parts: str(ROOT / "apps" / app / app / pathlib.Path(*parts))):
            css = context.fonts_css()
            scrubbed = frappe.utils.scrub_urls(css)
        self.assertEqual(css.count("url(data:font/ttf;base64,"), 2)  # Regular + Bold
        self.assertEqual(scrubbed, css)
        self.assertNotIn("!important", scrubbed)


class Rules(unittest.TestCase):
    def test_pages_hold_ten_rows_each_and_fill_the_blank_slots(self):
        pages = C.pages(rows(13))
        self.assertEqual([len(page) for page in pages], [10, 10])
        self.assertEqual(pages[1][2]["no"], 13)
        self.assertIsNone(pages[1][3])

    def test_the_bundled_print_font_and_logo_ship_with_the_app(self):
        # 打印字体随 App 分发（开源授权），`context.fonts_css` 按 `<家族>-Regular/Bold.ttf` 找；Logo 用正式品牌包的矢量 lockup
        public = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/public"
        fonts = sorted(path.name for path in (public / "fonts/print").glob("*.ttf"))
        self.assertTrue(any(name.endswith("-Regular.ttf") for name in fonts), fonts)
        self.assertTrue(any(name.endswith("-Bold.ttf") for name in fonts), fonts)
        for name in fonts:
            self.assertIn(b"Open Font License", (public / "fonts/print" / name).read_bytes())
        self.assertIn(b"<svg", (public / "images/print/pearnly-logo.svg").read_bytes()[:200])
        self.assertEqual(C.pages([]), [[None] * 10])

    def test_numbers_and_dates_print_like_the_template(self):
        self.assertEqual((C.money("1234.5"), C.money(None), C.money("")), ("1,234.50", "", ""))
        self.assertEqual((C.qty("3.000"), C.qty("2.50"), C.qty("1500")), ("3", "2.5", "1,500"))
        self.assertEqual(C.day("2026-10-25"), "25/10/2026")  # 公历，不换佛历

    def test_issue_purpose_and_warehouse_ticks(self):
        self.assertEqual(C.issue_purpose("internal_use", "x"),
                         {"checked": {"internal": True, "production": False, "sales": False, "other": False}, "other_text": ""})
        self.assertEqual(C.issue_purpose("damage", "แตก")["other_text"], "แตก")
        self.assertFalse(any(C.issue_purpose("", "")["checked"].values()))
        self.assertEqual(C.warehouse_check(True, "Main"), {"main": True, "other": False, "other_text": ""})
        self.assertEqual(C.warehouse_check(False, "คลัง B"), {"main": False, "other": True, "other_text": "คลัง B"})


@unittest.skipUnless(jinja2, "jinja2 not installed (runs in the candidate checks on the production image)")
class Layout(unittest.TestCase):
    def issue(self, **over):
        ctx = {"company": COMPANY, "number": "IS-1",
               "date": "25/10/2026", "remarks": "เบิก", "void": False, "pages": C.pages(rows(2)), "page_count": 1,
               "warehouse": "Main", "purpose": C.issue_purpose("internal_use", "")}
        ctx.update(over)
        return render("goods_issue", **ctx)

    def test_the_issue_slip_carries_the_template_labels_and_the_data(self):
        html = self.issue()
        for text in ("ใบเบิกสินค้า", "GOODS ISSUE SLIP", "IS-1", "25/10/2026", "1 / 1", "ฝ่ายที่เบิก", "ใบสั่งผลิต (MO)",
                     "จำนวนที่ขอเบิก", "Request Qty", "ผู้เบิก", "Requested by", "ผู้รับสินค้า", "Received by", "P2", "บริษัท ตัวอย่าง จำกัด"):
            self.assertIn(text, html)
        self.assertEqual(html.count("&#10003;"), 1)  # 只勾「ใช้งานภายใน」
        self.assertNotIn("CANCELLED", html)
        self.assertIn("CANCELLED", self.issue(void=True))
        self.assertIn("@font-face", self.issue(fonts_css="@font-face {}"))

    def test_more_than_ten_lines_continue_on_the_next_page(self):
        html = self.issue(pages=C.pages(rows(13)), page_count=2)
        self.assertEqual(html.count('class="pp-page'), 2)
        self.assertIn("2 / 2", html)
        self.assertIn(">13<", html)

    def test_the_receipt_note_fills_the_supplier_and_ticks_the_main_warehouse(self):
        html = render("goods_receipt", company=COMPANY, number="IR-1",
                      date="25/10/2026", remarks="", void=False, pages=C.pages(rows(1)), page_count=1, warehouse="Main",
                      supplier={"code": "S1", "name": "ผู้ขาย", "address": "ที่อยู่", "phone": "02", "email": "", "tax_id": "0105"},
                      warehouse_check=C.warehouse_check(True, "Main"))
        for text in ("ใบรับสินค้า", "GOODS RECEIPT NOTE", "วันที่รับสินค้า", "ข้อมูลผู้ขาย / Supplier", "ใบส่งของ (DO No.)",
                     "PO Qty", "DO Qty", "Receive Qty", "ผู้ตรวจสอบ", "Checked by", "ผู้ขาย / ผู้ส่งมอบ", "S1", "0105"):
            self.assertIn(text, html)
        self.assertEqual(html.count("&#10003;"), 1)  # 只勾「คลังหลัก」



class SecondBatchRules(unittest.TestCase):
    """第二批（主会话 10-26 §12 定）：税票口径写死、分店写法、车号行、盲盘、热敏纸长。取数层只给键，泰英文字在 `_labels.html`。"""

    def test_titles_never_claim_a_tax_invoice(self):
        self.assertEqual((C.sales_title("quotation", True), C.sales_title("receipt", True), C.sales_title("receipt", False)),
                         ("quotation", "delivery", "receipt"))  # 接了 Express：税票在 Express 出，ERP 打送货单
        self.assertEqual((C.pos_title("sale"), C.pos_title("refund")), ("pos_sale", "pos_refund"))
        labels = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/printing/templates/_labels.html").read_text(encoding="utf-8")
        self.assertNotIn("ใบกำกับภาษี", labels)
        for key in C.TITLE_KEYS:
            self.assertIn(f'"{key}": (', labels)

    def test_branches_vehicles_and_count_modes(self):
        self.assertEqual((C.branch_text("head_office"), C.branch_text("hq"), C.branch_text("branch", "00003"),
                          C.branch_text("", "")), ({"head": True}, {"head": True}, {"no": "00003"}, {}))
        self.assertEqual((C.company_branch("0"), C.company_branch("2"), C.company_branch("")),
                         ({"head": True}, {"no": "00002"}, {}))
        self.assertEqual(C.vehicle_lines([{"serial_no": "MR1", "secondary_no": "", "color": "แดง"}, {"serial_no": ""}]),
                         [{"serial_no": "MR1", "secondary_no": "", "color": "แดง"}])
        self.assertEqual((C.count_mode("Active"), C.count_mode("Posted"), C.count_mode("Closed")),
                         ("sheet", "result", "result"))
        # 进行中的核对表只给有库存管理权的人；结束后一律结果表（用户 10-29 批复 #1）
        self.assertEqual((C.count_mode("Active", "check", True), C.count_mode("Active", "check", False),
                          C.count_mode("Posted", "check", False)), ("check", "sheet", "result"))

    def test_count_rows_filter_by_only_warehouse_and_location(self):
        from pearnly_erp_portal.inventory import contracts as S

        rows = [{"name": "a", "counted": 0, "difference": 0, "warehouse": "W1", "location": "A1"},
                {"name": "b", "counted": 1, "difference": 0, "warehouse": "W1", "location": "A2"},
                {"name": "c", "counted": 1, "difference": -2, "warehouse": "W2", "location": "A1"},
                {"name": "d", "counted": 1, "difference": 0.0004, "warehouse": "W1", "location": ""}]
        names = lambda **kw: [row["name"] for row in S.count_rows(rows, **kw)]
        self.assertEqual(names(), ["a", "b", "c", "d"])
        self.assertEqual(names(only="uncounted"), ["a"])
        self.assertEqual(names(only="differences"), ["c"])  # 四舍到 3 位：0.0004 不算差异
        self.assertEqual(names(warehouse="W1", location="A1"), ["a"])
        self.assertEqual(names(only="counted", warehouse="W1"), ["b", "d"])
        self.assertLess(C.thermal_height_mm(3, 2), C.thermal_height_mm(30, 2))


@unittest.skipIf(jinja2 is None, "jinja2 not installed")
class SecondBatchLayouts(unittest.TestCase):
    def trade(self, **over):
        ctx = {"kind": "receipt", "company": COMPANY, "number": "SI-1", "date": "26/10/2026", "remarks": "ส่งของ",
               "void": False, "currency": "THB", "title": C.sales_title("receipt", False),
               "pages": C.pages([{**rows(1)[0], "discount": "", "sub": [{"serial_no": "MR1", "secondary_no": "E9", "color": ""}]}]),
               "page_count": 1,
               "party": {"code": "", "name": "ลูกค้า", "address": "ที่อยู่", "phone": "02", "tax_id": "0105", "branch": {"no": "00003"}},
               "info": [("payment", {"method": "cash"}), ("paid_date", "26/10/2026")],
               "totals": [{"key": "subtotal", "value": "1.00"}, {"key": "grand_total", "value": "1.07"}],
               "signs": ["delivered", "received", "collected"]}
        ctx.update(over)
        return render("trade", **ctx)

    def test_sales_slip_prints_customer_tax_id_branch_vehicle_and_totals(self):
        html = self.trade()
        for text in ("ใบเสร็จรับเงิน", "RECEIPT", "ข้อมูลลูกค้า / Customer", "0105", "สาขา 00003", "เลขตัวถัง MR1",
                     "เลขเครื่อง E9", "เงินสด / Cash", "จำนวนเงินรวมทั้งสิ้น / Grand Total", "1.07", "ผู้รับเงิน", "SI-1"):
            self.assertIn(text, html)
        self.assertNotIn("ใบกำกับภาษี", html)
        self.assertIn("ใบส่งของ", self.trade(title="delivery"))
        self.assertNotIn("order", C.TITLE_KEYS)  # 采购订单 10-28 删掉（用户：「采购订单删掉吧」）
        self.assertIn("CANCELLED", self.trade(void=True))

    def thermal(self, **over):
        ctx = {"kind": "pos", "company": {**COMPANY, "branch": {"head": True}, "tax_id": "0115"}, "fonts_css": "",
               "number": "SI-9", "date": "26/10/2026", "time": "10:42", "terminal": "หน้าร้าน 1", "cashier": "A",
               "void": False, "title": C.pos_title("sale"), "refund": False, "original": "", "remarks": "",
               "rows": [{"name": "สินค้า", "qty": "2", "unit": "ชิ้น", "price": "10.00", "discount": "", "amount": "20.00",
                         "sub": []}],
               "totals": [{"key": "pos_total", "value": "20.00"}], "vat": "1.31",
               "payments": [{"method": "cash", "amount": "100.00"}], "change": "80.00", "paper_mm": 150}
        ctx.update(over)
        return render("thermal", **ctx)

    def test_pos_receipt_and_return_are_80mm_and_not_tax_invoices(self):
        html = self.thermal()
        for text in ("page-width: 80mm", "page-height: 150mm", "ใบเสร็จรับเงิน", "SI-9", "สำนักงานใหญ่",
                     "เลขประจำตัวผู้เสียภาษี 0115", "เงินสด / Cash", "เงินทอน / Change", "80.00", "1.31"):
            self.assertIn(text, html)
        self.assertNotIn("ใบกำกับภาษี", html)
        self.assertNotIn("ขอบคุณ", html)  # 页眉页脚只印公司抬头资料，不加感谢语（§12-7）
        returned = self.thermal(refund=True, title=C.pos_title("refund"), original="SI-8", remarks="ชำรุด")
        for text in ("ใบรับคืนสินค้า", "RETURN RECEIPT", "SI-8", "ชำรุด"):
            self.assertIn(text, returned)

    def test_shift_summary(self):
        html = self.thermal(kind="shift", title="shift", opened="26/10/2026 08:00", closed="26/10/2026 17:05",
                            counts={"sales": 3, "refunds": 1}, totals=[{"key": "net", "value": "476.00"}],
                            methods=[{"method": "cash", "value": "903.00"}],
                            cash=[{"key": "difference", "value": "-10.00"}],
                            balances=[{"method": "promptpay", "expected": "1,890.50", "counted": "1,880.50",
                                       "difference": "-10.00"}])
        for text in ("สรุปการปิดกะ", "ยอดสุทธิ / Net", "476.00", "เงินสด / Cash", "903.00", "ส่วนต่าง / Difference",
                     "พร้อมเพย์", "1,880.50", "ผู้ตรวจสอบ"):
            self.assertIn(text, html)

    def test_goods_slips_print_the_internal_code_first(self):
        """收货单 / 领料单编码列：内部编码 → Express 编码 → 系统编码（用户 10-29 批复）。"""
        self.assertEqual(C.goods_code({"internal_code": "EX-1", "express_code": "E1", "code": "P-1"}), "EX-1")
        self.assertEqual(C.goods_code({"internal_code": "", "express_code": "E1", "code": "P-1"}), "E1")
        self.assertEqual(C.goods_code({"code": "P-1"}), "P-1")
        self.assertEqual(C.goods_code(None), "")

    def count(self, mode, rows_):
        return render("stock_count", company=COMPANY, number="STC-1", date="26/10/2026", remarks="", fonts_css="",
                      mode=mode, title="count_sheet" if mode == "sheet" else "count_result", warehouse="คลังหลัก",
                      count_title="ต.ค.", item_total=len(rows_), counted_total=0, difference_total=0,
                      pages=C.pages(rows_, C.COUNT_ROWS_PER_PAGE), page_count=1)

    def test_count_sheet_is_blind_and_the_result_shows_book_counted_difference(self):
        sheet = self.count("sheet", [{"code": "P1", "name": "สินค้า", "unit": "ชิ้น", "location": "A-01"}])
        self.assertNotIn("Book Qty", sheet)  # 盲盘：点数表不印账面数（§12-5）
        self.assertIn("ใบตรวจนับสินค้า", sheet)
        self.assertIn("A4 portrait", sheet)
        result = self.count("result", [{"code": "P1", "name": "สินค้า", "unit": "ชิ้น", "location": "A-01", "book": "6",
                                        "counted": "4", "difference": "-2"}])
        for text in ("ผลการตรวจนับ", "Book Qty", "Counted", "Difference", ">-2<"):
            self.assertIn(text, result)
        self.assertEqual(self.count("sheet", [{"code": f"P{i}"} for i in range(30)]).count('class="pp-page'), 2)


if __name__ == "__main__":
    unittest.main()
