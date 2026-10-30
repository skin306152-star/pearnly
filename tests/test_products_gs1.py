"""GS1 码与 GTIN 的几种写法对上商品条码（主会话 10-30：码制一次覆盖全，按类解析）。

用例里的原文是读码器实测给的样子（三星 A14 原生、WASM 3.1.3，`docs/work/2026-10-30-scan-symbologies.md`）。
纯规则，不需要 Frappe；认码与认台（要 Frappe）在 `tests/test_erp_gs1_codes.py`（候选镜像检查里跑）。
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gs1", ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/products/gs1.py")
gs1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gs1)

GTIN14, EAN13 = "08850001000019", "8850001000019"
# 同一件商品的 GS1 码，各读码器给的原文
SCANNED = {
    "Data Matrix (WASM)": "01088500010000191727123110LOT7\x1d21SN12345",
    "Data Matrix (native, leading FNC1)": "\x1d01088500010000191727123110LOT7\x1d21SN12345",
    "GS1-128 (native, symbology id)": "]C1010885000100001910LOT7",
    "GS1-128 (WASM)": "010885000100001910LOT7",
    "DataBar": "0108850001000019",
    "DataBar Expanded": "01088500010000193103000150",
    "typed with brackets": "(01)08850001000019(21)SN12345",
}


class Parse(unittest.TestCase):
    def test_every_scanned_form_gives_the_gtin(self):
        for label, raw in SCANNED.items():
            with self.subTest(label):
                fields = gs1.parse(raw)
                self.assertEqual(fields["01"], GTIN14)
                self.assertEqual(gs1.lookup(raw)["gtin"][:2], [GTIN14, EAN13])

    def test_the_serial_comes_out_and_batch_and_expiry_stay_parsed_only(self):
        fields = gs1.parse(SCANNED["Data Matrix (WASM)"])
        self.assertEqual((fields["17"], fields["10"], fields["21"]), ("271231", "LOT7", "SN12345"))
        self.assertEqual(gs1.lookup(SCANNED["Data Matrix (native, leading FNC1)"])["serial"], "SN12345")
        self.assertEqual(gs1.lookup(SCANNED["DataBar"])["serial"], "")

    def test_plain_codes_are_not_taken_for_gs1(self):
        for raw in ("PRL-128-0001", "2856", "PEARNLY-QR-0001", "https://example.com/01088500010000191", EAN13,
                    "0108850001000018", "01ABC"):
            with self.subTest(raw):
                self.assertIsNone(gs1.parse(raw))
        self.assertEqual(gs1.lookup("PRL-128-0001"), {"exact": ["PRL-128-0001"], "gtin": [], "serial": ""})

    def test_gtin_lengths_and_upc_forms_meet(self):
        # WASM 把 UPC-A 读成带 0 的 13 位、UPC-E 读成展开的 13 位；商品上存的可能是短写法，反过来也一样
        self.assertIn("036000291452", gs1.lookup("0036000291452")["gtin"])
        self.assertIn("0036000291452", gs1.lookup("036000291452")["gtin"])
        self.assertIn("01234565", gs1.lookup("0012345000065")["gtin"])
        self.assertIn("012345000065", gs1.lookup("01234565")["gtin"])
        self.assertEqual(gs1.lookup(EAN13)["gtin"], [GTIN14])
        self.assertEqual(gs1.gtin_forms("8850001000018"), [], "a wrong check digit is not a GTIN")

    def test_symbology_ids_and_fnc1_are_cleaned(self):
        self.assertEqual(gs1.clean("]C1ABC"), "ABC")
        self.assertEqual(gs1.clean("]d201088500010000191"), "01088500010000191")
        self.assertEqual(gs1.clean("\x1d0108850001000019"), "0108850001000019")
        self.assertEqual(gs1.clean("A\x1dB"), "A\x1dB", "a separator inside the text stays")


if __name__ == "__main__":
    unittest.main()
