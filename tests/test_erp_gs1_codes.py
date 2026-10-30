"""GS1 码按商品条码认、按序列号认台（主会话 10-30）：认码 `products.codes.candidates`（扫码 / 盘点 / 收银 / 入库共用）
与按台认号 `inventory.serials.lookup`。规则本身见 `tests/test_products_gs1.py`。

Run with Frappe installed. No Site, Redis or network: database reads are replaced.
"""
import unittest
from unittest import mock

GTIN14, EAN13 = "08850001000019", "8850001000019"
SCANNED = {
    "Data Matrix (WASM)": "01088500010000191727123110LOT7\x1d21SN12345",
    "Data Matrix (native, leading FNC1)": "\x1d01088500010000191727123110LOT7\x1d21SN12345",
    "GS1-128 (native, symbology id)": "]C1010885000100001910LOT7",
    "GS1-128 (WASM)": "010885000100001910LOT7",
    "DataBar": "0108850001000019",
    "DataBar Expanded": "01088500010000193103000150",
    "typed with brackets": "(01)08850001000019(21)SN12345",
}


class Candidates(unittest.TestCase):
    """认码（`products.codes.candidates`，扫码 / 盘点 / 收银 / 入库共用）：GS1 解出的 GTIN 按条码与售卖单位条码认。"""

    PRODUCTS = [{"name": "PRD-EAN", "product_name": "EAN", "code": "P1", "barcode": EAN13, "express_company": ""},
                {"name": "PRD-UPC", "product_name": "UPC", "code": "P2", "barcode": "01234565", "express_company": ""},
                {"name": "PRD-QR", "product_name": "QR", "code": "P3", "qr_payload": "PEARNLY-QR-0001", "express_company": ""},
                {"name": "PRD-PACK", "product_name": "Pack", "code": "P4", "express_company": ""}]
    UNITS = [{"parent": "PRD-PACK", "barcode": "18850001000016"}]

    def candidates(self, value):
        from pearnly_erp_portal.products import codes

        def matches(row_value, wanted):
            return row_value in wanted[1] if isinstance(wanted, list) and wanted[:1] == ["in"] else row_value == wanted

        def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
            rows = self.UNITS if doctype == codes.UNIT else self.PRODUCTS
            found = [row for row in rows if all(key in ("is_active", "parenttype") or matches(row.get(key), wanted)
                                                for key, wanted in filters.items())]
            return [row[pluck] for row in found] if pluck else found

        with mock.patch.object(codes.frappe, "get_all", side_effect=get_all):
            return [row["name"] for row in codes.candidates(value)]

    def test_gs1_codes_find_the_product_by_its_barcode(self):
        for label, raw in SCANNED.items():
            with self.subTest(label):
                self.assertEqual(self.candidates(raw), ["PRD-EAN"])

    def test_upc_read_long_finds_the_short_barcode(self):
        self.assertEqual(self.candidates("0012345000065"), ["PRD-UPC"])

    def test_a_gtin_14_of_a_pack_finds_its_sale_unit(self):
        self.assertEqual(self.candidates("0118850001000016"), ["PRD-PACK"])
        self.assertEqual(self.candidates("18850001000016"), ["PRD-PACK"])

    def test_plain_content_still_matches_exactly(self):
        self.assertEqual(self.candidates("PEARNLY-QR-0001"), ["PRD-QR"])
        self.assertEqual(self.candidates("NOTHING-HERE"), [])


class Serials(unittest.TestCase):
    def test_a_gs1_code_finds_the_unit_by_its_serial(self):
        from pearnly_erp_portal.inventory import serials

        seen = []

        def get_all(doctype, filters=None, **kwargs):
            seen.append(dict(filters))
            return [{"name": "SER-1"}] if filters.get("secondary_no") == "SN12345" else []

        with mock.patch.object(serials.frappe, "get_all", side_effect=get_all), \
                mock.patch.object(serials, "key", lambda company, text: f"{company}|{text}"):
            self.assertEqual(serials.lookup(SCANNED["Data Matrix (WASM)"], "CO-1"), [{"name": "SER-1"}])
        self.assertEqual(seen[0]["serial_key"], "CO-1|SN12345")


if __name__ == "__main__":
    unittest.main()
