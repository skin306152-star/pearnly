"""共用层 MR.ERP 库存入库 / 出库导入（`pearnly_core.mrerp.push.xlsx.stock`、`menus` 的库存几类，11-19）的离线检查。

只用标准库：不连库、不连 MR.ERP，CI 直接跑。导入表照 MR.ERP 源码（`impstktranrec` / `impstktraniss` 的 importpc / formrdpc）：
Sheet1 6 列、Sheet2 入库 8 列 / 出库 6 列、只有两张表；数量必须是正数（MR.ERP 会吃掉负号）、入库要单价、仓库码 4 位、分店 5 位。
"""
from __future__ import annotations

import io
import pathlib
import re
import sys
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "apps/pearnly_core") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.mrerp import menus  # noqa: E402
from pearnly_core.mrerp.push import modules  # noqa: E402
from pearnly_core.mrerp.push.xlsx import stock as sheet  # noqa: E402


def entry(kind=sheet.IN, **extra):
    line = {"code": "P0001", "store": "0001", "qty": "3", "dept": "0000", "job": "0000"}
    if kind == sheet.IN:
        line["price"] = "12.50"
    base = {"kind": kind, "number": "IN261119-0001", "date": "2026-11-19", "branch": "00000",
            "notes": ["", "", ""], "lines": [line]}
    base.update(extra)
    return base


def cells(blob, number):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        strings = [re.sub(r"<[^>]+>", "", s) for s in re.findall(r"<si>(.*?)</si>", zf.read("xl/sharedStrings.xml").decode())]
        xml = zf.read(f"xl/worksheets/sheet{number}.xml").decode()
    out = {}
    for ref, attrs, value in re.findall(r'<c r="([A-Z]+\d+)"([^>]*)><v>([^<]*)</v></c>', xml):
        out[ref] = strings[int(value)] if 't="s"' in attrs else value
    return out


class StockSheet(unittest.TestCase):
    def test_receipt_has_six_header_columns_and_eight_line_columns_with_price_and_amount(self):
        blob = sheet.generate(entry())
        head, lines = cells(blob, 1), cells(blob, 2)
        self.assertEqual([head[f"{c}1"] for c in "ABCDEF"], sheet.HEADERS_1)
        self.assertEqual((head["A2"], head["B2"], head["C2"], head["F2"]), ("IN261119-0001", "2026-11-19", "00000", "-"))
        self.assertEqual([lines[f"{c}1"] for c in "ABCDEFGH"], sheet.HEADERS_2[sheet.IN])
        self.assertEqual([lines[f"{c}2"] for c in "ABCDEFGH"],
                         ["IN261119-0001", "P0001", "0000", "0000", "0001", "3", "12.5", "37.5"])

    def test_issue_has_no_price_columns(self):
        lines = cells(sheet.generate(entry(sheet.OUT)), 2)
        self.assertEqual([lines[f"{c}1"] for c in "ABCDEF"], sheet.HEADERS_2[sheet.OUT])
        self.assertNotIn("G1", lines)
        self.assertNotIn("G2", lines)

    def test_only_two_sheets_and_no_dangling_parts(self):
        with zipfile.ZipFile(io.BytesIO(sheet.generate(entry()))) as zf:
            names = set(zf.namelist())
            workbook = zf.read("xl/workbook.xml").decode()
            rels = zf.read("xl/_rels/workbook.xml.rels").decode()
            types = zf.read("[Content_Types].xml").decode()
        self.assertNotIn("xl/worksheets/sheet3.xml", names)
        self.assertNotIn("xl/calcChain.xml", names)
        self.assertEqual(len(re.findall(r"<sheet ", workbook)), 2)
        for text in (rels, types):
            self.assertNotIn("sheet3.xml", text)
            self.assertNotIn("calcChain", text)
        self.assertNotIn('Target="/', rels)  # 相对路径（MR.ERP 的旧 PHPExcel 读不出绝对路径）

    def test_rules_before_upload(self):
        self.assertEqual(sheet.check(entry()), "")
        self.assertEqual(sheet.check(entry(sheet.OUT)), "")
        self.assertEqual(sheet.check(entry(number="X" * 19)), sheet.NUMBER_INVALID)
        self.assertEqual(sheet.check(entry(lines=[])), sheet.LINES_EMPTY)
        self.assertEqual(sheet.check(entry(branch="0000")), sheet.FIELD_MISSING)
        self.assertEqual(sheet.check(entry(date="19/11/2569")), sheet.FIELD_MISSING)
        base = entry()["lines"][0]
        for bad, code in (({"store": "01"}, sheet.FIELD_MISSING), ({"code": "P"}, sheet.FIELD_MISSING),
                          ({"job": ""}, sheet.FIELD_MISSING), ({"qty": "-2"}, sheet.QTY_INVALID),
                          ({"qty": "0"}, sheet.QTY_INVALID), ({"qty": "x"}, sheet.QTY_INVALID),
                          ({"price": None}, sheet.PRICE_MISSING), ({"price": "-1"}, sheet.QTY_INVALID)):
            with self.subTest(bad=bad):
                self.assertEqual(sheet.check(entry(lines=[{**base, **bad}])), code)
        self.assertEqual(sheet.check(entry(lines=[{**base, "price": "0"}])), "")  # 0 单价照原生可以

    def test_amount_rounds_to_cents(self):
        self.assertEqual(str(sheet.amount("3", "0.335")), "1.01")


class StockMenus(unittest.TestCase):
    def test_stock_pages_and_kinds(self):
        self.assertEqual(menus.page_of(menus.STOCK_IN), "impstktranrec")
        self.assertEqual(menus.page_of(menus.stock_out_kind("damage")), "impstktraniss")
        self.assertEqual(menus.stock_out_kind(""), "stock_out.other")
        self.assertIsNone(menus.page_of("stock_out_x"))
        self.assertEqual((menus.ALL_PAGES["impstktranrec"], menus.ALL_PAGES["impstktraniss"]), (356, 357))
        self.assertEqual((modules.get_module("stock_in").idmenu, modules.get_module("stock_out").idmenu), (356, 357))

    def test_an_issue_menu_is_remembered_per_purpose(self):
        observed = menus.observe({}, "6:1", {"impstktraniss": [{"idmenu": "33", "label": "จ่าย", "linked": True},
                                                               {"idmenu": "34", "label": "จ่าย-2", "linked": True}]},
                                 at="2026-11-19")
        chosen = menus.choose({}, "6:1", menus.stock_out_kind("damage"), "34", observed, actor="a", at="2026-11-19")
        self.assertEqual(menus.chosen_map(chosen, "6:1"), {"stock_out.damage": "34"})
        view = menus.view(chosen, observed, "6:1", [menus.stock_out_kind("damage"), menus.stock_out_kind("other")])
        self.assertEqual(view["kinds"]["stock_out.damage"]["chosen"], "34")
        self.assertTrue(view["kinds"]["stock_out.other"]["needed"])
        self.assertNotIn("stock_out.other", menus.chosen_map(chosen, "6:1"))


if __name__ == "__main__":
    unittest.main()
