"""MR.ERP 导入表的金额三件事与写后核对（共用层 `pearnly_core.mrerp.push`，直写与代录同一份；Cowork、ERP 同一份）。

11-14 在 MR.ERP 测试账套经 Cowork 推送真写查出（单号在 `docs/work/2026-11-14-mrerp-write-grid.md`），三张都报「已写入」：

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
    def test_goods_purchase_from_a_non_vat_seller_is_outside_the_vat_report(self):
        # 11-14 主会话：无税卖家开的不是税务发票，用 MR.ERP「不进税报告」那一档（初始库名 นอกระบบ），不用零税率「0」
        self.assertEqual(fmt.mrerp_tax_label(NON_VAT, purchase=True), "นอกระบบ")
        got = sheets(purchase.generate_xlsx_purchase([NON_VAT], MAPPINGS))
        self.assertEqual(got[1][1][2], "นอกระบบ")
        self.assertEqual(fmt.mrerp_tax_label(dict(NON_VAT, direction="sales")), "0")  # 销售税额 0 仍是零税率

    def test_names_come_from_the_companys_vat_rate_table(self):
        # 单据接口 txtothvatrate：[id, 名称, 税率, 价内外 1 价外 / 2 价内, 不进税报告]；名称改过的照这家公司的写
        rows = [["1", "ศูนย์", "0.00", None, None], ["2", "VAT7 นอก", "7.00", "1", None],
                ["3", "VAT7 ใน", "7.00", "2", None], ["4", "ไม่เข้าระบบ", "0.00", None, "1"]]
        rates = fmt.vat_rates(rows)
        self.assertEqual(rates, {"zero": "ศูนย์", "exclusive": "VAT7 นอก", "inclusive": "VAT7 ใน", "outside": "ไม่เข้าระบบ"})
        mappings = dict(MAPPINGS, _mrerp_vat_rates=rates)
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([NON_VAT], mappings))[1][1][2], "ไม่เข้าระบบ")
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([PURCHASE_DISCOUNT], mappings))[1][1][2], "VAT7 นอก")
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([NON_VAT], mappings, expense=True))[1][1][2], "ไม่เข้าระบบ")
        self.assertEqual(sheets(sales_credit._generate_xlsx_sales_credit_korn_clone([LINE_DISCOUNT], mappings))[1][1][2], "VAT7 นอก")

    def test_goods_purchase_with_vat_keeps_exclusive_and_expense_keeps_out_of_system(self):
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([PURCHASE_DISCOUNT], MAPPINGS))[1][1][2], "7 (แยก)")
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([NON_VAT], MAPPINGS, expense=True))[1][1][2], "นอกระบบ")


class BillNumber(unittest.TestCase):
    """第 11 列「เลขที่บิล」= MR.ERP 的税务发票号（税报告「ใบกำกับภาษี เลขที่」取它）：票面号原样；没有时 = 菜单前缀 + 单号
    （MR.ERP 网页不填的默认）；超过 20 字 MR.ERP 拒收，预检拦下不截（11-14 起不再写死「SI」+ 单号）。"""

    def test_sales_carry_the_printed_invoice_number(self):
        row = sheets(sales_credit._generate_xlsx_sales_credit_korn_clone([LINE_DISCOUNT], MAPPINGS))[1][1]
        self.assertEqual(row[10], "MW-a1")
        cash = dict(LINE_DISCOUNT, fields=dict(LINE_DISCOUNT["fields"], posting_payment_manual="cash"))
        self.assertEqual(sales_credit.build_sales_credit_row(cash, MAPPINGS)["bill_no"], "MW-a1")

    def test_without_a_printed_number_it_is_the_menu_prefix_and_the_document_number(self):
        bare = {**LINE_DISCOUNT, "invoice_no": "", "invoice_number": "",
                "fields": dict(LINE_DISCOUNT["fields"], invoice_number="")}
        docnum = sheets(sales_credit._generate_xlsx_sales_credit_korn_clone([bare], dict(MAPPINGS, _mrerp_menu_prefix="SE")))[1][1][0]
        row = sheets(sales_credit._generate_xlsx_sales_credit_korn_clone([bare], dict(MAPPINGS, _mrerp_menu_prefix="SE")))[1][1]
        self.assertEqual(row[10], "SE" + docnum)

    def test_a_number_longer_than_mrerp_takes_is_held_not_cut(self):
        long_no = "INV-2026-11-000000000123"  # 24 字
        purchase_ticket = dict(NON_VAT, invoice_no=long_no, invoice_number=long_no)
        self.assertEqual(purchase.validate_purchase_history(purchase_ticket, MAPPINGS)[:2], (False, "ERR_BILL_NO_TOO_LONG"))
        self.assertEqual(sheets(purchase.generate_xlsx_purchase([purchase_ticket], MAPPINGS))[1][1][10], long_no)


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
        self.assertEqual((block["vat_rate"], block["totals"]["net"]), ("นอกระบบ", "500.00"))


class Routing(unittest.TestCase):
    def test_an_explicit_credit_sale_without_a_buyer_stays_a_credit_sale(self):
        """无买方身份的销售只在没人明确选赊账时才算散客现销（11-14 ERP 真写：未收款、客户没税号的单被改成现销）。"""
        from datetime import date
        from pearnly_core.mrerp.push import routing

        walkin = ticket("b1", "sales", [{"name": "A", "qty": "1", "price": "200", "subtotal": "200"}],
                        subtotal="200.00", vat="14.00", total_amount="214.00")
        walkin["fields"]["buyer_tax"] = ""
        credit = dict(walkin, fields=dict(walkin["fields"], posting_payment_manual="credit"))
        groups, _ = routing.group_by_doc_type([walkin, credit], {"_own_tax_id": OWN}, "sales_credit",
                                              today=date(2026, 11, 14))
        self.assertEqual({k: [h["id"] for h in v] for k, v in groups.items()},
                         {"sales_cash": [walkin["id"]], "sales_credit": [credit["id"]]})


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


class CashPurchase(unittest.TestCase):
    """现购（11-14）：导入第 9 列记账用写现金供应商、第 10 列开票用写卖家；没定下现金供应商不写（测试账套真写：
    记账用 CODE_NOAP、开票用卖家，日记账贷 1111-02 现金）。"""

    def test_cash_purchase_sheet_books_the_cash_supplier_and_bills_the_seller(self):
        cash = dict(NON_VAT, fields=dict(NON_VAT["fields"], posting_payment_manual="cash"))
        got = sheets(purchase.generate_xlsx_purchase([cash], dict(MAPPINGS, _mrerp_cash_supplier="CODE_NOAP")))
        self.assertEqual((got[1][1][8], got[1][1][9]), ("CODE_NOAP", SUPPLIER))
        credit = sheets(purchase.generate_xlsx_purchase([NON_VAT], dict(MAPPINGS, _mrerp_cash_supplier="CODE_NOAP")))
        self.assertEqual((credit[1][1][8], credit[1][1][9]), (SUPPLIER, SUPPLIER))

    def test_cash_purchase_without_a_cash_supplier_is_not_written(self):
        cash = dict(NON_VAT, fields=dict(NON_VAT["fields"], posting_payment_manual="cash"))
        self.assertEqual(purchase.validate_expense_history(cash, MAPPINGS)[:2],
                         (False, "ERR_MRERP_CASH_SUPPLIER_NOT_CREATED"))


if __name__ == "__main__":
    unittest.main()
