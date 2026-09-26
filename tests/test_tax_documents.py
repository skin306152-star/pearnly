"""ADR-0012 共用票种决策表：回归样本、旧线上口径、各产品不漂移。纯离线，不需要 Frappe。"""
import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.tax_documents import codes, rules  # noqa: E402

CASES = json.loads((ROOT / "tests/fixtures/tax-documents/cases.json").read_text(encoding="utf-8"))["cases"]


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


if __name__ == "__main__":
    unittest.main()
