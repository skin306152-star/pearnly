"""ADR-0012 共用票种决策表：回归样本、旧线上口径、各产品不漂移。纯离线，不需要 Frappe。"""
import ast
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.tax_documents import codes, rules  # noqa: E402

FIXTURE = json.loads((ROOT / "tests/fixtures/tax-documents/cases.json").read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]


class Samples(unittest.TestCase):
    def test_every_sample_gets_its_expected_treatment(self):
        self.assertGreaterEqual(len([case for case in CASES if case["source"] == "real"]), 2)
        for case in CASES:
            with self.subTest(case["id"]):
                kind, basis = rules.classify(case["model_type"], case["title"])
                treatment = rules.purchase_treatment(kind, vat=case["vat"], buyer_tax_id=case["buyer_tax_id"],
                                                     buyer_name=case["buyer_name"])
                expect = case["expect"]
                self.assertEqual((kind, basis), (expect["document_type"], expect["basis"]))
                self.assertEqual((treatment.claims_input_vat, treatment.route, treatment.paid_hint,
                                  list(treatment.review)),
                                 (expect["claims_input_vat"], expect["route"], expect["paid_hint"], expect["review"]))

    def test_samples_cover_every_document_type(self):
        covered = {case["expect"]["document_type"] for case in CASES}
        self.assertEqual(covered, set(codes.DOCUMENT_TYPES))


class LegacyContract(unittest.TestCase):
    """旧线上 `pearnly-app@ec2e5c95` `services/purchase/intake.py:38-82` 的判据（Cowork 推送原样沿用）。"""

    def legacy_route(self, document_type, vat, buyer_tax, buyer_name):
        dtype = (document_type or "").lower()
        if dtype in ("payment_evidence", "order_evidence"):
            return "expense"
        if dtype == "tax_invoice" and vat > 0 and rules.has_buyer_identity(buyer_tax, buyer_name):
            return "purchase"
        return "expense"

    def test_route_matches_the_old_live_rule_for_every_combination(self):
        for kind in (*codes.DOCUMENT_TYPES, "", None, "TAX_INVOICE", "bogus"):
            for vat in (0, 7, -7):
                for buyer_tax, buyer_name in (("", ""), ("0105546015062", ""), ("123", "เงินสด"),
                                              ("", "Walk-in 3"), ("", "AB"), ("", "ACME Co.")):
                    with self.subTest(kind=kind, vat=vat, buyer=(buyer_tax, buyer_name)):
                        treatment = rules.purchase_treatment(kind, vat=vat, buyer_tax_id=buyer_tax,
                                                             buyer_name=buyer_name)
                        self.assertEqual(treatment.route, self.legacy_route(kind, vat, buyer_tax, buyer_name))

    def test_buyer_identity_and_paid_hint(self):
        self.assertTrue(rules.has_buyer_identity("0105546015062"))
        self.assertTrue(rules.has_buyer_identity(None, "บริษัท ชินเชียร์ไอซ์ จำกัด"))
        for name in ("", "cash", "CUSTOMER 12", "walk-in", "ลูกค้า", "เงินสด", "Take away", "AB"):
            self.assertFalse(rules.has_buyer_identity("12", name), name)
        self.assertEqual([rules.paid_hint(kind) for kind in codes.DOCUMENT_TYPES],
                         [False, True, True, False, None, None, None])
        self.assertIsNone(rules.paid_hint(""))


