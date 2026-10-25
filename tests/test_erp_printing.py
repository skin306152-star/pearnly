"""单据打印（领料单、收货单，岳父样板 1:1，主会话 10-26 审定）：纯规则与版面渲染，不连库。

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


class Rules(unittest.TestCase):
    def test_pages_hold_ten_rows_each_and_fill_the_blank_slots(self):
        pages = C.pages(rows(13))
        self.assertEqual([len(page) for page in pages], [10, 10])
        self.assertEqual(pages[1][2]["no"], 13)
        self.assertIsNone(pages[1][3])
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
        ctx = {"company": COMPANY, "title_th": "ใบเบิกสินค้า", "title_en": "GOODS ISSUE SLIP", "number": "IS-1",
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

    def test_more_than_ten_lines_continue_on_the_next_page(self):
        html = self.issue(pages=C.pages(rows(13)), page_count=2)
        self.assertEqual(html.count('class="pp-page'), 2)
        self.assertIn("2 / 2", html)
        self.assertIn(">13<", html)

    def test_the_receipt_note_fills_the_supplier_and_ticks_the_main_warehouse(self):
        html = render("goods_receipt", company=COMPANY, title_th="ใบรับสินค้า", title_en="GOODS RECEIPT NOTE", number="IR-1",
                      date="25/10/2026", remarks="", void=False, pages=C.pages(rows(1)), page_count=1, warehouse="Main",
                      supplier={"code": "S1", "name": "ผู้ขาย", "address": "ที่อยู่", "phone": "02", "email": "", "tax_id": "0105"},
                      warehouse_check=C.warehouse_check(True, "Main"))
        for text in ("ใบรับสินค้า", "GOODS RECEIPT NOTE", "วันที่รับสินค้า", "ข้อมูลผู้ขาย / Supplier", "ใบส่งของ (DO No.)",
                     "PO Qty", "DO Qty", "Receive Qty", "ผู้ตรวจสอบ", "Checked by", "ผู้ขาย / ผู้ส่งมอบ", "S1", "0105"):
            self.assertIn(text, html)
        self.assertEqual(html.count("&#10003;"), 1)  # 只勾「คลังหลัก」


if __name__ == "__main__":
    unittest.main()
