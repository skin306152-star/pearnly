"""ภ.ง.ด.3 / ภ.ง.ด.53 草稿（`tax.wht`）的纯函数检查：不连库、不依赖 Frappe。

归哪张表（0 开头 → 53，0991 / 0992 → 3）、ชื่อ / ชื่อสกุล、付款金额与税额、50 ทวิ 的付款方判断与配对
（一对一、几张票一张凭证、一张票几张凭证；赊账票与 90 天内的凭证；配不上待核；跨月只计一次）、票面没有本公司税号的待核、
按付款日落月、所得类型「记住」与兜底、逐行改动、建议补扣不进合计、ใบแนบ 分页（每页 6 名、每名 3 行）。
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "pearnly_cowork"), str(ROOT / "apps" / "pearnly_core")]

from pearnly_cowork.contracts import document_policy  # noqa: E402
from pearnly_cowork.tax import periods, wht  # noqa: E402

COMPANY = "0105556000009"
TRANSPORT = "0105546015062"     # 法人
LANDLORD = "3100500000003"      # 个人
CUSTOMER = "0105536000011"
GROUP = "0992000000004"         # คณะบุคคล（税务局号）


def invoice(ref, seller=TRANSPORT, date_="2026-09-05", subtotal="10000", vat="700", wht_amount="300",
            wht_rate="", buyer=COMPANY, **fields):
    return {"ref": ref, "origin": "history", "fields": {
        "document_type": fields.pop("document_type", "tax_invoice"), "invoice_number": fields.pop("number", ref),
        "date": date_, "seller_name": fields.pop("seller_name", "Transport Co"), "seller_tax": seller,
        "buyer_name": "Our Co", "buyer_tax": buyer, "subtotal": subtotal, "vat": vat,
        "total_amount": fields.pop("total", str(Decimal(subtotal) + Decimal(vat))), "wht_amount": wht_amount,
        "wht_rate": wht_rate, **fields}}


def certificate(ref, payee=LANDLORD, date_="2026-09-20", base="20000", tax="1000", payee_side="buyer",
                name="นายสมชาย ใจดี", origin="reference"):
    payer_side = "seller" if payee_side == "buyer" else "buyer"
    return {"ref": ref, "origin": origin, "fields": {
        "document_flag": "withholding_certificate", "date": date_, f"{payer_side}_tax": COMPANY,
        f"{payer_side}_name": "Our Co", f"{payee_side}_tax": payee, f"{payee_side}_name": name,
        "subtotal": base, "wht_amount": tax}}


def draft(documents, memory=None, overrides=None, remembered=(), month=9):
    payments, notes = wht.collect(documents, COMPANY, remembered)
    return wht.draft(payments, notes, 2026, month, memory or {}, overrides or {})


def all_lines(result):
    return [line for form in wht.FORMS for line in result["forms"][form]["lines"]]


def fetched(documents, month):
    """同 `sources.wht_documents`：票据日 / 凭证日在 [月初 − 90 天, 月末 + FORWARD_DAYS] 的记录。"""
    start, end = periods.bounds(2026, month)
    return [item for item in documents if start - timedelta(days=wht.LOOKBACK_DAYS)
            <= periods.parse_date(item["fields"]["date"]) <= end + timedelta(days=wht.FORWARD_DAYS)]


def monthly_tax(documents, months):
    return {month: draft(fetched(documents, month), month=month)["totals"]["tax"] for month in months}


class Forms(unittest.TestCase):
    def test_the_tax_id_picks_the_form(self):
        self.assertEqual(wht.form_for(TRANSPORT), "pnd53")
        self.assertEqual(wht.form_for("0993000000001"), "pnd53")       # 税务局号：法人
        self.assertEqual(wht.form_for(GROUP), "pnd3")                   # คณะบุคคล / ห้างหุ้นส่วนสามัญ
        self.assertEqual(wht.form_for("0991000000001"), "pnd3")         # 外籍个人、未分割遗产
        self.assertEqual(wht.form_for(LANDLORD), "pnd3")
        self.assertEqual(wht.form_for(LANDLORD, "pnd53"), "pnd53")      # 会计改过的为准
        self.assertEqual(wht.form_for(""), "")
        self.assertEqual(wht.form_for("105546015062"), "pnd53")         # 读丢开头 0 补回

    def test_names_split_for_pnd3(self):
        self.assertEqual(wht.split_name("นายสมชาย ใจดี"), ("นาย", "สมชาย", "ใจดี"))
        self.assertEqual(wht.split_name("นางสาว มาลี  ศรีสุข"), ("นางสาว", "มาลี", "ศรีสุข"))
        self.assertEqual(wht.split_name("Mr. John Smith"), ("Mr.", "John", "Smith"))
        self.assertEqual(wht.split_name("คณะบุคคล ร้านดี"), ("", "คณะบุคคล ร้านดี", ""))
        self.assertTrue(wht.name_suggests_individual("ห้างหุ้นส่วนสามัญ ทดสอบ"))
        self.assertFalse(wht.name_suggests_individual("ห้างหุ้นส่วนสามัญนิติบุคคล ทดสอบ"))


class Amounts(unittest.TestCase):
    def test_base_is_the_amount_before_vat(self):
        self.assertEqual(wht.base_of({"subtotal": "10000", "vat": "700", "total_amount": "10700"}), Decimal("10000.00"))
        self.assertEqual(wht.base_of({"subtotal": "10000", "discount": "500", "vat": "665", "total_amount": "10165"}),
                         Decimal("9500.00"))
        self.assertEqual(wht.base_of({"subtotal": "9500", "discount": "500", "vat": "665", "total_amount": "10165"}),
                         Decimal("9500.00"))
        self.assertEqual(wht.base_of({"total_amount": "10700", "vat": "700"}), Decimal("10000.00"))
        self.assertIsNone(wht.base_of({}))

    def test_withheld_tax_from_the_document(self):
        self.assertEqual(wht.withheld({"wht_amount": "300"}, Decimal("10000")), (Decimal(3), Decimal("300.00")))
        self.assertEqual(wht.withheld({"wht_rate": "5"}, Decimal("1234.50")), (Decimal("5.00"), Decimal("61.73")))
        self.assertEqual(wht.withheld({"wht_amount": "123"}, Decimal("10000")), (Decimal("1.23"), Decimal("123.00")))
        self.assertEqual(wht.withheld({}, Decimal("10000")), (None, None))

    def test_paid_at_issue(self):
        self.assertTrue(wht.paid_at_issue({"payment_method": "cash"}))
        self.assertTrue(wht.paid_at_issue({"document_type": "receipt"}))
        self.assertTrue(wht.paid_at_issue({"posting_payment_manual": "cash"}))
        self.assertFalse(wht.paid_at_issue({"document_type": "tax_invoice"}))
        # 录入时人工选的赊账优先于票面印的付款方式与票种
        self.assertFalse(wht.paid_at_issue({"posting_payment_manual": "credit", "payment_method": "transfer",
                                            "document_type": "receipt"}))
        line = all_lines(draft([invoice("H1", payment_method="transfer", posting_payment_manual="credit")]))[0]
        self.assertEqual((line["date_source"], line["issues"][0]["code"]), ("invoice_date", "payment_date_assumed"))

    def test_rates_are_written_without_exponents(self):
        self.assertEqual([wht.rate_text(Decimal(value)) for value in ("10", "1.50", "3.00", "15")],
                         ["10", "1.5", "3", "15"])
        result = draft([invoice("D1", subtotal="50000", vat="0", wht_amount="5000")])
        self.assertEqual([line["rate"] for line in all_lines(result)], ["10"])
        self.assertEqual([question["rates"] for question in result["questions"]], [["10"]])


class Collect(unittest.TestCase):
    def test_expense_invoice_with_tax_withheld_is_a_payment(self):
        payments, notes = wht.collect([invoice("H1"), invoice("H2", wht_amount="")], COMPANY)
        self.assertEqual([(p["row"], p["payee_tax_id"], p["base"], p["tax"]) for p in payments],
                         [("H1", TRANSPORT, Decimal("10000.00"), Decimal("300.00"))])
        self.assertEqual(notes, [])

    def test_sales_invoices_are_not_ours_to_file(self):
        payments, _notes = wht.collect([invoice("H1", seller=COMPANY, buyer=CUSTOMER)], COMPANY)
        self.assertEqual(payments, [])

    def test_certificate_direction(self):
        documents = [
            invoice("H1", seller=TRANSPORT, wht_amount=""),                 # 本公司向 TRANSPORT 买过 → 付款方
            certificate("C1", payee=TRANSPORT, base="5000", tax="50"),
            invoice("H2", seller=COMPANY, buyer=CUSTOMER, wht_amount="30"),  # 本公司卖给 CUSTOMER
            certificate("C2", payee=CUSTOMER, payee_side="seller", base="1000", tax="30"),  # 字段反着放也认
            certificate("C3", payee=LANDLORD),                               # 没有往来 → 待确认
            certificate("C4", payee=GROUP),                                  # 记过所得类型 → 付款方
        ]
        payments, notes = wht.collect(documents, COMPANY, remembered_payees=[GROUP])
        by_row = {p["row"]: p for p in payments}
        self.assertTrue(by_row["C1"]["include"])
        self.assertNotIn("C2", by_row)
        self.assertEqual([n["code"] for n in notes], ["certificate_received"])
        self.assertFalse(by_row["C3"]["include"])
        self.assertEqual([i["code"] for i in by_row["C3"]["issues"]], ["certificate_direction_unclear"])
        self.assertTrue(by_row["C4"]["include"])

    def test_certificate_not_naming_the_company_is_another_clients(self):
        other = certificate("C1")
        other["fields"]["seller_tax"] = "0105556000099"
        self.assertEqual(wht.collect([other], COMPANY), ([], []))
        broken = certificate("C2", payee="")
        _payments, notes = wht.collect([broken], COMPANY)
        self.assertEqual([n["code"] for n in notes], ["certificate_parties_unreadable"])

    def test_unreadable_certificate_amount_is_left_out(self):
        result = draft([certificate("C1", base="", tax="")], remembered=[LANDLORD])
        self.assertEqual(all_lines(result), [])
        self.assertEqual([i["code"] for i in result["review"][0]["issues"]][:1], ["amount_unreadable"])

    def test_an_unreadable_certificate_counts_once_the_amounts_are_entered(self):
        overrides = {"C1": wht.clean_edit({"base": "20000", "tax": "1000"})}
        result = draft([certificate("C1", base="", tax="")], remembered=[LANDLORD], overrides=overrides)
        line, = all_lines(result)
        self.assertEqual((line["base"], line["tax"], line["rate"]), ("20000.00", "1000.00", "5"))
        self.assertNotIn("amount_unreadable", [item["code"] for item in line["issues"]])
        self.assertEqual(result["review"], [])

    def test_a_partner_that_buys_and_sells_needs_the_accountant(self):
        documents = [invoice("P1", wht_amount="", date_="2026-08-10"),                    # 本公司向 TRANSPORT 买过
                     invoice("S1", seller=COMPANY, buyer=TRANSPORT, number="S1", wht_amount=""),  # 也卖给它
                     certificate("C9", payee=TRANSPORT, base="40000", tax="1200")]
        for documents_ in (documents, documents[:2] + [certificate("C9", payee=TRANSPORT, payee_side="seller",
                                                                   base="40000", tax="1200")]):
            payments, notes = wht.collect(documents_, COMPANY)
            self.assertEqual([(p["row"], p["include"], [i["code"] for i in p["issues"]]) for p in payments],
                             [("C9", False, ["certificate_direction_unclear"])])
            self.assertEqual(notes, [])
        self.assertEqual(draft(documents)["totals"]["tax"], "0.00")
        # 与本公司印了预扣税的费用票配上了即认定本公司付款
        paired = documents + [invoice("P2", subtotal="40000", vat="2800", wht_amount="1200", date_="2026-09-10")]
        payments, _ = wht.collect(paired, COMPANY)
        self.assertEqual([(p["row"], p["source"], p["include"], p["issues"]) for p in payments],
                         [("P2", "both", True, [])])

    def test_documents_filed_under_the_company_without_its_tax_id_wait_for_the_accountant(self):
        receipt = invoice("R1", seller=LANDLORD, buyer="", document_type="receipt", subtotal="10000", vat="0",
                          wht_amount="500", seller_name="นายสมชาย ใจดี")
        receipt["match"] = wht.MATCH_CLIENT
        sale = invoice("S1", seller="", buyer="", direction="sales")
        sale["match"] = wht.MATCH_WORKSPACE
        unknown = invoice("U1", seller=LANDLORD, buyer="", number="U1")   # 没有归属：不取
        result = draft([receipt, sale, unknown])
        self.assertEqual(all_lines(result), [])
        self.assertEqual([(row["row"], [item["code"] for item in row["issues"]]) for row in result["review"]],
                         [("R1", ["buyer_unconfirmed", "income_type_missing"])])
        result = draft([receipt], overrides={"R1": wht.clean_edit({"include": True})})
        line, = result["forms"]["pnd3"]["lines"]
        self.assertEqual((line["row"], [item["code"] for item in line["issues"]]), ("R1", ["income_type_missing"]))
        # 与写着本公司的 50 ทวิ 配上了即确认
        paired = [receipt, certificate("C1", payee=LANDLORD, date_="2026-09-10", base="10000", tax="500")]
        line, = all_lines(draft(paired))
        self.assertEqual((line["refs"], line["source"]), (["R1", "C1"], "both"))

    def test_duplicates_count_once(self):
        documents = [invoice("H1", number="INV-1"), invoice("H2", number=" inv-1"),
                     certificate("C1"), certificate("C2")]
        payments, notes = wht.collect(documents, COMPANY, [LANDLORD])
        self.assertEqual(sorted(p["row"] for p in payments), ["C1", "H1"])
        self.assertEqual([(n["code"], n["ref"], n["of"]) for n in notes],
                         [("duplicate_document", "H2", "H1"), ("duplicate_document", "C2", "C1")])


class NameMatchAndExcluded(unittest.TestCase):
    """2026-10-13 测试机：多公司工作空间里个人开的收据没读出买方税号、买方名称就是本公司 → 原先整张悄悄没了。"""

    PERSON = "1990100000029"

    def test_a_receipt_naming_the_company_as_buyer_waits_for_the_accountant_or_its_certificate(self):
        receipt = invoice("R1", seller=self.PERSON, buyer="", document_type="receipt", subtotal="10000", vat="0",
                          wht_amount="300", seller_name="นางสาวมะลิ ดีงาม")
        receipt["match"] = wht.MATCH_NAME
        result = draft([receipt])
        self.assertEqual(all_lines(result), [])
        self.assertEqual([(row["row"], [item["code"] for item in row["issues"]]) for row in result["review"]],
                         [("R1", ["buyer_unconfirmed", "income_type_missing"])])
        # 与写着本公司付款的 50 ทวิ 配上了即确认（同现有配对规则）
        paired = [receipt, certificate("C1", payee=self.PERSON, date_="2026-09-12", base="10000", tax="300")]
        line, = all_lines(draft(paired))
        self.assertEqual((line["refs"], line["source"], line["form"]), (["R1", "C1"], "both", wht.PND3))
        # 按名称认到、但是销售单的不取
        sale = invoice("S1", seller="", buyer="", direction="sales")
        sale["match"] = wht.MATCH_NAME
        self.assertEqual(wht.collect([sale], COMPANY), ([], []))

    def test_every_document_of_the_month_read_but_not_counted_says_why(self):
        other = certificate("C9")
        other["fields"]["seller_tax"] = "0105556000099"   # 别家公司的凭证
        documents = [
            invoice("H1"),                                                    # 计入
            invoice("H1D", number="H1"),                                      # 重复：在说明里
            invoice("H2", number="H2", wht_amount=""),                        # 没有预扣
            invoice("S1", seller=COMPANY, buyer=CUSTOMER, number="S1"),       # 本公司的销售单
            other,
            invoice("I3", seller=LANDLORD, seller_name="นายสมชาย ใจดี", subtotal="5000", vat="0", wht_amount="250",
                    date_="2026-09-25"),                                      # 10 月才付（凭证 10 月）
            certificate("C3", payee=LANDLORD, date_="2026-10-05", base="5000", tax="250"),
            invoice("H0", number="H0", wht_amount="", date_="2026-08-10"),    # 上个月的：不在本月列
        ]
        skipped = {}
        payments, notes = wht.collect(documents, COMPANY, (), skipped)
        result = wht.draft(payments, notes, 2026, 9, {}, {}, documents, skipped)
        self.assertEqual([line["row"] for line in all_lines(result)], ["H1"])
        self.assertEqual([(item["ref"], item["reason"]) for item in result["excluded"]],
                         [("I3", "paid_in_other_month"), ("C9", "certificate_other_company"),
                          ("H2", "nothing_withheld"), ("S1", "sales_document")])
        paid_later = result["excluded"][0]
        self.assertEqual((paid_later["payment_date"], paid_later["seller_tax"], paid_later["wht_amount"]),
                         ("2026-10-05", LANDLORD, "250"))
        self.assertEqual(result["excluded"][1]["document_type"], "withholding_certificate")
        # 本月读到的每一张都有去处：表里、待核、建议、说明或 excluded
        placed = {ref for line in all_lines(result) for ref in line["refs"]}
        placed |= {ref for key in ("review", "suggestions") for row in result[key] for ref in row["refs"]}
        placed |= {note["ref"] for note in result["notes"]} | {item["ref"] for item in result["excluded"]}
        this_month = {item["ref"] for item in documents if item["fields"]["date"].startswith("2026-09")}
        self.assertEqual(this_month - placed, set())
        # 会计把付款日改到别的月：同样列出、带改后的付款日；不给记录就不列（老调用方）
        moved = wht.draft(payments, notes, 2026, 9, {}, {"H1": wht.clean_edit({"payment_date": "2026-11-02"})},
                          documents, skipped)
        self.assertEqual([(item["ref"], item["reason"], item.get("payment_date")) for item in moved["excluded"]][:1],
                         [("H1", "paid_in_other_month", "2026-11-02")])
        self.assertEqual(draft(documents)["excluded"], [])


class Pairing(unittest.TestCase):
    def test_invoice_and_certificate_are_one_payment(self):
        documents = [invoice("H1", date_="2026-08-28", wht_amount="300"),
                     certificate("C1", payee=TRANSPORT, date_="2026-09-03", base="10000.01", tax="300")]
        payments, _ = wht.collect(documents, COMPANY)
        self.assertEqual(len(payments), 1)
        paid = payments[0]
        self.assertEqual((paid["source"], paid["refs"], paid["date"], paid["date_source"]),
                         ("both", ["H1", "C1"], date(2026, 9, 3), "certificate"))
        # 按付款日落月：票据 8 月、凭证 9 月 → 9 月
        self.assertEqual(len(all_lines(draft(documents, month=9))), 1)
        self.assertEqual(all_lines(draft(documents, month=8)), [])

    def test_too_far_apart_or_different_amounts_stay_apart(self):
        def apart(paid, day, certificate_day="2026-09-01"):
            documents = [invoice("H1", date_=day, **paid),
                         certificate("C1", payee=TRANSPORT, date_=certificate_day, base="10000", tax="300")]
            return len(wht.collect(documents, COMPANY)[0]) == 2

        cash, credit = {"payment_method": "cash"}, {"posting_payment_manual": "credit"}
        # 当场付了的票：与 62 天后的凭证是两笔
        self.assertTrue(apart(cash, "2026-07-01"))
        # 赊账票（付款日只是推定）：62 天后（常见 60 天账期）的凭证就是这张票的付款，配成一笔
        self.assertFalse(apart(credit, "2026-07-01"))
        self.assertFalse(apart(credit, "2026-06-03"))      # 90 天
        self.assertTrue(apart(credit, "2026-06-02"))       # 91 天
        self.assertTrue(apart(credit, "2026-10-17"))       # 凭证在票据日 46 天以前
        other = [invoice("H1"), certificate("C1", payee=TRANSPORT, base="7000", tax="210")]
        self.assertEqual(len(wht.collect(other, COMPANY)[0]), 2)

    def test_a_credit_invoice_paid_sixty_days_later_is_counted_once(self):
        # 7 月赊账、9 月付款开凭证：7 月的草稿取得到这张凭证、9 月的取得到这张票，两边都配成一笔，按凭证日只算在 9 月
        for day, certificate_day in (("2026-07-10", "2026-09-05"), ("2026-07-01", "2026-09-28")):
            documents = [invoice("I1", date_=day, posting_payment_manual="credit"),
                         certificate("C1", payee=TRANSPORT, date_=certificate_day, base="10000", tax="300")]
            self.assertEqual(monthly_tax(documents, (7, 8, 9)), {7: "0.00", 8: "0.00", 9: "300.00"})
            line, = all_lines(draft(fetched(documents, 9)))
            self.assertEqual((line["refs"], line["date_source"]), (["I1", "C1"], "certificate"))

    def test_a_credit_invoice_whose_certificate_went_to_a_later_invoice_waits_for_the_accountant(self):
        # 7 月、8 月两张同额赊账票，只有一张 9 月的凭证：凭证配给日期近的 8 月票；7 月的票不按票据日悄悄计入，也不丢，
        # 放进 7 月待核（列出那张凭证）
        documents = [invoice("I1", date_="2026-07-10", number="INV-7", posting_payment_manual="credit"),
                     invoice("I2", date_="2026-08-20", number="INV-8", posting_payment_manual="credit"),
                     certificate("C2", payee=TRANSPORT, date_="2026-09-05", base="10000", tax="300")]
        self.assertEqual(monthly_tax(documents, (7, 8, 9)), {7: "0.00", 8: "0.00", 9: "300.00"})
        july = draft(fetched(documents, 7), month=7)
        (row,) = july["review"]
        self.assertEqual((row["row"], next(item["candidates"] for item in row["issues"]
                                           if item["code"] == "pairing_unclear")), ("I1", ["C2"]))
        line, = all_lines(draft(fetched(documents, 9)))
        self.assertEqual(line["refs"], ["I2", "C2"])

    def test_the_invoice_fills_an_amount_the_certificate_did_not_show(self):
        documents = [invoice("H1"), certificate("C1", payee=TRANSPORT, base="", tax="300")]
        line, = all_lines(draft(documents))
        self.assertEqual((line["source"], line["base"], line["tax"]), ("both", "10000.00", "300.00"))
        self.assertNotIn("amount_unreadable", [item["code"] for item in line["issues"]])

    def test_certificate_confirms_an_invoice_with_nothing_printed(self):
        documents = [invoice("H1", wht_amount=""), certificate("C1", payee=TRANSPORT, base="10000", tax="300")]
        payments, _ = wht.collect(documents, COMPANY, [TRANSPORT])
        self.assertEqual([(p["source"], p["include"], p["tax"]) for p in payments], [("both", True, Decimal("300.00"))])

    def test_several_invoices_paid_with_one_certificate_count_once(self):
        documents = [invoice("I1", date_="2026-09-02"), invoice("I2", date_="2026-09-05"),
                     certificate("C1", payee=TRANSPORT, date_="2026-09-20", base="20000", tax="600")]
        result = draft(documents)
        line, = all_lines(result)
        self.assertEqual((line["row"], line["refs"], line["source"], line["base"], line["tax"], line["rate"],
                          line["date"]), ("C1", ["C1", "I1", "I2"], "both", "20000.00", "600.00", "3", "2026-09-20"))
        self.assertEqual((result["totals"]["tax"], result["review"]), ("600.00", []))

    def test_one_invoice_paid_in_two_instalments(self):
        documents = [invoice("I1", subtotal="20000", vat="1400", wht_amount="600", date_="2026-09-01"),
                     certificate("C1", payee=TRANSPORT, date_="2026-09-10", base="12000", tax="360"),
                     certificate("C2", payee=TRANSPORT, date_="2026-10-10", base="8000", tax="240")]
        self.assertEqual([(line["row"], line["refs"], line["tax"]) for line in all_lines(draft(documents))],
                         [("C1", ["C1", "I1"], "360.00")])
        self.assertEqual([(line["row"], line["tax"]) for line in all_lines(draft(documents, month=10))],
                         [("C2", "240.00")])

    def test_a_certificate_next_month_is_counted_once_in_its_month(self):
        documents = [invoice("I1", date_="2026-08-20"),
                     certificate("C1", payee=TRANSPORT, date_="2026-09-05", base="10000", tax="300")]
        self.assertEqual(monthly_tax(documents, (8, 9)), {8: "0.00", 9: "300.00"})

    def test_unmatched_invoice_and_certificate_of_one_payee_wait_for_the_accountant(self):
        documents = [invoice("I1", date_="2026-09-02"),
                     certificate("C1", payee=TRANSPORT, date_="2026-09-20", base="9000", tax="270")]
        result = draft(documents)
        self.assertEqual((all_lines(result), result["totals"]["tax"]), ([], "0.00"))
        unclear = {row["row"]: next(item for item in row["issues"] if item["code"] == "pairing_unclear")
                   for row in result["review"]}
        self.assertEqual({row: item["candidates"] for row, item in unclear.items()}, {"I1": ["C1"], "C1": ["I1"]})
        self.assertEqual(unclear["I1"]["level"], "warn")
        # 会计确认凭证这一笔：只计入它，提示去掉
        result = draft(documents, overrides={"C1": wht.clean_edit({"include": True})})
        line, = all_lines(result)
        self.assertEqual((line["row"], line["tax"]), ("C1", "270.00"))
        self.assertNotIn("pairing_unclear", [item["code"] for item in line["issues"]])
        self.assertEqual([row["row"] for row in result["review"]], ["I1"])
        # 日期远的不算可能重复
        far = [invoice("I1", date_="2026-07-01"), certificate("C1", payee=TRANSPORT, date_="2026-09-20", base="9000",
                                                              tax="270")]
        self.assertEqual([line["row"] for line in all_lines(draft(far))], ["C1"])

    def test_an_invoice_near_another_payments_certificate_waits_for_the_accountant(self):
        # 那张凭证配上了别的票；但各月取到的单据不同时也可能配到这张票：先不按票据日计入
        documents = [invoice("I1", date_="2026-07-08", subtotal="2000", wht_amount="100"),
                     invoice("I2", date_="2026-07-20", subtotal="3000", wht_amount="90"),
                     certificate("C1", payee=TRANSPORT, date_="2026-08-02", base="3000", tax="90")]
        july = draft(documents, month=7)
        self.assertEqual(all_lines(july), [])
        (row,) = july["review"]
        self.assertEqual((row["row"], [(item["code"], item.get("candidates")) for item in row["issues"]][:1]),
                         ("I1", [("pairing_unclear", ["C1"])]))
        line, = all_lines(draft(documents, month=8))
        self.assertEqual((line["refs"], line["tax"], [item["code"] for item in line["issues"]]),
                         (["I2", "C1"], "90.00", ["income_type_missing"]))

    def test_edits_on_a_merged_invoice_keep_the_certificate_amounts_and_date(self):
        documents = [invoice("I1", date_="2026-09-02"), invoice("I2", date_="2026-09-05"),
                     certificate("C1", payee=TRANSPORT, date_="2026-09-20", base="20000", tax="600")]
        overrides = {"I1": wht.clean_edit({"base": "1", "tax": "1", "payment_date": "2026-10-01",
                                           "income_type": "service"})}
        line, = all_lines(draft(documents, overrides=overrides))
        self.assertEqual((line["base"], line["tax"], line["date"], line["income_type"]),
                         ("20000.00", "600.00", "2026-09-20", "service"))

    def test_the_sum_search_is_bounded(self):
        rows = [{"tax": Decimal(7 + 2 * index)} for index in range(40)]
        self.assertEqual(wht._subset(rows, "tax", Decimal("16.00")), [0, 1])
        self.assertEqual(wht._subset(rows, "tax", Decimal("1.00")), [])
        evens = [{"tax": Decimal("2.00")} for _ in range(40)]
        self.assertEqual(wht._subset(evens, "tax", Decimal("41.00")), [])   # 步数上限，不会一直搜


class Draft(unittest.TestCase):
    def test_forms_totals_and_questions(self):
        result = draft([invoice("H1"), certificate("C1")], memory={"payee:" + LANDLORD: {"income_type": "rent"}},
                       remembered=[LANDLORD])
        self.assertEqual([line["row"] for line in result["forms"]["pnd53"]["lines"]], ["H1"])
        self.assertEqual([line["row"] for line in result["forms"]["pnd3"]["lines"]], ["C1"])
        rent = result["forms"]["pnd3"]["lines"][0]
        self.assertEqual((rent["income_type"], rent["type_source"], rent["name_first"], rent["name_last"]),
                         ("rent", "payee", "สมชาย", "ใจดี"))
        self.assertEqual(result["questions"], [{"payee_tax_id": TRANSPORT, "payee_name": "Transport Co", "category": "",
                                                "rows": 1, "base": "10000.00", "tax": "300.00", "rates": ["3"],
                                                "suggested": "service"}])
        self.assertEqual(result["totals"], {"base": "30000.00", "tax": "1300.00", "lines": 2, "review": 0,
                                            "suggestions": 0})
        self.assertEqual((result["forms"]["pnd53"]["base"], result["forms"]["pnd3"]["tax"]), ("10000.00", "1000.00"))

    def test_category_is_the_fallback_memory(self):
        result = draft([invoice("H1", category="ค่าขนส่ง")], memory={"category:ค่าขนส่ง": {"income_type": "transport"}})
        line = all_lines(result)[0]
        self.assertEqual((line["income_type"], line["type_source"]), ("transport", "category"))
        result = draft([invoice("H1", category="ค่าขนส่ง")],
                       memory={"category:ค่าขนส่ง": {"income_type": "transport"},
                               "payee:" + TRANSPORT: {"income_type": "service"}})
        self.assertEqual(all_lines(result)[0]["type_source"], "payee")

    def test_payment_date_is_assumed_for_credit_invoices(self):
        line = all_lines(draft([invoice("H1")]))[0]
        self.assertEqual((line["date_source"], [i["code"] for i in line["issues"]][0]),
                         ("invoice_date", "payment_date_assumed"))
        line = all_lines(draft([invoice("H1", payment_method="transfer")]))[0]
        self.assertEqual(line["date_source"], "paid_at_issue")

    def test_rate_checks(self):
        memory = {"payee:" + TRANSPORT: {"income_type": "service"}}
        line = all_lines(draft([invoice("H1", wht_amount="100")], memory=memory))[0]      # 1%：可能是 e-WHT
        self.assertIn("ewht_check", [i["code"] for i in line["issues"]])
        line = all_lines(draft([invoice("H1", wht_amount="200")], memory=memory))[0]      # 2% 服务费
        self.assertIn("rate_mismatch", [i["code"] for i in line["issues"]])
        line = all_lines(draft([invoice("H1", wht_amount="123")], memory=memory))[0]
        self.assertIn("rate_not_standard", [i["code"] for i in line["issues"]])
        line = all_lines(draft([invoice("H1", subtotal="900", vat="63", wht_amount="27")], memory=memory))[0]
        self.assertIn("below_threshold", [i["code"] for i in line["issues"]])

    def test_form_checks(self):
        memory = {"payee:" + LANDLORD: {"income_type": "interest", "condition": "3"}}
        line = all_lines(draft([certificate("C1")], memory=memory, remembered=[LANDLORD]))[0]
        self.assertEqual(line["form"], "pnd3")
        self.assertIn("form_type_mismatch", [i["code"] for i in line["issues"]])   # 利息不进 ภ.ง.ด.3
        memory = {"payee:" + TRANSPORT: {"income_type": "service", "condition": "3"}}
        line = all_lines(draft([invoice("H1")], memory=memory))[0]
        self.assertIn("condition_invalid", [i["code"] for i in line["issues"]])    # ภ.ง.ด.53 只有 1 / 2
        named = invoice("H1", seller_name="ห้างหุ้นส่วนสามัญ ทดสอบ")
        line = all_lines(draft([named]))[0]
        self.assertIn("form_name_mismatch", [i["code"] for i in line["issues"]])

    def test_payee_without_tax_id_cannot_be_filed(self):
        result = draft([invoice("H1", seller="")])
        self.assertEqual(all_lines(result), [])
        self.assertEqual(result["review"][0]["issues"][0]["code"], "payee_tax_missing")
        self.assertEqual(result["questions"], [])

    def test_edits_override_one_row(self):
        overrides = {"H1": wht.clean_edit({"income_type": "rent", "rate": "5", "payment_date": "2026-10-02"}),
                     "C1": wht.clean_edit({"include": True})}
        result = draft([invoice("H1"), certificate("C1")], overrides=overrides, month=9)
        self.assertEqual([line["row"] for line in all_lines(result)], ["C1"])   # H1 改到 10 月付
        result = draft([invoice("H1"), certificate("C1")], overrides=overrides, month=10)
        line = all_lines(result)[0]
        self.assertEqual((line["income_type"], line["rate"], line["tax"], line["edited"]),
                         ("rent", "5", "500.00", ["income_type", "payment_date", "rate"]))

    def test_clean_edit_refuses_bad_values(self):
        for edit in ({"include": "yes"}, {"payment_date": "next week"}, {"income_type": "salary"}, {"rate": "-1"},
                     {"rate": "101"}, {"condition": "4"}, {"payee_tax_id": "123"}, {"form": "pnd1"}):
            with self.assertRaises(ValueError, msg=edit):
                wht.clean_edit(edit)
        self.assertEqual(wht.clean_edit({"row": "H1", "tax": "1,000.5", "payee_tax_id": "105546015062", "x": 1}),
                         {"tax": "1000.50", "payee_tax_id": TRANSPORT})

    def test_suggestions_are_listed_not_counted(self):
        memory = {"payee:" + TRANSPORT: {"income_type": "transport"}}
        documents = [invoice("H1", wht_amount=""), invoice("H2", wht_amount="", subtotal="500", vat="35")]
        result = draft(documents, memory=memory, remembered=[TRANSPORT])
        self.assertEqual(all_lines(result), [])
        self.assertEqual([(row["row"], row["rate"], row["tax"]) for row in result["suggestions"]],
                         [("H1", "1", "100.00")])
        self.assertEqual(result["totals"]["tax"], "0.00")
        result = draft(documents, memory=memory, remembered=[TRANSPORT],
                       overrides={"H1": wht.clean_edit({"include": True})})
        self.assertEqual([(line["row"], line["tax"]) for line in all_lines(result)], [("H1", "100.00")])
        self.assertEqual(result["totals"]["tax"], "100.00")


class Sheets(unittest.TestCase):
    def test_six_payees_a_sheet_and_three_lines_a_payee(self):
        documents = []
        for payee in range(7):
            seller = f"01055460{payee:05d}"
            for day in range(1, 5 if payee == 0 else 2):
                documents.append(invoice(f"H{payee}-{day}", seller=seller, seller_name=f"Payee {payee}",
                                         date_=f"2026-09-{day:02d}", number=f"N{payee}{day}"))
        lines = draft(documents)["forms"]["pnd53"]["lines"]
        payee_zero = [line for line in lines if line["payee_name"] == "Payee 0"]
        self.assertEqual([line["seq"] for line in payee_zero], [1, 1, 1, 2])   # 第 4 行另起一个序号
        self.assertEqual(max(line["seq"] for line in lines), 8)
        self.assertEqual({line["sheet"] for line in lines if line["seq"] <= 6}, {1})
        self.assertEqual({line["sheet"] for line in lines if line["seq"] > 6}, {2})
        result = draft(documents)["forms"]["pnd53"]
        self.assertEqual((result["sheets"], result["payees"]), (2, 7))
        self.assertEqual(sum(Decimal(entry["tax"]) for entry in result["sheet_totals"]), Decimal(result["tax"]))


class CertificatePolicy(unittest.TestCase):
    PAGES = [{"page_index": 1, "fields": {"document_flag": "withholding_certificate", "is_not_invoice": True}},
             {"page_index": 2, "fields": {"document_type": "payment_evidence"}},
             {"page_index": 3, "fields": {"document_type": "tax_invoice"}}]

    def test_only_skipped_certificates_are_kept_as_references(self):
        self.assertEqual(document_policy.skipped_certificates(self.PAGES, {}), [])
        self.assertEqual(document_policy.skipped_certificates(self.PAGES, {"withholding_certificate": "enter"}), [])
        found = document_policy.skipped_certificates(self.PAGES, {"withholding_certificate": "skip",
                                                                  "payment_evidence": "skip"})
        self.assertEqual([(position, page["page_index"]) for position, page in found], [(0, 1)])

    def test_the_skip_choice_still_makes_the_page_a_non_invoice(self):
        pages = [{"page_index": 1, "fields": {"document_flag": "withholding_certificate", "is_not_invoice": False}}]
        document_policy.apply_to_pages(pages, {"withholding_certificate": "skip"})
        self.assertTrue(pages[0]["fields"]["is_not_invoice"])
        self.assertEqual(len(document_policy.skipped_certificates(pages, {"withholding_certificate": "skip"})), 1)


# 2026-10-13 测试机两张真 50 ทวิ（扫描 PDF、手机照片）的读数：结果里没有原文，标题规则没跑，类别空着；
# 付款公司在卖方一栏，收款方（公司 / 个人）在买方一栏。
PAYER = "0105599210021"
PLAZA = "0105599220141"
PERSON = "1990100000029"
RENT_CERTIFICATE = {"is_not_invoice": True, "document_type": "other", "document_flag": "",
                    "invoice_number": "09/2569 เลขที่ 001", "date": "2026-09-02",
                    "seller_name": "บริษัท สยามทดสอบ จำกัด", "seller_tax": PAYER,
                    "buyer_name": "บริษัท สายสุข พลาซ่า จำกัด", "buyer_tax": PLAZA,
                    "total_amount": "25000.00", "wht_amount": "1250.00"}
PERSON_CERTIFICATE = {"is_not_invoice": True, "document_type": "other", "document_flag": "",
                      "invoice_number": "09/2569 เลขที่ 002", "date": "2026-09-30",
                      "seller_name": "บริษัท สยามทดสอบ จำกัด", "seller_tax": PAYER,
                      "buyer_name": "นายสมศักดิ์ ทดสอบ", "buyer_tax": PERSON, "wht_amount": "180.00"}


class CertificateShape(unittest.TestCase):
    """认 50 ทวิ 的共用规则（`contracts.document_policy`）：原文标题打的类别，或读数本身就是凭证的样子。"""

    def test_the_two_real_certificates_are_certificates(self):
        for fields in (RENT_CERTIFICATE, PERSON_CERTIFICATE, {**PERSON_CERTIFICATE, "is_not_invoice": "true"},
                       {**RENT_CERTIFICATE, "seller_tax": PAYER[1:]}):   # 读丢开头 0 的按共用规则补回
            self.assertTrue(document_policy.certificate_shape(fields), fields)
            self.assertEqual(document_policy.document_class(fields), "withholding_certificate")

    def test_other_pages_are_not(self):
        slip = {"is_not_invoice": True, "document_type": "other", "seller_tax": PAYER, "buyer_tax": PLAZA,
                "total_amount": "25000.00", "wht_amount": ""}
        self.assertEqual(document_policy.document_class(slip), "")                      # 转账凭条：没有预扣税额
        self.assertEqual(document_policy.document_class({**slip, "document_type": "payment_evidence"}),
                         "payment_evidence")
        invoice_ = {**RENT_CERTIFICATE, "is_not_invoice": False, "document_type": "tax_invoice"}
        self.assertEqual(document_policy.document_class(invoice_), "")                  # 印了预扣税额的票：不碰
        letter = {**RENT_CERTIFICATE, "buyer_tax": ""}
        self.assertEqual(document_policy.document_class(letter), "")                    # 只有一边有税号的信函
        for broken in ({**RENT_CERTIFICATE, "buyer_tax": "0105599220142"},              # 校验位不对
                       {**RENT_CERTIFICATE, "buyer_tax": PAYER},                         # 两边同一个号
                       {**RENT_CERTIFICATE, "wht_amount": "0.00"}, {**RENT_CERTIFICATE, "wht_amount": "n/a"}):
            self.assertEqual(document_policy.document_class(broken), "", broken)

    def test_the_tenant_choice_applies_like_a_flagged_certificate(self):
        pages = [{"page_index": 1, "fields": dict(RENT_CERTIFICATE)}]
        document_policy.apply_to_pages(pages, {"withholding_certificate": "skip"})
        fields = pages[0]["fields"]
        self.assertEqual((fields["is_not_invoice"], fields["document_flag"]), (True, "withholding_certificate"))
        self.assertEqual(len(document_policy.skipped_certificates(pages, {"withholding_certificate": "skip"})), 1)
        for policy, block in (({}, "document_policy_needed_withholding_certificate"),
                              ({"withholding_certificate": "enter"}, None)):
            pages = [{"page_index": 1, "fields": dict(PERSON_CERTIFICATE)}]
            document_policy.apply_to_pages(pages, policy)
            fields = pages[0]["fields"]
            # 算票（推送前没选的停下来问）；类别靠标记留着，不再靠「不是发票」
            self.assertEqual((fields["is_not_invoice"], fields["document_flag"]), (False, "withholding_certificate"))
            self.assertEqual(document_policy.push_block(fields, policy), block)
            self.assertEqual(document_policy.skipped_certificates(pages, policy), [])


class RealCertificatesPair(unittest.TestCase):
    """真凭证的读数（没有类别标记、付款公司在卖方一栏）与本公司的票配成一笔，不看租户选的录不录。"""

    def test_the_rent_certificate_pairs_with_the_plaza_invoice(self):
        rent = invoice("SP-RC-2609-045", seller=PLAZA, buyer=PAYER, date_="2026-09-02", subtotal="25000", vat="0",
                       wht_amount="1250", seller_name="บริษัท สายสุข พลาซ่า จำกัด")
        for origin in ("reference", "history"):
            certificate_ = {"ref": "C-RENT", "origin": origin, "fields": dict(RENT_CERTIFICATE)}
            payments, notes = wht.collect([rent, certificate_], PAYER)
            result = wht.draft(payments, notes, 2026, 9)
            line, = result["forms"]["pnd53"]["lines"]
            self.assertEqual((line["refs"], line["source"], line["include"], line["payee_tax_id"]),
                             (["SP-RC-2609-045", "C-RENT"], "both", True, PLAZA))
            self.assertEqual((line["base"], line["tax"], line["rate"], line["date"]),
                             ("25000.00", "1250.00", "5", "2026-09-02"))
            self.assertEqual((result["review"], result["notes"]), ([], []))

    def test_the_individuals_certificate_confirms_the_receipt_naming_the_company(self):
        receipt = invoice("1/14", seller=PERSON, buyer="", document_type="receipt", date_="2026-09-30",
                          subtotal="6000", vat="0", wht_amount="180", seller_name="นายสมศักดิ์ ทดสอบ",
                          buyer_name="บริษัท สยามทดสอบ จำกัด")
        receipt["match"] = wht.MATCH_NAME   # 买方税号空着、买方名称是本公司（`sources._company_names`）
        alone = wht.draft(*wht.collect([receipt], PAYER), 2026, 9)
        self.assertEqual([row["row"] for row in alone["review"]], ["1/14"])
        for fields in (PERSON_CERTIFICATE, {**PERSON_CERTIFICATE, "total_amount": "6000.00"}):
            result = wht.draft(*wht.collect([receipt, {"ref": "C-PERSON", "origin": "reference",
                                                       "fields": dict(fields)}], PAYER), 2026, 9)
            line, = result["forms"]["pnd3"]["lines"]
            self.assertEqual((line["refs"], line["source"], line["include"], line["payee_tax_id"]),
                             (["1/14", "C-PERSON"], "both", True, PERSON))
            self.assertEqual((line["base"], line["tax"], line["date"]), ("6000.00", "180.00", "2026-09-30"))
            self.assertNotIn("buyer_unconfirmed", [item["code"] for item in line["issues"]])
            self.assertEqual(result["review"], [])

    def test_a_certificate_read_as_not_an_invoice_is_listed_as_one(self):
        other = {**RENT_CERTIFICATE, "seller_tax": "0105556000009"}     # 别家公司付的
        skipped = {}
        documents = [{"ref": "C-OTHER", "origin": "history", "fields": other}]
        payments, notes = wht.collect(documents, PAYER, (), skipped)
        result = wht.draft(payments, notes, 2026, 9, {}, {}, documents, skipped)
        self.assertEqual([(item["ref"], item["reason"], item["document_type"]) for item in result["excluded"]],
                         [("C-OTHER", "certificate_other_company", "withholding_certificate")])


if __name__ == "__main__":
    unittest.main()
