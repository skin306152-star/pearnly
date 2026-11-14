"""MR.ERP 导入表的金额三件事与写后核对（共用层 `pearnly_core.mrerp.push`，直写与代录同一份；Cowork、ERP 同一份）。

11-14 在 test01 · TEST2019 经 Cowork 推送真写查出（单号在 `docs/work/2026-11-14-mrerp-write-grid.md`），三张都报「已写入」：

1. 行金额：Cowork 识别的明细金额记在 ``subtotal``（同 Express 的读法），生成器只读 ``amount``，行折扣被改成「数量 × 单价」——
   票 288.90 记成 321.00（SI691114-865946）；
2. 采购货品税率写死「7 (แยก)」：无税卖家 500.00 记成 535.00（PG691114-730046）；
3. 赊销 / 采购表头折扣写死 0：整单折扣票 963.00 记成 1,070.00（SI691114-762644）。现销早已把折扣摊进行里，三种单改成同一做法
   （`fmt.allocate_discount`）；
4. 写后核对：此前回读只认单号，列表里的金额不看——现在拿列表金额比票面总额，核不上记 ``meta.verify`` mismatch（同 Express，只记不拦）。

导入表直接调生成器（纯函数，不连网、不要 Frappe），读出 sheet 的格子比对。
"""
import io
import pathlib
import re
import sys
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.mrerp.push import operator_draft, readback  # noqa: E402
from pearnly_core.mrerp.push.xlsx import fmt, purchase, sales_cash, sales_credit  # noqa: E402

OWN, SUPPLIER, CUSTOMER = "0105989581512", "0105666955135", "0105252761298"
MAPPINGS = {
    "_own_tax_id": OWN,
    "_mrerp_header": {"branch": "00000", "dept": "0000", "job": "0000", "store": "0000", "employee": ". .",
                      "area": "ไม่ระบุ", "delivery": "ไม่ระบุ"},
    "_mrerp_receipt_account_cash": "1111-01", "_mrerp_receipt_account_bank": "1112-01",
    "products": [{"name": "สินค้าขายทดสอบ A", "erp_code": "PA"}, {"name": "สกรูทดสอบ M6", "erp_code": "PS"}],
    "suppliers": [{"seller_tax": SUPPLIER, "erp_code": SUPPLIER}],
    "customers": [{"buyer_tax": CUSTOMER, "erp_code": CUSTOMER}],
}


def ticket(hid, direction, items, **fields):
    """Cowork 推送载荷里的一张票（`flatten_history_for_mrerp` 之后：票面在 ``fields``，明细键同识别 `{name, qty, price, subtotal}`）。"""
    face = {"direction": direction, "invoice_number": f"MW-{hid}", "date": "2026-11-14", "items": items,
            "seller_tax": SUPPLIER if direction == "purchase" else OWN,
            "buyer_tax": CUSTOMER if direction == "sales" else OWN, **fields}
    return {"id": f"CWH-{hid:0>48}", "invoice_no": face["invoice_number"], "invoice_number": face["invoice_number"],
            "invoice_date": "2026-11-14", "total_amount": fields.get("total_amount"), "fields": face}


LINE_DISCOUNT = ticket("a1", "sales", [{"name": "สินค้าขายทดสอบ A", "qty": "3", "price": "100.00", "subtotal": "270.00"}],
                       subtotal="270.00", vat="18.90", total_amount="288.90")
NON_VAT = ticket("a2", "purchase", [{"name": "สกรูทดสอบ M6", "qty": "10", "price": "50.00", "subtotal": "500.00"}],
                 subtotal="500.00", vat="0", total_amount="500.00", posting_item_type_manual="goods")
HEADER_DISCOUNT = ticket("a3", "sales", [{"name": "สินค้าขายทดสอบ A", "qty": "4", "price": "250.00", "subtotal": "1000.00"}],
                         discount="100.00", subtotal="900.00", vat="63.00", total_amount="963.00")
PURCHASE_DISCOUNT = ticket("a4", "purchase", [{"name": "สกรูทดสอบ M6", "qty": "10", "price": "50.00", "subtotal": "500.00"},
                                              {"name": "สกรูทดสอบ M6", "qty": "10", "price": "50.00", "subtotal": "500.00"}],
                           discount="100.00", subtotal="900.00", vat="63.00", total_amount="963.00",
                           posting_item_type_manual="goods")