class NoDrift(unittest.TestCase):
    """各产品只引用共用码表：提示词里列的票种、Cowork 的结构化枚举都与码表一致。"""

    def prompt_types(self, path, name):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        text = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                    and any(getattr(target, "id", None) == name for target in node.targets))
        line = next(row for row in text.splitlines() if '"document_type":' in row)
        return [part.strip().strip('",') for part in line.split(":", 1)[1].split("|")]

    def test_cowork_prompts_list_every_document_type(self):
        for name in ("L2_SYSTEM", "L3_SYSTEM"):
            with self.subTest(name):
                self.assertEqual(self.prompt_types("apps/pearnly_cowork/pearnly_cowork/ocr/engine/prompts.py", name),
                                 list(codes.DOCUMENT_TYPES))

    def test_products_do_not_keep_their_own_type_lists(self):
        schema = (ROOT / "apps/pearnly_cowork/pearnly_cowork/ocr/schema/invoice.py").read_text(encoding="utf-8")
        self.assertIn("Literal[tax_codes.DOCUMENT_TYPES]", schema)
        erp = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/purchasing/recognition.py").read_text(encoding="utf-8")
        self.assertIn("tax_rules.purchase_treatment(", erp)
        self.assertIn("tax_rules.classify(", erp)
        express = (ROOT / "apps/pearnly_cowork/pearnly_cowork/push/express/field_rules.py").read_text(encoding="utf-8")
        self.assertIn("tax_rules.purchase_treatment(", express)
        self.assertNotIn("simplified_tax_invoice", express)

    def test_the_contract_is_pure(self):
        allowed = {"re", "dataclasses", "decimal", "typing", "pearnly_core"}
        for path in (ROOT / "apps/pearnly_core/pearnly_core/tax_documents").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    self.assertIn(name.split(".")[0], allowed, f"{path.name}: {name}")
                    if name.startswith("pearnly_core"):
                        self.assertTrue(name.startswith("pearnly_core.tax_documents"), f"{path.name}: {name}")


