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


def export(rows, title="รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ-ทั้งหมด", total=None):
    book = Workbook()
    sheet = book.active
    sheet.append([title])
    sheet.append(["วันที่ 26/11/2569"])
    sheet.append(list(HEADER))
    for row in rows:
        sheet.append(list(row))
    sheet.append([f"รวม  {len(rows) if total is None else total}  รายการ"])
    for line in (1, 2, sheet.max_row):
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
    def test_available_means_status_starting_with_rot_wang_including_show_cars(self):
        self.assertTrue(report.is_available("รถว่าง"))
        self.assertTrue(report.is_available("รถว่าง-โชว์"))
        self.assertFalse(report.is_available("รถทีมสาขาจอง"))
        self.assertFalse(report.is_available("รถแต่งเสร็จ"))

    def test_available_scope_groups_by_model_colour_and_year(self):
        summary = report.summarize(report.parse_export(export(TEST_LIBRARY)), "available", CARS)
        self.assertEqual(summary["total"], 5)
        self.assertEqual([section["year"] for section in summary["sections"]], ["26", "25", ""])
        first = summary["sections"][0]["rows"]
        self.assertEqual([(row["name"], row["code"], row["count"]) for row in first],
                         [("D-MAX SPARK", "DM-2D19 ONX", 1), ("D-MAX SPARK", "DM-2D19 PWH", 1)])
        # 车名回落：没有 ชื่อรถ 用 ชื่อรุ่นรถ，再没有用代码
        self.assertEqual(summary["sections"][1]["rows"][0]["name"], "DM 4D")
        self.assertEqual(summary["sections"][2]["rows"][0]["name"], "Test")

    def test_locations_are_counted_and_joined_like_the_sample(self):
        rows = [{"model": "UNT20", "color": "ELG", "status": "รถว่าง", "location": place}
                for place in ("ระยอง", "ปลวกแดง", "ปลวกแดง")]
        row = report.summarize(rows, "available", {})["sections"][0]["rows"][0]
        self.assertEqual((row["code"], row["count"]), ("UNT20 ELG", 3))
        self.assertEqual(report.locations_text(row["locations"]), "ปลวกแดง 2 / ระยอง 1")

    def test_all_and_unallocated_keep_every_status(self):
        rows = report.parse_export(export(TEST_LIBRARY))
        self.assertEqual(report.summarize(rows, "all", CARS)["total"], 9)
        self.assertEqual(report.summarize(rows, "unallocated", CARS)["total"], 9)
        self.assertEqual([spec["allocate"] for spec in report.SCOPES.values()], ["1", "1", "2"])


class Pages(unittest.TestCase):
    def test_hundreds_of_cars_page_at_forty_rows_with_the_year_heading_repeated(self):
        with lang():
            summary = report.summarize(report.parse_export(export(big_library())), "available", {})
            pages = stock_render.image_pages(summary, "available")
        self.assertGreater(len(pages), stock_render.MAX_IMAGES)
        for page in pages:
            self.assertLessEqual(len(page), stock_render.ROWS_PER_IMAGE)
            self.assertEqual(page[0]["kind"], "head")


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
            out = stock_flow.messages(self.result(summary, images=3), "available", "26/11/2026 11:20",
                                      datetime(2026, 11, 26, 11, 20))
        self.assertLessEqual(len(out), 5)
        self.assertEqual([message["type"] for message in out], ["image", "image", "image", "flex"])
        buttons = out[-1]["contents"]["footer"]["contents"]
        self.assertEqual([button["action"]["label"] for button in buttons],
                         ["原件 Excel", "原件 PDF", "汇总 Excel", "汇总 PDF"])
        self.assertTrue(all("openExternalBrowser=1" in button["action"]["uri"] for button in buttons))
        # 原件保留 MR.ERP 原文件名（含「-ทั้งหมด」），不另加说明
        self.assertIn(("xlsx", "รายงานสต๊อกคงค้างแสดงสถานที่จอดรถ-ทั้งหมด [26112569_112012].xlsx"), self.saved)

    def test_zero_rows_is_one_sentence_and_no_card(self):
        with lang("zh"):
            out = stock_flow.messages(self.result({"sections": [], "total": 0}), "available", "x",
                                      datetime(2026, 11, 26))
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
                    started = time.monotonic()
                    result = stock_render.render(browser.new_context(), summary, scope, "26/11/2569 11:20")
                    workbook = stock_render.workbook(summary, scope, "26/11/2569 11:20")
                    seconds = time.monotonic() - started
                    sizes = [len(image) for image in result["images"]]
                    print(f"\n{count} cars / {scope}: {summary['total']} vehicles, "
                          f"{sum(len(s['rows']) for s in summary['sections'])} model-colour rows, "
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