def sheets(blob):
    """xlsx → {sheet 号: [[格子文字…], …]}（共享字符串换回文字；数字格原样）。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        shared = [re.sub(r"<[^>]+>", "", s) for s in
                  re.findall(r"<si>(.*?)</si>", zf.read("xl/sharedStrings.xml").decode(), re.S)]
        out = {}
        for n in (1, 2):
            xml = zf.read(f"xl/worksheets/sheet{n}.xml").decode()
            rows = []
            for row in re.findall(r"<row\b[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for attrs, body in re.findall(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", row, re.S):
                    value = (re.search(r"<v>(.*?)</v>", body or "") or [None, ""])[1]
                    cells.append(shared[int(value)] if 't="s"' in attrs and value != "" else value)
                rows.append(cells)
            out[n] = rows
    return out


def money(text):
    return f"{float(text or 0):.2f}"


class LineAmount(unittest.TestCase):
    def test_cowork_line_subtotal_is_the_line_amount_in_every_lane(self):
        rows = sales_credit.build_sales_credit_detail_rows(LINE_DISCOUNT, MAPPINGS)
        self.assertEqual([money(r["amount"]) for r in rows], ["270.00"])
        rows = purchase._detail_rows(NON_VAT, MAPPINGS)
        self.assertEqual([money(r["amount"]) for r in rows], ["500.00"])
        self.assertEqual(fmt.line_amount({"qty": "3", "price": "100", "subtotal": "270"}), 270.0)
        self.assertEqual(fmt.line_amount({"amount": "1", "subtotal": "2"}), 1.0)  # 导入表 / ERP 的 amount 优先
        self.assertIsNone(fmt.line_amount({"qty": "3", "price": "100"}))

    def test_credit_sale_sheet_carries_the_printed_line_amount(self):
        got = sheets(sales_credit._generate_xlsx_sales_credit_korn_clone([LINE_DISCOUNT], MAPPINGS))
        self.assertEqual(money(got[2][1][7]), "270.00")


class TaxLabel(unittest.TestCase):
    def test_goods_purchase_from_a_non_vat_seller_is_written_at_zero(self):
        self.assertEqual(fmt.mrerp_tax_label(NON_VAT), "0")
        got = sheets(purchase.generate_xlsx_purchase([NON_VAT], MAPPINGS))
        self.assertEqual(got[1][1][2], "0")

    def test_goods_purchase_with_vat_keeps_exclusive_and_expense_keeps_out_of_system(self):
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([PURCHASE_DISCOUNT], MAPPINGS))[1][1][2], "7 (แยก)")
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([NON_VAT], MAPPINGS, expense=True))[1][1][2], "นอกระบบ")


class Discount(unittest.TestCase):
    def test_credit_sale_spreads_the_document_discount_into_its_lines(self):
        got = sheets(sales_credit._generate_xlsx_sales_credit_korn_clone([HEADER_DISCOUNT], MAPPINGS))
        self.assertEqual(money(got[1][1][14]), "0.00")
        self.assertEqual([money(r[7]) for r in got[2][1:]], ["900.00"])

    def test_goods_purchase_spreads_it_too_and_cash_sale_is_unchanged(self):
        got = sheets(purchase.generate_xlsx_purchase([PURCHASE_DISCOUNT], MAPPINGS))
        self.assertEqual(money(got[1][1][12]), "0.00")
        self.assertEqual([money(r[7]) for r in got[2][1:]], ["450.00", "450.00"])
        cash = dict(HEADER_DISCOUNT, fields=dict(HEADER_DISCOUNT["fields"], posting_payment_manual="cash"))
        got = sheets(sales_cash.generate_xlsx_sales_cash([cash], MAPPINGS))
        self.assertEqual(money(got[1][1][14]), "0.00")
        self.assertEqual([money(r[7]) for r in got[2][1:]], ["900.00"])

    def test_a_discount_that_does_not_add_up_stays_on_the_header(self):
        odd = ticket("a5", "sales", [{"name": "สินค้าขายทดสอบ A", "qty": "4", "price": "250.00", "subtotal": "1000.00"}],
                     discount="50.00", subtotal="900.00", vat="63.00", total_amount="963.00")
        got = sheets(sales_credit._generate_xlsx_sales_credit_korn_clone([odd], MAPPINGS))
        self.assertEqual(money(got[1][1][14]), "50.00")
        self.assertEqual([money(r[7]) for r in got[2][1:]], ["1000.00"])

    def test_operator_web_form_puts_line_and_document_discounts_in_its_header_discount(self):
        mappings = dict(MAPPINGS, _mrerp_customer_code=CUSTOMER)
        for history, discount, net in ((LINE_DISCOUNT, "30", "288.90"), (HEADER_DISCOUNT, "100", "963.00")):
            block = operator_draft.block(history, mappings, doc_type="sales_credit", menu="119", url="https://x/",
                                         connection="C", account_set={})
            self.assertEqual((block["discount"], block["totals"]["net"]), (discount, net))
        block = operator_draft.block(NON_VAT, MAPPINGS, doc_type="purchase", menu="68", url="https://x/",
                                     connection="C", account_set={})
        self.assertEqual((block["vat_rate"], block["totals"]["net"]), ("0", "500.00"))


class WrittenCheck(unittest.TestCase):
    def test_listed_amount_against_the_printed_total(self):
        self.assertEqual(readback.amount_check("1,605.00", "1605.00"), {"state": "ok", "fields": []})
        self.assertEqual(readback.amount_check("321.00", "288.90"), {"state": "mismatch", "fields": ["amount"]})
        self.assertEqual(readback.amount_check("963.03", "963.00"), {"state": "ok", "fields": []})  # 两边各自四舍五入
        self.assertEqual(readback.amount_check("", "963.00"), {"state": "mismatch", "fields": ["unreadable"]})

    def test_the_verify_survives_the_receipt_whitelist(self):
        from pearnly_core.express_rules.push import written_check

        self.assertEqual(written_check.clean({"state": "mismatch", "fields": ["amount"]}),
                         {"state": "mismatch", "fields": ["amount"], "party": {}})


if __name__ == "__main__":
    unittest.main()
