"""MR.ERP 导入菜单与回读判定：纯规则 + 真实页面夹具（已打码，见 fixtures/mrerp/README.md）+ 旧单转人工补丁。

背景（2026-09-28，MR.ERP 推送假成功）：旧线上写死菜单 67 / 453 / 118 / 125，MR.ERP 不校验菜单、没关联日记账也回 "1"，
推送收到 "1" 就记成功。现在只用导入页上当前年度已关联日记账的菜单，多个时人选一次按账套记住，
推完在所选菜单列表里以我方单号结尾查到才算成功。
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

from pearnly_cowork.targets import mrerp_menus as menus  # noqa: E402
from pearnly_cowork.push.mrerp import readback  # noqa: E402

FIXTURES = root / "tests" / "fixtures" / "mrerp"
ACCOUNT = "6:1"


def page(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def seen(pages):
    return json.dumps(menus.observe(None, ACCOUNT, pages, at="2026-09-28T01:00:00"))


class Options(unittest.TestCase):
    def test_real_import_pages(self):
        self.assertEqual(menus.parse_options(page("impaptran_formupload.html")),
                         [{"idmenu": "68", "label": "ซื้อ", "linked": True},
                          {"idmenu": "454", "label": "ซื้อ-ค่าใช้จ่าย", "linked": True}])
        self.assertEqual([o["idmenu"] for o in menus.parse_options(page("impartran_formupload.html"))], ["119"])
        self.assertEqual([o["idmenu"] for o in menus.parse_options(page("imparse_formupload.html"))], ["126"])

    def test_page_without_a_menu_is_unreadable(self):
        self.assertIsNone(menus.parse_options('<h3>กรุณาป้อนข้อมูล "ตั้งค่าระบบสินค้า"</h3>'))
        self.assertEqual(menus.resolve(None, ""), (None, menus.UNREADABLE))

    def test_unlinked_option_is_never_used(self):
        options = menus.parse_options('<select name="selmenu"><option value="67" data-chklac="2">ซื้อ</option>'
                                      '<option value="68" data-chklac="1">ซื้อ</option></select>')
        self.assertEqual(menus.resolve(options, "")[0]["idmenu"], "68")
        self.assertEqual(menus.resolve(options, "67"), (None, menus.CHANGED))
        self.assertEqual(menus.resolve(options[:1], ""), (None, menus.NOT_LINKED))
        self.assertEqual(menus.resolve([], ""), (None, menus.NOT_LINKED))

    def test_several_linked_need_a_choice_and_the_choice_wins(self):
        options = menus.parse_options(page("impaptran_formupload.html"))
        self.assertEqual(menus.resolve(options, ""), (None, menus.CHOICE_REQUIRED))
        self.assertEqual(menus.resolve(options, "454")[0]["idmenu"], "454")
        self.assertEqual(menus.resolve(options, "999"), (None, menus.CHANGED))


class Choice(unittest.TestCase):
    """按连接 + 账套 + 单据类型记住；只能选最近读到的已关联选项；失效时请人重选。"""

    def setUp(self):
        self.observed = seen({"impaptran": menus.parse_options(page("impaptran_formupload.html")),
                              "impartran": menus.parse_options(page("impartran_formupload.html")),
                              "imparse": menus.parse_options(page("imparse_formupload.html"))})

    def test_view_asks_only_where_there_is_more_than_one(self):
        view = menus.view(None, self.observed, ACCOUNT)
        self.assertTrue(view["known"])
        self.assertEqual({k for k, v in view["kinds"].items() if v["needed"]}, {"purchase", "purchase_expense"})
        self.assertEqual(view["kinds"]["sales_credit"]["chosen"], "119")  # 唯一一个自动用
        self.assertFalse(menus.view(None, None, ACCOUNT)["known"])

    def test_choose_is_per_account_and_kind(self):
        raw = menus.choose(None, ACCOUNT, "purchase", "68", self.observed, actor="a@example.com", at="t")
        raw = menus.choose(json.dumps(raw), ACCOUNT, "purchase_expense", "454", self.observed, actor="a", at="t")
        self.assertEqual(menus.chosen_map(json.dumps(raw), ACCOUNT), {"purchase": "68", "purchase_expense": "454"})
        self.assertEqual(menus.chosen_map(json.dumps(raw), "7:1"), {})
        view = menus.view(json.dumps(raw), self.observed, ACCOUNT)
        self.assertFalse(any(v["needed"] for v in view["kinds"].values()))

    def test_only_linked_options_that_were_read_can_be_chosen(self):
        self.assertIsNone(menus.choose(None, ACCOUNT, "purchase", "67", self.observed, actor="a", at="t"))
        self.assertIsNone(menus.choose(None, ACCOUNT, "purchase", "68", None, actor="a", at="t"))
        self.assertIsNone(menus.choose(None, ACCOUNT, "stock_in", "68", self.observed, actor="a", at="t"))

    def test_choice_that_went_stale_is_asked_again(self):
        raw = json.dumps(menus.choose(None, ACCOUNT, "purchase", "454", self.observed, actor="a", at="t"))
        later = json.dumps(menus.observe(self.observed, ACCOUNT, {"impaptran": [
            {"idmenu": "68", "label": "ซื้อ", "linked": True}, {"idmenu": "70", "label": "ซื้อ 2", "linked": True},
            {"idmenu": "454", "label": "ซื้อ-ค่าใช้จ่าย", "linked": False}]}, at="t2"))
        kind = menus.view(raw, later, ACCOUNT)["kinds"]["purchase"]
        self.assertEqual((kind["reason"], kind["needed"], kind["chosen"]), (menus.CHANGED, True, None))

    def test_a_failed_read_keeps_the_last_options(self):
        failed = menus.observe(self.observed, ACCOUNT, None, at="t3", error="ERR_AUTH")
        view = menus.view(None, json.dumps(failed), ACCOUNT)
        self.assertEqual((view["known"], view["error"], view["error_at"]), (True, "ERR_AUTH", "t3"))


class Readback(unittest.TestCase):
    def test_listing_of_an_unlinked_menu_is_not_proof(self):
        self.assertEqual(readback.listing_problem(page("aptran_allview_unlinked.html")), "not_linked")
        self.assertIsNone(readback.listing_problem(page("aptran_allview_linked.html")))

    def test_row_found_by_its_ending_and_prefix_taken_from_mrerp(self):
        rows = readback.parse_rows(page("aptran_showdata_row.html"))
        self.assertEqual([(r.docno, r.amount) for r in rows], [("PG09", "214.00")])
        self.assertEqual(readback.find(rows, "09").docno, "PG09")
        self.assertIsNone(readback.find(rows, "9"[:0]))

    def test_no_data_and_ambiguous_rows_are_not_found(self):
        self.assertEqual(readback.parse_rows(page("aptran_showdata_nodata.html")), [])
        rows = [readback.ListedRow("PG690922-000001", "", "", ""), readback.ListedRow("PN690922-000001", "", "", "")]
        self.assertIsNone(readback.find(rows, "690922-000001"))
        self.assertIsNone(readback.find([readback.ListedRow("PG1690922-000001x", "", "", "")], "690922-000001"))

    def test_unprefixed_row_under_an_unlinked_menu_parses_but_listing_rejects_it(self):
        rows = readback.parse_rows(page("aptran_showdata_unprefixed.html"))
        self.assertEqual(readback.find(rows, "690922-524602").docno, "690922-524602")


class LegacyPatch(unittest.TestCase):
    """修复前的 MR.ERP 成功单改成待人工核对 + 结果不明；Express 与其它状态不动。"""

    def test_marks_only_mrerp_successes(self):
        try:
            import frappe
            from pearnly_cowork.patches import v0_0_7_mrerp_legacy_menu_review as legacy
        except ImportError as exc:
            self.skipTest(f"needs the Frappe bench environment: {exc}")
        rows = [frappe._dict(name="CWP-1", history="H1", document_key="D1", external_docnum="690922-1",
                             invoice_no="INV1", target_json=json.dumps({"adapter": "mrerp"})),
                frappe._dict(name="CWP-2", history="H2", document_key="D2", external_docnum="RR1",
                             invoice_no="INV2", target_json=json.dumps({"adapter": "express"})),
                frappe._dict(name="CWP-3", history="H3", document_key="D3", external_docnum="SI1",
                             invoice_no="INV3", target_json=json.dumps({"adapter": "mrerp"}))]
        writes = []

        def sql(query, values=None, as_dict=False):
            if "status='success'" in query:
                return rows
            return [("CWP-9",)] if values and values[0] == "D3" else []

        db = frappe._dict(table_exists=lambda name: True, sql=sql,
                          set_value=lambda doctype, name, value, update_modified=True: writes.append((doctype, name, value)),
                          get_value=lambda doctype, name, fields, as_dict=False: frappe._dict(
                              last_push_record="CWP-1" if name == "H1" else "CWP-8"))
        with patch.object(frappe, "db", db, create=True), patch("builtins.print"):
            legacy.execute()
        self.assertEqual(writes, [
            ("Cowork Push Record", "CWP-1", {"status": "manual", "result_unknown": 1, "reason": legacy.REASON,
                                             "active_key": "D1"}),
            ("Cowork OCR History", "H1", {"last_push_status": "manual", "last_push_reason": legacy.REASON}),
            ("Cowork Push Record", "CWP-3", {"status": "manual", "result_unknown": 1, "reason": legacy.REASON,
                                             "active_key": None}),
        ])


if __name__ == "__main__":
    unittest.main()