class IncludedVat(unittest.TestCase):
    """简式税票票面价依法含 VAT（ม.86/6）：税额读成 0 / 没写时按 7/107 拆出；其余不动。"""

    def split(self, document_type="simplified_tax_invoice", total="727.00", **values):
        return rules.included_vat(document_type, total, **values)

    def test_every_sample(self):
        self.assertGreaterEqual(len([case for case in FIXTURE["vat_included"] if case["source"] == "real"]), 1)
        for case in FIXTURE["vat_included"]:
            with self.subTest(case["id"]):
                result = rules.included_vat(case["document_type"], case["total"], subtotal=case["subtotal"],
                                            vat=case["vat"], texts=case["texts"])
                expect = case["expect"]
                self.assertEqual(result.reason, expect["reason"])
                self.assertEqual(result.applies, expect["reason"] == rules.SPLIT)
                if result.applies:
                    self.assertEqual((str(result.subtotal), str(result.vat)), (expect["subtotal"], expect["vat"]))

    def test_the_reported_abb_is_split_into_base_and_vat(self):
        # 2026-10-13 testcowork：D2 手写 ABB 727.00 读成税前 727.00、税额 "0"
        result = self.split(subtotal="727.00", vat="0")
        self.assertEqual((result.reason, result.subtotal, result.vat), (rules.SPLIT, Decimal("679.44"), Decimal("47.56")))
        self.assertEqual(result.subtotal + result.vat, Decimal("727.00"))

    def test_zero_and_missing_vat_are_written_in_several_ways(self):
        for vat in ("0", "0.00", " 0 ", 0, "", None, "  "):
            for subtotal in ("727.00", "727", "727.01", "", None):
                with self.subTest(vat=vat, subtotal=subtotal):
                    result = self.split(subtotal=subtotal, vat=vat)
                    self.assertEqual((result.subtotal, result.vat), (Decimal("679.44"), Decimal("47.56")))

    def test_rounding_is_half_up_to_the_cent_and_always_adds_back_to_the_total(self):
        for total, vat in (("597.00", "39.06"), ("100", "6.54"), ("107", "7.00"), ("1,070.00", "70.00"),
                           ("0.15", "0.01"), ("12345.67", "807.66")):
            with self.subTest(total=total):
                result = self.split(total=total, subtotal="", vat="0")
                self.assertEqual(result.vat, Decimal(vat))
                self.assertEqual(result.subtotal + result.vat, Decimal(total.replace(",", "")).quantize(Decimal("0.01")))

    def test_anything_already_split_or_not_an_abbreviated_invoice_is_left_alone(self):
        self.assertEqual(self.split(subtotal="557.94", vat="39.06", total="597.00").reason, rules.VAT_PRESENT)
        self.assertEqual(self.split(subtotal="727", vat="47.56").reason, rules.VAT_PRESENT)
        for kind in ("tax_invoice", "receipt", "credit_note", "payment_evidence", "order_evidence", "other", "", None,
                     "bogus"):
            with self.subTest(kind=kind):
                self.assertEqual(self.split(kind, subtotal="727", vat="0").reason, rules.NOT_INCLUDED_TYPE)
        self.assertTrue(self.split("SIMPLIFIED_TAX_INVOICE", subtotal="727", vat="0").applies)

    def test_foreign_currency_odd_amounts_and_withholding_are_left_alone(self):
        self.assertEqual(self.split(subtotal="727", vat="0", currency="USD").reason, rules.FOREIGN_CURRENCY)
        self.assertEqual(self.split(subtotal="727", vat="0", currency="$").reason, rules.FOREIGN_CURRENCY)
        for baht in ("", None, "THB", "thb", "บาท", "฿", "Baht"):
            self.assertTrue(self.split(subtotal="727", vat="0", currency=baht).applies, baht)
        for total in (None, "", "0", "-727", "abc", "NaN"):
            self.assertEqual(self.split(total=total, vat="0").reason, rules.TOTAL_MISSING, total)
        self.assertEqual(self.split(subtotal="700", vat="0").reason, rules.SUBTOTAL_DIFFERS)
        self.assertEqual(self.split(subtotal="n/a", vat="0").reason, rules.SUBTOTAL_DIFFERS)
        self.assertEqual(self.split(subtotal="727", vat="-").reason, rules.VAT_UNREADABLE)
        self.assertEqual(self.split(subtotal="727", vat="exempt").reason, rules.VAT_UNREADABLE)
        self.assertEqual(self.split(subtotal="727", vat="0", wht_amount="21.81").reason, rules.WITHHOLDING)
        self.assertTrue(self.split(subtotal="727", vat="0", wht_amount="0").applies)

    def test_a_document_that_says_it_is_exempt_or_zero_rated_is_left_alone(self):
        for text in ("ผักสด (ยกเว้นภาษี)", "สินค้ายกเว้น VAT", "VAT EXEMPT", "Exempted goods", "NON-VAT 120.00",
                     "NON VATABLE", "zero-rated", "Zero rate", "VAT 0%", "0% VAT", "VAT 0.00", "ภาษีมูลค่าเพิ่ม 0.00",
                     "อัตราภาษีร้อยละ 0", "อัตราศูนย์", "ไม่มีภาษี", "ไม่คิด VAT", "ไม่รวมVAT", "no VAT", "VAT-free"):
            with self.subTest(text=text):
                self.assertTrue(rules.no_vat_marked([text]))
                self.assertEqual(self.split(subtotal="727", vat="0", texts=["น้ำแข็ง", text]).reason, rules.NO_VAT_MARKED)

    def test_ordinary_document_text_does_not_count_as_exempt(self):
        for text in ("ABB / VAT INCLUDED", "รวมเงิน (รวมภาษีมูลค่าเพิ่มแล้ว) 727.00", "ราคารวม VAT 7% แล้ว", "VAT 7%",
                     "ภาษีมูลค่าเพิ่ม 47.56", "อัตราภาษีร้อยละ 7", "10% VAT", "ส่วนลด 0%", "Coke Zero", "นม 0% fat",
                     "VAT 0.07", "VATABLE 727.00", "ใบกำกับภาษีอย่างย่อ", "ไม่สามารถใช้เป็นใบกำกับภาษีเต็มรูป", "", None):
            with self.subTest(text=text):
                self.assertFalse(rules.no_vat_marked([text]))
        self.assertTrue(self.split(subtotal="727", vat="0", texts=["ABB / VAT INCLUDED", "ข้าวผัด"]).applies)


class RepairedTaxId(unittest.TestCase):
    """12 位税号补回开头的 0：只有补完通过校验位才补（erpcowork 2026-09-30 的真实读错）。"""

    def test_dropped_leading_zero_is_restored_when_the_check_digit_agrees(self):
        from pearnly_core.tax_documents.amounts import repaired_tax_id
        for read, printed in (("105546015062", "0105546015062"), ("105541034263", "0105541034263"),
                              ("105568182913", "0105568182913"), ("105557139761", "0105557139761")):
            self.assertEqual(repaired_tax_id(read), printed)

    def test_anything_else_is_left_as_read(self):
        from pearnly_core.tax_documents.amounts import repaired_tax_id
        for value in ("0105546015062", "123456789012", "10554601506", "", None, "1055-4601-5062"):
            self.assertEqual(repaired_tax_id(value), value)
        # A number cannot keep a leading zero at all; it comes back as the printed 13-digit text.
        self.assertEqual(repaired_tax_id(105546015062), "0105546015062")


if __name__ == "__main__":
    unittest.main()
