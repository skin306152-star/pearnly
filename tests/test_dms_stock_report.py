"""DMS LINE「报表查询 → 1 库存停放报表」（评估 docs/work/2026-11-26-dms-line-stock-report-assessment.md）。

在生产镜像里跑（要 frappe、openpyxl、Playwright Chromium）：不连站点、不登 MR.ERP、不发 LINE。
MR.ERP 的导出用 openpyxl 按 11-26 测试库实查的样子造（标题、日期、9 列表头、一台车一行、末行「รวม n รายการ」）。
"""
import csv
import io
import os
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_dms"):
    sys.path.insert(0, str(ROOT / "apps" / app))
for name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_core", "pearnly_dms")]:
    del sys.modules[name]

import frappe  # noqa: E402
from openpyxl import Workbook  # noqa: E402

from pearnly_dms import i18n  # noqa: E402
from pearnly_dms.query import query_cards, query_flow, stock_files, stock_flow, stock_render  # noqa: E402
from pearnly_dms.query import stock_report as report  # noqa: E402

assert Path(report.__file__).is_relative_to(ROOT), report.__file__
HEADER = ("รุ่นรถ", "สีรถ", "หมายเลขเครื่อง", "หมายเลขตัวถัง", "สถานะรถ", "สถานที่จอดรถ (คลัง)", "เลขที่ใบจอง",
          "ชื่อที่ปรึกษาการขาย", "ทีมสาขา")
# 测试库 2026-11-26 实读的 9 台（导出原样）
TEST_LIBRARY = [
    ("DM-2D19", "MGY", "1122334455", "11223344556677", "รถแต่งเสร็จส่งคืน", "BANGKOK", "BK000002610000015", "dmstest", "BKK-A"),
    ("DM-2D19", "ONX", "DMX19-000102", "DMCGO19-0000102", "รถว่าง", "BANGKOK", None, None, "BKK-A"),
    ("DM-2D19", "PWH", "DMX19-000106", "DMCGO19-0000106", "รถว่าง", "คลังที่ 1", None, None, "BKK-A"),
    ("DM-4D19", "MGY", "DMX19-000104", "DMCAB19-0000104", "รถว่าง-โชว์", "คลังที่ 1", None, None, "BKK-A"),
    ("DM-4D19", "PWH", "DMX19-000103", "DMCAB19-0000103", "รถว่าง", "BANGKOK", None, None, "BKK-A"),
    ("DM-4D30", "MGY", "DMX30-000107", "DMCAB30-0000107", "รถทีมสาขาจอง", "BANGKOK", None, None, "BKK-A"),
    ("Test", "RED", "1111222", "222333", "รถว่าง-โชว์", "BANGKOK", "BK000002602000002", "dmstest", "BKK-A"),
    ("Test", "RED", "778899", "777888999", "รถทีมสาขาจอง", "BANGKOK", "BK000002511000001", "salemgr", "BKK-A"),
    ("TS-01", "RED", "FLL351", "MP1TFR41JTG011978", "รถแต่งเสร็จ", "คลังที่ 1", None, None, "BKK-A"),
]
CARS = {"DM-2D19": {"name": "D-MAX SPARK", "model_name": "DM 2D", "year": "2026"},
        "DM-4D19": {"name": "", "model_name": "DM 4D", "year": "2025"},
        "Test": {"name": "", "model_name": "", "year": ""}}


