"""Offline checks for the Thai Revenue Department (RD) lookup parser.

No network, no Frappe: the fixtures are **real responses** captured from
`rdws.rd.go.th` on 2026-09-23 (tax id 0105546015062), so the parser is asserted
against the shape the service actually sends — not the shape we hoped for.

Why this matters: the old project's table only knew `vProvinceName` / `vThumbolName`
tags while the live response uses `vProvince` / `vThambol` / `vAmphur`. Parsing only
the documented shape silently drops the province and district from the address.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))

from pearnly_erp_portal.masters import rd_contracts as rd  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TAX_ID = "0105546015062"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class NormalizeTin(unittest.TestCase):
    def test_keeps_thirteen_digits_and_strips_separators(self):
        self.assertEqual(rd.normalize_tin("0-1055-46015-06-2"), TAX_ID)
        self.assertEqual(rd.normalize_tin(TAX_ID), TAX_ID)

    def test_rejects_wrong_length(self):
        for value in ("", None, "123", "010554601506299", "abcdefghijklm"):
            self.assertEqual(rd.normalize_tin(value), "")


class VatResponse(unittest.TestCase):
    def setUp(self):
        self.fields = rd.raw_fields(fixture("rd_vat_0105546015062.xml"))

    def test_flattens_v_prefixed_fields(self):
        self.assertEqual(self.fields.get("NID"), TAX_ID)
        self.assertEqual(self.fields.get("BranchNumber"), "0")
        self.assertEqual(self.fields.get("HouseNumber"), "14")
        # `anyType` 那种包装不该出现在字段表里。
        self.assertNotIn("anyType", self.fields)

    def test_address_uses_live_tag_names(self):
        """线上返回的是 vProvince / vThambol / vAmphur，不是 *Name —— 两处都不能缺。"""
        address = rd.full_address(self.fields)
        for part in ("14", "รามคำแหง 189", "มีนบุรี", "กรุงเทพมหานคร", "10510"):
            self.assertIn(part, address)

    def test_payload_matches_the_form_fields(self):
        data = rd.vat_payload(self.fields, TAX_ID, "Head Office", "Branch {0}")
        self.assertEqual(data["tax_id"], TAX_ID)
        self.assertEqual(data["name"], "บริษัท ซินเซียร์ไอซ์ จำกัด")
        self.assertEqual(data["branch_no"], 0)
        self.assertEqual(data["branch_label"], "Head Office")
        self.assertEqual(data["province"], "กรุงเทพมหานคร")
        self.assertEqual(data["post_code"], "10510")
        self.assertEqual(data["vat_register_date"], "2546-02-14")
        self.assertTrue(data["address"])

    def test_branch_label_follows_the_branch_number(self):
        branched = dict(self.fields, BranchNumber="00042", NID=TAX_ID)
        data = rd.vat_payload(branched, TAX_ID, "Head Office", "Branch {0}")
        self.assertEqual(data["branch_no"], 42)
        self.assertEqual(data["branch_label"], "Branch 00042")


class TinResponse(unittest.TestCase):
    def test_existing_tax_id_is_valid(self):
        fields = rd.raw_fields(fixture("rd_tin_0105546015062.xml"))
        data = rd.tin_payload(fields, TAX_ID)
        self.assertTrue(data["exists"])
        self.assertTrue(data["valid"])
        self.assertEqual(data["nid"], TAX_ID)

    def test_boolean_words_are_understood(self):
        self.assertTrue(rd.truthy({"IsExist": "Yes"}, "IsExist"))
        self.assertTrue(rd.truthy({"IsExist": "TRUE"}, "IsExist"))
        self.assertFalse(rd.truthy({"IsExist": "No"}, "IsExist"))


class ClientsContract(unittest.TestCase):
    def test_garbage_response_yields_no_fields(self):
        self.assertEqual(rd.raw_fields("<html>503</html>"), {})
        self.assertEqual(rd.raw_fields(""), {})

    def test_blank_marker_dash_is_treated_as_missing(self):
        fields = {"HouseNumber": "-", "Province": "กรุงเทพมหานคร"}
        self.assertEqual(rd.pick(fields, "HouseNumber"), "")
        self.assertEqual(rd.full_address(fields), "กรุงเทพมหานคร")

    def test_request_templates_carry_anonymous_credentials(self):
        vat = rd.VAT_SOAP_TPL.format(tax_id=TAX_ID, branch_no=3)
        self.assertIn("anonymous", vat)
        self.assertIn(f"<vat:TIN>{TAX_ID}</vat:TIN>", vat)
        self.assertIn("<vat:BranchNumber>3</vat:BranchNumber>", vat)
        tin = rd.TIN_SOAP_TPL.format(tax_id=TAX_ID)
        self.assertIn(f"<chec:TIN>{TAX_ID}</chec:TIN>", tin)


if __name__ == "__main__":
    unittest.main()