def _dealer_library():
    """真实经销商库存的形状（11-26 上线当晚线上那份原件：车型 / 颜色 / 状态 / 地点的分布照原样，机号车架号为造的）：
    92 台、49 个车型颜色组合、状态几乎全是「รถลงแล้ว」、团队列全空、只有一台被订走。"""
    hq, pd, show_ry, show_pd = "ระยอง-สำนักงานใหญ่", "ปลวกแดง", "โชว์บนโชว์รูม(RY)", "โชว์บนโชว์รูม(PD)"
    groups = (
        ("UNL10", "SBW", ((show_ry, 1), (hq, 1))), ("UNN10", "SBW", ((hq, 1),)), ("UNT20", "ELG", ((hq, 1),)),
        ("UPL10", "SBW", ((pd, 1),)), ("UPL20", "ELG", ((pd, 2),)), ("UQK20", "ELG", ((hq, 1),)),
        ("URA20", "BOS", ((hq, 1),)), ("URB10", "SBW", ((hq, 1), (pd, 1))), ("USF20", "BAB", ((hq, 1),)),
        ("USK20", "ELG", ((hq, 1),)), ("USM30", "DWP", ((hq, 1),)), ("USR30", "DWP", ((pd, 1), (hq, 1))),
        ("UUD20", "BAB", ((show_pd, 1),)), ("UUH20", "BAB", ((show_ry, 1),)), ("UUK60", "DWB", ((hq, 1),)),
        ("VNK10", "SBW", ((hq, 1),)), ("VNK20", "ELG", ((pd, 1),)), ("VPA10", "SBW", ((pd, 1),)),
        ("VPA20", "ELG", ((pd, 1),)), ("VPE20", "ELG", ((hq, 2),)), ("VPG10", "SBW", ((pd, 1),)),
        ("VPG20", "ELG", ((pd, 1), (hq, 1))), ("VQA20", "ELG", ((pd, 1), (hq, 1))),
        ("VQC20", "BOS", ((hq, 1), (pd, 1))), ("VRA10", "SBW", ((pd, 2),)), ("VRC20", "BOS", ((hq, 1), (pd, 1))),
        ("VRC20", "ELG", ((pd, 3),)), ("VRD20", "BOS", ((hq, 1),)), ("VRF20", "BOS", ((pd, 1),)),
        ("VRG20", "BOS", ((hq, 1),)), ("VSA20", "BOS", ((pd, 2), (hq, 1))), ("VSA20", "ELG", ((pd, 2), (hq, 2))),
        ("VSB20", "BOS", ((show_ry, 1), (hq, 4), (pd, 1))), ("VSB20", "ELG", ((hq, 3), (pd, 2))),
        ("VSB30", "DWP", ((hq, 4), (pd, 5))), ("VSC20", "BOS", ((pd, 1),)), ("VSC20", "ELG", ((pd, 1),)),
        ("VSC30", "DWP", ((hq, 2), (pd, 2))), ("VSE20", "BOS", ((hq, 2), (pd, 1))), ("VSE20", "ELG", ((hq, 1),)),
        ("VSF30", "DWP", ((pd, 1),)), ("VSG20", "ELG", ((hq, 1),)), ("VSG30", "DWP", ((pd, 1),)),
        ("VSH20", "BOS", ((hq, 1),)), ("VUB20", "HVG", ((hq, 1),)), ("VUB30", "DWP", ((pd, 1), (hq, 1))),
        ("VUC30", "DWP", ((show_ry, 1),)), ("VUD50", "HGB", ((pd, 1),)), ("VUD60", "DWB", ((pd, 2), (hq, 2))),
    )
    rows = []
    for model, color, places in groups:
        for place, count in places:
            for _ in range(count):
                number = len(rows) + 1
                rows.append([model, color, f"E{number:05d}", f"C{number:05d}", "รถลงแล้ว", place, None, None, None])
    rows[-1][4] = "รถว่าง"  # 原件里唯一一台叫「รถว่าง」的
    rows[-2][6:8] = ["BK000002611000001", "sale01"]  # 一台被订走
    return [tuple(row) for row in rows]


DEALER_LIBRARY = _dealer_library()


def export(rows, title="รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ-ทั้งหมด", total=None, company=None):
    book = Workbook()
    sheet = book.active
    if company:  # 线上的原件在标题上面还有一行公司名（测试库那行是空的）
        sheet.append([company])
    sheet.append([title])
    sheet.append(["วันที่ 26/11/2569"])
    sheet.append(list(HEADER))
    for row in rows:
        sheet.append(list(row))
    sheet.append([f"รวม  {len(rows) if total is None else total}  รายการ"])
    for line in (*range(1, 4 if company else 3), sheet.max_row):
        sheet.merge_cells(f"A{line}:I{line}")
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def big_library(count=600):
    """几百台车的桩回包（testdms 测试库只有 9 台）：100 个车型 × 3 种颜色、5 个停放地点。"""
    places = ("ปลวกแดง", "ระยอง", "ชลบุรี", "BANGKOK", "คลังที่ 1")
    statuses = ("รถว่าง", "รถว่าง", "รถว่าง-โชว์", "รถทีมสาขาจอง", "รถแต่งเสร็จ")
    return [(f"UNT{index % 100:02d}", ("ELG", "PWH", "MGY")[(index // 100) % 3], f"E{index}", f"C{index}",
             statuses[index % 5], places[index % 5], None, None, "TEAM") for index in range(count)]


def lang(code="th"):
    return patch.object(i18n, "line_lang", return_value=code)


class Parse(unittest.TestCase):
    def test_real_layout_reads_every_vehicle(self):
        rows = report.parse_export(export(TEST_LIBRARY))
        self.assertEqual(len(rows), 9)
        self.assertEqual(rows[1], {"model": "DM-2D19", "color": "ONX", "engine": "DMX19-000102",
                                   "chassis": "DMCGO19-0000102", "status": "รถว่าง", "location": "BANGKOK",
                                   "booking": "", "advisor": "", "team": "BKK-A"})

    def test_empty_export_is_zero_rows_not_an_error(self):
        self.assertEqual(report.parse_export(export([])), [])

    def test_total_mismatch_or_not_a_workbook_fails_instead_of_a_wrong_table(self):
        with self.assertRaises(report.ExportChanged):
            report.parse_export(export(TEST_LIBRARY, total=10))
        with self.assertRaises(report.ExportChanged):
            report.parse_export(b"<html>login</html>")

    def test_form_values_keep_the_ranges_mrerp_prefilled(self):
        page = ('<input type="text" name="txtbranch_bg" value="BANGKOK"><input type="hidden" name="branchval_bg" value="42">'
                '<input type="hidden" name="carval_bg" value="5"><input type="button" name="btnprint" value="ออกรายงาน">')
        self.assertEqual(report.form_values(page), {"txtbranch_bg": "BANGKOK", "branchval_bg": "42", "carval_bg": "5"})


class Summary(unittest.TestCase):
    def test_available_means_not_booked_whatever_the_status_is_called(self):
        def car(status, booking=""):
            return {"status": status, "booking": booking}

        for status in ("รถลงแล้ว", "รถว่าง", "รถว่าง-โชว์", "รถแต่งเสร็จ", "", "สถานะที่ยังไม่เคยเห็น"):
            self.assertTrue(report.is_available(car(status)), status)
        self.assertFalse(report.is_available(car("รถทีมสาขาจอง")))
        self.assertFalse(report.is_available(car("รถลงแล้ว", "BK000002610000015")))
        self.assertFalse(report.is_available(car("รถว่าง-โชว์", "BK000002602000002")))

    def test_dealer_stock_whose_status_is_received_is_all_available(self):
        """11-26 上线当晚的真实形状：公司名一行在最上、几乎全部「รถลงแล้ว」、没有团队、没有订车单号 → 空车档不能只剩 1 台。"""
        rows = report.parse_export(export(DEALER_LIBRARY, company="บริษัท ตัวอย่าง จำกัด"))
        self.assertEqual(len(rows), 92)
        available = report.summarize(rows, "available", {})
        everything = report.summarize(rows, "all", {})
        self.assertEqual((available["total"], everything["total"]), (91, 92))  # 只少被订走的那一台
        self.assertEqual(len(available["rows"]), len(everything["rows"]))
        first = available["rows"][0]
        self.assertEqual((first["code"], first["count"]), ("UNL10 SBW", 2))
        self.assertEqual(report.locations_text(first["locations"]), "ระยอง-สำนักงานใหญ่ 1 / โชว์บนโชว์รูม(RY) 1")

    def test_one_table_sorted_by_model_and_colour_without_year_sections(self):
        summary = report.summarize(report.parse_export(export(TEST_LIBRARY)), "available", CARS)
        self.assertEqual(summary["total"], 5)  # 9 台里 2 台团队预订、2 台有订车单号
        self.assertNotIn("sections", summary)
        self.assertEqual([(row["name"], row["code"], row["count"]) for row in summary["rows"]],
                         [("D-MAX SPARK", "DM-2D19 ONX", 1), ("D-MAX SPARK", "DM-2D19 PWH", 1),
                          ("DM 4D", "DM-4D19 MGY", 1), ("DM 4D", "DM-4D19 PWH", 1), ("TS-01", "TS-01 RED", 1)])
        # 车名回落：没有 ชื่อรถ 用 ชื่อรุ่นรถ，再没有用代码

    def test_year_only_when_every_model_has_the_same_year(self):
        rows = report.parse_export(export(TEST_LIBRARY))
        self.assertEqual(report.summarize(rows, "available", CARS)["year"], "")  # 2026 / 2025 / 没填
        same = {code: {"name": code, "year": "2026"} for code in ("DM-2D19", "DM-4D19", "DM-4D30", "Test", "TS-01")}
        self.assertEqual(report.summarize(rows, "all", same)["year"], "26")
        self.assertEqual(report.summarize(rows, "all", {**same, "Test": {"name": "Test", "year": ""}})["year"], "")

    def test_locations_are_counted_and_joined_like_the_sample(self):
        rows = [{"model": "UNT20", "color": "ELG", "status": "รถว่าง", "location": place}
                for place in ("ระยอง", "ปลวกแดง", "ปลวกแดง")]
        row = report.summarize(rows, "available", {})["rows"][0]
        self.assertEqual((row["code"], row["count"]), ("UNT20 ELG", 3))
        self.assertEqual(report.locations_text(row["locations"]), "ปลวกแดง 2 / ระยอง 1")

    def test_a_place_and_its_count_stay_together_in_the_picture(self):
        summary = report.summarize([{"model": "VSB20", "color": "BOS", "status": "รถลงแล้ว", "location": place}
                                    for place in ("ระยอง-สำนักงานใหญ่", "ปลวกแดง")], "available", {})
        with lang("th"):
            page = stock_render.document(summary["rows"], summary, "available", {"date": "26/11/2026", "time": "21.08"})
        self.assertIn("<span>ปลวกแดง\u00a01</span> / <span>ระยอง-สำนักงานใหญ่\u00a01</span>", page)
        self.assertIn("td.p span{display:inline-block}", page)  # 整块换行；不用 nowrap（超长地点名会被截掉）

    def test_all_and_unallocated_keep_every_status(self):
        rows = report.parse_export(export(TEST_LIBRARY))
        self.assertEqual(report.summarize(rows, "all", CARS)["total"], 9)
        self.assertEqual(report.summarize(rows, "unallocated", CARS)["total"], 9)
        self.assertEqual([spec["allocate"] for spec in report.SCOPES.values()], ["1", "1", "2"])


class Layout(unittest.TestCase):
    """版式照岳父样表 1:1（用户 11-26）：一行三格绿表头（第一格横跨车名与代码两列）、没有标题行、没有网格线、底部一行
    「อัพเดต 日期 เวลา 时间 Total」且合计在台数列下；多张图时每张顶上同一行表头，底部那行只在最后一张。"""

    def page(self, summary, scope="available", **kw):
        with lang("th"):
            return stock_render.document(summary["rows"], summary, scope,
                                         stock_render.as_of_label(datetime(2026, 11, 20, 16, 53)), **kw)

    def test_single_three_cell_header_and_sample_footer(self):
        same = {code: {"name": code, "year": "2026"} for code in ("DM-2D19", "DM-4D19", "DM-4D30", "Test", "TS-01")}
        summary = report.summarize(report.parse_export(export(TEST_LIBRARY)), "available", same)
        page = self.page(summary)
        self.assertEqual(page.count("</th>"), 4)
        self.assertIn("<th>รถรุ่น 26 MY ว่าง</th><th>Model/Color</th><th class=n>จำนวน</th><th>สถานที่จอดรถ</th>", page)
        self.assertNotIn("<h1", page)
        self.assertIn("border:0", page)
        self.assertIn("<tr class=f><td colspan=2>อัพเดต 20/11/2026 เวลา 16.53 Total</td><td class=n>5</td>", page)

    def test_header_wording_for_year_and_scope(self):
        rows = report.parse_export(export(TEST_LIBRARY))
        with lang("th"):
            self.assertEqual(stock_render.header_text("available", ""), ("รถรุ่น ว่าง", "Model/Color"))
            self.assertEqual(stock_render.header_text("all", "26"), ("รถรุ่น 26 MY", "Model/Color"))
            self.assertEqual(stock_render.header_text("unallocated", ""), ("รถรุ่น", "Model/Color"))
        with lang("zh"):
            self.assertEqual(stock_render.header_text("available", "26"), ("26 MY 空车", "车型/颜色"))
            self.assertEqual(stock_render.header_text("available", ""), ("空车", "车型/颜色"))
            self.assertEqual(stock_render.header_text("all", ""), ("车辆", "车型/颜色"))
        with lang("en"):
            self.assertEqual(stock_render.header_text("all", ""), ("Vehicles", "Model/Color"))
        with lang("ja"):
            self.assertEqual(stock_render.header_text("available", "26"), ("26 MY 空き車", "車種/色"))
            self.assertEqual(stock_render.header_text("all", ""), ("車両", "車種/色"))
        self.assertIn("<th>รถรุ่น ว่าง</th><th>Model/Color</th>", self.page(report.summarize(rows, "available", CARS)))

    def test_pages_repeat_the_header_and_only_the_last_has_the_footer(self):
        summary = report.summarize(report.parse_export(export(big_library())), "available", {})
        pages = stock_render.image_pages(summary)
        self.assertGreater(len(pages), stock_render.MAX_IMAGES)
        self.assertTrue(all(len(page) <= stock_render.ROWS_PER_IMAGE for page in pages))
        first = self.page({**summary, "rows": pages[0]}, footer=False)
        last = self.page({**summary, "rows": pages[1]}, first_index=stock_render.ROWS_PER_IMAGE)
        self.assertEqual((first.count("<thead>"), last.count("<thead>")), (1, 1))
        self.assertNotIn("class=f", first)
        self.assertIn("class=f", last)


class Alignment(unittest.TestCase):
    """每一列表头与数据同一种对齐（用户 11-26「表头和内容要对齐……歪歪扭扭的」）：车名 / 代码 / 位置靠左，台数表头、数字、合计居中。"""

    LONG = [("USR30", "DWP", "E1", "C1", "รถว่าง", "ปลวกแดง", None, None, "T"),
            ("USR30", "DWP", "E2", "C2", "รถว่าง", "ระยอง โชว์รูม", None, None, "T")]

    def test_header_and_data_share_one_alignment_per_column(self):
        from openpyxl import load_workbook

        cars = {"USR30": {"name": "V-Cross Max 4Drs 3.0Ddi M AT", "year": "2026"}}
        with lang("th"):
            summary = report.summarize(report.parse_export(export(self.LONG)), "available", cars)
            as_of = stock_render.as_of_label(datetime(2026, 11, 20, 16, 53))
            page = stock_render.document(summary["rows"], summary, "available", as_of)
            sheet = load_workbook(io.BytesIO(stock_render.workbook(summary, "available", as_of))).active
        self.assertIn("th{background:#00FF00;color:#000;font-weight:700;text-align:left}th.n{text-align:center}", page)
        self.assertIn("td.n{text-align:center", page)
        self.assertIn("<th class=n>จำนวน</th>", page)
        self.assertIn("<tr class=f><td colspan=2>", page)  # 底部蓝字从车名列起，合计在台数列（td.n）
        self.assertIn("overflow-wrap:anywhere", page)  # 长车名在本列内换行，不压到代码列
        self.assertIn("table-layout:fixed", page)
        # 表头「Model/Color」与代码颜色列数据同列（第 2 格 / B 列）、同为靠左；其余各列同理
        self.assertIn("<th>รถรุ่น 26 MY ว่าง</th><th>Model/Color</th><th class=n>", page)
        self.assertIn("<td class=c>USR30 DWP</td>", page)
        heads = [sheet.cell(1, column).alignment.horizontal for column in range(1, 5)]
        data = [sheet.cell(2, column).alignment.horizontal for column in range(1, 5)]
        self.assertEqual(heads, ["left", "left", "center", "left"])
        self.assertEqual(heads, data)
        self.assertEqual([sheet["B1"].value, sheet["B2"].value], ["Model/Color", "USR30 DWP"])
        self.assertEqual(sheet["D2"].value, "ปลวกแดง 1 / ระยอง โชว์รูม 1")


class Colours(unittest.TestCase):
    """配色照岳父样表（用户 11-26）：表头绿底黑字加粗、隔行浅灰、台数红色加粗居中、底部蓝色加粗且合计数字红色。"""

    def test_workbook_uses_the_sample_layout_and_colours(self):
        from openpyxl import load_workbook

        with lang("th"):
            summary = report.summarize(report.parse_export(export(TEST_LIBRARY)), "all", CARS)
            as_of = stock_render.as_of_label(datetime(2026, 11, 20, 16, 53))
            sheet = load_workbook(io.BytesIO(stock_render.workbook(summary, "all", as_of))).active
        self.assertNotIn("A1:B1", [str(cell_range) for cell_range in sheet.merged_cells.ranges])  # 两半各一格
        self.assertEqual([sheet["A1"].value, sheet["B1"].value, sheet["C1"].value, sheet["D1"].value],
                         ["รถรุ่น", "Model/Color", "จำนวน", "สถานที่จอดรถ"])
        self.assertEqual(sheet["B1"].fill.fgColor.rgb[-6:], stock_render.HEAD_FILL)
        self.assertEqual(sheet["A1"].fill.fgColor.rgb[-6:], stock_render.HEAD_FILL)
        self.assertTrue(sheet["A1"].font.bold)
        first, second = sheet[2], sheet[3]
        self.assertEqual(first[2].font.color.rgb[-6:], stock_render.COUNT_COLOR)
        self.assertTrue(first[2].font.bold)
        self.assertEqual(first[2].alignment.horizontal, "center")
        self.assertEqual({first[0].fill.fill_type, second[0].fill.fgColor.rgb[-6:]}, {None, stock_render.STRIPE_FILL})
        footer = sheet.max_row
        self.assertEqual(sheet.cell(footer, 1).value, "อัพเดต 20/11/2026 เวลา 16.53 Total")
        self.assertEqual(sheet.cell(footer, 1).font.color.rgb[-6:], stock_render.FOOT_COLOR)
        self.assertEqual(sheet.cell(footer, 3).value, 9)
        self.assertEqual(sheet.cell(footer, 3).font.color.rgb[-6:], stock_render.COUNT_COLOR)
        self.assertEqual(sheet.max_row, 1 + 8 + 1)  # 表头 + 8 个车型颜色 + 底部一行，没有标题行


class Languages(unittest.TestCase):
    """报表文字跟发起人的 LINE 语言（`i18n.line_lang()`，后台任务里由 `line/out.run_guarded` 的 `i18n.line_language(user)` 设好），
    不写死某一种；四语各自的表头、底部一行（日期四语都用公历、照样表；时间：泰文用点）、文件名都对（用户 11-26：「不要写死中文」）。"""

    EXPECT = {"th": ("จำนวน", "อัพเดต 26/11/2026 เวลา 11.20 Total", "สต๊อกตามที่จอดรถ [26112026_1120]"),
              "zh": ("台数", "更新 26/11/2026 11:20 合计", "库存停放报表 [26112026_1120]"),
              "en": ("Qty", "Updated 26/11/2026 11:20 Total", "Stock by location [26112026_1120]"),
              "ja": ("台数", "更新 26/11/2026 11:20 合計", "在庫・保管場所 [26112026_1120]")}

    def test_each_language_has_its_own_headers_footer_dates_and_file_names(self):
        from openpyxl import load_workbook

        moment = datetime(2026, 11, 26, 11, 20)
        out_dir = os.environ.get("STOCK_REPORT_LANG_OUT")
        for code, (count, foot, name) in self.EXPECT.items():
            with self.subTest(code), lang(code):
                summary = report.summarize(report.parse_export(export(TEST_LIBRARY)), "all", CARS)
                as_of = stock_render.as_of_label(moment)
                book = load_workbook(io.BytesIO(stock_render.workbook(summary, "all", as_of))).active
                page = stock_render.document(summary["rows"], summary, "all", as_of)
                saved = []
                with patch.object(stock_files, "save", side_effect=lambda c, e, f, b: saved.append(f) or "https://x.test/d?f=1"):
                    stock_flow.messages({"summary": summary, "originals": {"xlsx": {"content": b"PK", "name": "o.xlsx"},
                                                                         "pdf": {"content": b"%PDF", "name": "o.pdf"}},
                                         "rendered": {"pdf": b"%PDF", "images": [b"j"], "omitted": 0}}, "all", as_of, moment)
                self.assertEqual(book["C1"].value, count)
                self.assertEqual(book.cell(book.max_row, 1).value, foot)
                self.assertIn(count, page)
                self.assertIn(foot, page)
                self.assertIn(name + ".xlsx", saved)
                self.assertIn(name + ".pdf", saved)
                if out_dir:
                    Path(out_dir, f"lang-{code}.xlsx").write_bytes(stock_render.workbook(summary, "all", as_of))


class Copy(unittest.TestCase):
    """两处菜单入口（关键词菜单卡、六宫格富菜单）名称四语一致，按钮标签不超 20 字。"""

    def glossary(self):
        with open(ROOT / "apps/pearnly_dms/pearnly_dms/i18n/messages.tsv", encoding="utf-8") as handle:
            return {row["key"]: row for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
                    if row.get("key")}

    def test_both_menu_entries_have_the_same_name_in_every_language(self):
        words = self.glossary()
        card, rich = words["dms.card.TXT_MENU_ITEM5"], words["dms.card.rich.query.title"]
        for code in ("en", "zh", "th", "ja"):
            self.assertEqual(card[code], rich[code], code)
        self.assertEqual(card["zh"], "报表查询")
        self.assertNotIn("dms.card.TXT_MENU_D5", words)  # 菜单卡说明小字直接用六宫格那条（同一个词条）
        source = (ROOT / "apps/pearnly_dms/pearnly_dms/menu/menu_cards.py").read_text(encoding="utf-8")
        self.assertIn('"dms.card.rich.query.sub"', source)

    def test_button_labels_fit_line(self):
        words = self.glossary()
        keys = ["dms.card.query.TYPE_STOCK", "dms.card.query.TYPE_SALES"] + [
            f"dms.card.report.{key}" for key in ("SCOPE_AVAILABLE", "SCOPE_ALL", "SCOPE_UNALLOCATED", "BTN_ORIGINAL_XLSX",
                                                "BTN_ORIGINAL_PDF", "BTN_SUMMARY_XLSX", "BTN_SUMMARY_PDF")]
        for key in keys:
            for code in ("en", "zh", "th", "ja"):
                self.assertLessEqual(len(words[key][code]), 20, (key, code))


class Entries(unittest.TestCase):
    """菜单卡第 5 项、富菜单那一格、菜单态打「5」、旧按钮 action=menu_query：都进同一个 open_query，先出 1 库存 / 2 销售。"""

    def test_every_entry_opens_the_same_report_picker(self):
        from pearnly_dms.line import cards
        from pearnly_dms.menu import menu_cards, menu_flow, rich_menu

        stubs = (patch("frappe.utils.get_url", return_value="https://dms.example.test"),
                 patch.object(menu_cards, "account_action", return_value=None),
                 patch.object(rich_menu, "account_action",
                              side_effect=lambda kind, label: {"type": "uri", "label": str(label), "uri": "https://x.test"}))
        for stub in stubs:
            stub.start()
            self.addCleanup(stub.stop)
        card_actions = [box for box in str(menu_cards.menu_card(can_query=True)).split("'data': '")[1:]]
        self.assertTrue(any(item.startswith("action=" + cards.ACT_MENU_QUERY) for item in card_actions))
        with lang("zh"):
            rich = rich_menu.payload(True, "zh")
        query_cell = [area["action"] for area in rich["areas"] if area["action"].get("data") == "action=menu_query"]
        self.assertEqual(len(query_cell), 1)
        self.assertEqual(query_cell[0]["label"], i18n.t("dms.card.rich.query.title", lang="zh"))
        self.assertEqual(menu_flow._MENU_CHOICES["5"], cards.ACT_MENU_QUERY)
        sent = []
        binding = {"user_id": "op@example.invalid", "line_user_id": "U1"}
        with patch.object(query_flow, "_can_query", return_value=True), \
                patch.object(query_flow.store, "set_session"), \
                patch.object(query_flow, "_send", side_effect=lambda user, msg, token: sent.append(msg)), lang("zh"):
            query_flow.handle_postback(binding, "U1", "tok", cards.ACT_MENU_QUERY, {}, None)
        items = sent[0]["quickReply"]["items"]
        self.assertEqual([item["action"]["label"] for item in items], ["1 库存停放报表", "2 销售记录"])
        self.assertEqual([parse_qs(item["action"]["data"])["kind"][0] for item in items], ["stock", "sales"])

    def test_no_query_permission_means_no_report_menu_and_no_report(self):
        binding = {"user_id": "op@example.invalid", "line_user_id": "U1"}
        with patch.object(query_flow.query_access, "can_query", return_value=False), \
                patch.object(query_flow, "_denied") as denied:
            query_flow.open_query(binding, "U1", "tok")
        denied.assert_called_once()
        with patch.object(stock_flow.query_access, "can_query", return_value=False), \
                patch("pearnly_dms.line.out.enqueue") as enqueue, \
                patch("pearnly_dms.line.out.push_or_reply_text") as reply:
            stock_flow.handle_postback(binding, "U1", "tok", stock_flow.ACT_STOCK_SCOPE, {"scope": "available"})
        enqueue.assert_not_called()
        reply.assert_called_once()

    def test_scope_buttons_queue_one_background_read_with_the_reading_fallback(self):
        binding = {"user_id": "op@example.invalid", "line_user_id": "U1"}
        with patch.object(stock_flow.query_access, "can_query", return_value=True), \
                patch("pearnly_core.line_session.set_session") as state, \
                patch("pearnly_dms.line.out.enqueue") as enqueue, lang("th"):
            stock_flow.handle_postback(binding, "U1", "tok", stock_flow.ACT_STOCK_SCOPE, {"scope": "available"})
        args, kwargs = enqueue.call_args
        self.assertEqual(args[:5], ("stock_report", stock_flow.run, binding, "U1", "available"))
        self.assertEqual(kwargs["waiting_text"], i18n.t("dms.card.query.MANUAL_RUNNING", lang="th"))
        state.assert_called_once_with("U1", stock_flow.WAITING_STATE, {"scope": "available"})

    def test_text_typed_while_waiting_replays_the_loading_animation(self):
        with patch("pearnly_dms.line.out.start_loading") as loading:
            self.assertTrue(query_flow.handle_text({}, "U1", "tok", {"state": stock_flow.WAITING_STATE}, "นานจัง"))
        loading.assert_called_once_with("U1", 60)
        self.assertFalse(stock_flow.handle_text("U1", {"state": "menu"}))

    def test_files_that_cannot_be_saved_answer_with_the_failure_sentence(self):
        binding = {"user_id": "op@example.invalid", "line_user_id": "U1"}
        sent = []
        summary = report.summarize(report.parse_export(export(TEST_LIBRARY)), "available", CARS)
        ok = {"ok": True, "summary": summary, "rendered": {"pdf": b"%PDF", "images": [b"j"], "omitted": 0},
              "originals": {}}
        with patch.object(stock_flow.query_access, "can_query", return_value=True), \
                patch.object(stock_flow, "_endpoint", return_value={"name": "EP-1"}), \
                patch.object(stock_flow, "_car_catalogue", return_value={}), \
                patch("pearnly_dms.adapter.operations.run_logged_in", return_value=ok), \
                patch("pearnly_dms.line.identity.matches", return_value=True), \
                patch("pearnly_dms.line.out.send", side_effect=lambda user, msg, *a: sent.append(msg)), \
                patch("pearnly_core.line_session.get_session", return_value={"state": stock_flow.WAITING_STATE}), \
                patch("pearnly_core.line_session.set_session") as state, \
                patch("frappe.db", MagicMock()), patch("frappe.log_error"), \
                patch("frappe.utils.now_datetime", return_value=datetime(2026, 11, 26, 11, 20)), lang("zh"):
            result = stock_flow.run(binding, "U1", "available")
        self.assertEqual(result["error_code"], "ERR_DMS_TECHNICAL")
        self.assertEqual([msg["type"] for msg in sent], ["text"])
        state.assert_called_once_with("U1", "query_menu", {})


class Messages(unittest.TestCase):
    def setUp(self):
        self.saved = []
        patcher = patch.object(stock_files, "save", side_effect=self.save)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, content, extension, filename, batch):
        self.saved.append((extension, filename))
        return f"https://dms.example.test/api/method/pearnly_dms.query.stock_files.download?f={len(self.saved)}"

    def result(self, summary, images=1):
        return {"summary": summary, "rendered": {"pdf": b"%PDF", "images": [b"jpg"] * images, "omitted": 0},
                "originals": {"xlsx": {"content": b"PK", "name": "รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ-ทั้งหมด [26112569_112012].xlsx"},
                              "pdf": {"content": b"%PDF", "name": "รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ-ทั้งหมด [26112569_112012].pdf"}}}

    def test_images_then_one_card_with_four_download_buttons(self):
        with lang("zh"):
            summary = report.summarize(report.parse_export(export(TEST_LIBRARY)), "available", CARS)
            out = stock_flow.messages(self.result(summary, images=3), "available",
                                      {"date": "26/11/2026", "time": "11:20"}, datetime(2026, 11, 26, 11, 20))
        self.assertLessEqual(len(out), 5)
        self.assertEqual([message["type"] for message in out], ["image", "image", "image", "flex"])
        # 卡片收紧（主会话 11-26，真机 d6b 标题下空一大块）：标题与四个按钮同在 body，没有 footer / spacer / separator / margin
        bubble = out[-1]["contents"]
        self.assertEqual(set(bubble), {"type", "size", "body"})
        body = bubble["body"]["contents"]
        self.assertEqual([part["type"] for part in body], ["text", "button", "button", "button", "button"])
        self.assertFalse(any("margin" in part or "offsetTop" in part for part in body))
        buttons = body[1:]
        self.assertEqual([button["action"]["label"] for button in buttons],
                         ["原件 Excel", "原件 PDF", "汇总 Excel", "汇总 PDF"])
        self.assertTrue(all("openExternalBrowser=1" in button["action"]["uri"] for button in buttons))
        # 原件保留 MR.ERP 原文件名（含「-ทั้งหมด」），不另加说明
        self.assertIn(("xlsx", "รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ-ทั้งหมด [26112569_112012].xlsx"), self.saved)

    def test_zero_rows_is_one_sentence_and_no_card(self):
        with lang("zh"):
            out = stock_flow.messages(self.result({"rows": [], "total": 0, "year": ""}), "available",
                                      {"date": "x", "time": "x"}, datetime(2026, 11, 26))
        self.assertEqual(out, [{"type": "text", "text": "没有可显示的车辆。"}])
        self.assertEqual(self.saved, [])


class Links(unittest.TestCase):
    def setUp(self):
        for target, kwargs in (("frappe.utils.password.get_encryption_key", {"return_value": "site-key"}),
                               ("frappe.utils.get_url", {"return_value": "https://dms.example.test"})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def link(self):
        doc = MagicMock(name="File")
        doc.name = "abc123"
        with patch("frappe.get_doc", return_value=doc):
            url = stock_files.save(b"PK", "xlsx", "สรุป.xlsx", "b1")
        return {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}

    def download(self, query):
        row = frappe._dict(file_name="dms-stock-b1-0000.xlsx", is_private=1)
        file_doc = MagicMock()
        file_doc.get_content.return_value = b"PK-content"
        with patch("frappe.db", MagicMock(get_value=MagicMock(return_value=row))), \
                patch("frappe.get_doc", return_value=file_doc):
            return stock_files.download(**query)

    def test_signed_link_downloads_with_the_display_name(self):
        response = self.download(self.link())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(), b"PK-content")
        self.assertIn("filename*=UTF-8''", response.headers["Content-Disposition"])

    def test_a_very_long_display_name_is_left_out_of_the_link(self):
        doc = MagicMock(name="File")
        doc.name = "abc123"
        with patch("frappe.get_doc", return_value=doc):
            url = stock_files.save(b"PK", "xlsx", "รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ" * 4 + ".xlsx", "b1")
        self.assertLessEqual(len(url) + len("&openExternalBrowser=1"), 1000)
        query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items()}
        self.assertNotIn("d", query)
        self.assertEqual(self.download(query).status_code, 200)

    def test_tampered_or_expired_links_are_not_found(self):
        query = self.link()
        self.assertEqual(self.download({**query, "d": "other.xlsx"}).status_code, 404)
        self.assertEqual(self.download({**query, "f": "other"}).status_code, 404)
        with patch.object(stock_files.time, "time", return_value=time.time() + stock_files.LINK_SECONDS + 5):
            self.assertEqual(self.download(query).status_code, 404)


@unittest.skipUnless(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path("/opt/ms-playwright").is_dir(),
                     "Chromium is only in the production image")
class Render(unittest.TestCase):
    """真出图：600 台车的桩，看分页、单张大小与耗时（主会话 11-26 要的实测；截图写到 STOCK_REPORT_OUT 目录）。"""

    def test_render_hundreds_of_cars(self):
        from playwright.sync_api import sync_playwright

        out_dir = os.environ.get("STOCK_REPORT_OUT")
        with lang("th"), sync_playwright() as play:
            browser = play.chromium.launch()
            try:
                for count, scope in ((9, "available"), (600, "available"), (600, "all")):
                    rows = TEST_LIBRARY if count == 9 else big_library(count)
                    summary = report.summarize(report.parse_export(export(rows)), scope, CARS)
                    as_of = stock_render.as_of_label(datetime(2026, 11, 26, 11, 20))
                    started = time.monotonic()
                    result = stock_render.render(browser.new_context(), summary, scope, as_of)
                    workbook = stock_render.workbook(summary, scope, as_of)
                    seconds = time.monotonic() - started
                    sizes = [len(image) for image in result["images"]]
                    print(f"\n{count} cars / {scope}: {summary['total']} vehicles, "
                          f"{len(summary['rows'])} model-colour rows, "
                          f"{len(sizes)} images {sizes} bytes, omitted {result['omitted']} rows, "
                          f"pdf {len(result['pdf'])} bytes, xlsx {len(workbook)} bytes, {seconds:.1f}s")
                    self.assertTrue(result["pdf"].startswith(b"%PDF"))
                    self.assertLessEqual(len(sizes), stock_render.MAX_IMAGES)
                    self.assertTrue(all(size < 1024 * 1024 for size in sizes))  # LINE 预览图上限 1 MB
                    if out_dir:
                        for index, image in enumerate(result["images"], 1):
                            Path(out_dir, f"stock-{count}-{scope}-{index}.jpg").write_bytes(image)
                        Path(out_dir, f"stock-{count}-{scope}.pdf").write_bytes(result["pdf"])
                        Path(out_dir, f"stock-{count}-{scope}.xlsx").write_bytes(workbook)
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
