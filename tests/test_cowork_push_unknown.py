"""Cowork 推送结果不明（`push.unknown`，主会话 11-25：结果不明不得显示成失败）：录入列表、复核页、公司待处理同一判据。

断言应有状态：
- 停在转人工、推送记录标了结果不明的（原因栏可能是 ERR_TECHNICAL 之类）算结果不明；原因栏是结果不明码的也算；别的转人工不算；
- 列表一页补只读 ``push_unknown``，一次查完；
- 「结果不明」筛选只要转人工里结果不明的；「失败」筛选去掉它们；
- 网页：列表状态药丸与筛选有「结果不明」，复核页用 ``push_unknown`` 给「核对」；历史域经钩子拿筛选（不导入推送域）。

    python3 tests/test_cowork_push_unknown.py
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for app in ("pearnly_core", "pearnly_cowork"):
    sys.path.insert(0, str(ROOT / "apps" / app))

CALLS = []
RECORDS = {"PR1": {"result_unknown": 1, "status": "manual", "history": "H1"},
           "PR2": {"result_unknown": 0, "status": "manual", "history": "H2"},
           "PR3": {"result_unknown": 1, "status": "manual", "history": "H3"}}


def _get_all(doctype, filters=None, pluck=None, **kwargs):
    CALLS.append((doctype, dict(filters or {}), kwargs))
    rows = [{"name": name, **row} for name, row in RECORDS.items()]
    if "name" in (filters or {}):
        rows = [row for row in rows if row["name"] in filters["name"][1]]
    for key in ("result_unknown", "status"):
        if key in (filters or {}):
            rows = [row for row in rows if row[key] == filters[key]]
    return [row[pluck] for row in rows] if pluck else rows


frappe = types.ModuleType("frappe")
frappe.get_all = _get_all
sys.modules["frappe"] = frappe
lifecycle = types.ModuleType("pearnly_cowork.push.lifecycle")
lifecycle.CONFIRMED_UNACKED = "confirmed_push_unacked"   # 真模块导入共用层运行框架；这里只要这一个常量（下面按源码核对）
sys.modules["pearnly_cowork.push.lifecycle"] = lifecycle

from pearnly_cowork.push import unknown  # noqa: E402


class Unknown(unittest.TestCase):
    def setUp(self):
        CALLS.clear()

    def test_the_constant_is_the_lifecycle_one(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/push/lifecycle.py").read_text(encoding="utf-8")
        self.assertIn('CONFIRMED_UNACKED = "confirmed_push_unacked"', source)

    def test_manual_with_a_flagged_record_or_an_unknown_code_is_unknown(self):
        flagged = unknown.records(["PR1", "PR2"])
        self.assertEqual(flagged, frozenset({"PR1"}))
        cases = [
            ({"last_push_status": "manual", "last_push_record": "PR1", "last_push_reason": "ERR_TECHNICAL"}, True),
            ({"last_push_status": "manual", "last_push_record": "PR2", "last_push_reason": "ERR_TECHNICAL"}, False),
            ({"last_push_status": "manual", "last_push_reason": "ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"}, True),
            ({"last_push_status": "manual", "last_push_reason": "EXPRESS_MANUAL: confirmed_push_unacked"}, True),
            ({"last_push_status": "manual", "last_push_reason": "mrerp.result_unknown: ERR_AUTH"}, True),
            ({"last_push_status": "success", "last_push_record": "PR1"}, False),
            # 旧票没有推送状态、只记了结果不明的码：也是结果不明（主会话 11-28：原来两档都筛不到）
            ({"last_push_status": "", "last_push_reason": "ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"}, True),
            ({"last_push_status": "", "last_push_record": "PR1", "last_push_reason": "ERR_TECHNICAL"}, False),
            ({"last_push_status": "pending", "last_push_reason": "ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"}, False),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(unknown.is_unknown(row, flagged), expected)

    def test_a_page_is_marked_in_one_query(self):
        rows = [{"last_push_status": "manual", "last_push_record": "PR1"}, {"last_push_status": "manual", "last_push_record": "PR2"},
                {"last_push_status": "pending", "last_push_record": "PR3"},
                {"last_push_status": "", "last_push_reason": "ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"}]
        unknown.mark(rows)
        self.assertEqual([row.get("push_unknown") for row in rows], [1, None, None, 1])
        self.assertEqual(len(CALLS), 1)
        self.assertEqual(sorted(CALLS[0][1]["name"][1]), ["PR1", "PR2"])   # 只问转人工的几条

    def test_list_filters_split_unknown_out_of_failed(self):
        both, either = unknown.list_filters("unknown")
        self.assertEqual(both, [["last_push_status", "not in", ["success", "pending"]]])  # 停着的：转人工或旧票没有状态
        self.assertEqual(either[0], ["name", "in", ["H1", "H3"]])
        self.assertEqual([row[1:] for row in either[1:]], [["like", "%result_unknown%"], ["like", "%confirmed_push_unacked%"]])
        both, either = unknown.list_filters("failed")
        self.assertEqual(both[0], ["name", "not in", ["H1", "H3"]])
        self.assertEqual([row[1:] for row in both[1:]], [["not like", "%result_unknown%"], ["not like", "%confirmed_push_unacked%"]])
        self.assertEqual(either, [])
        self.assertEqual(unknown.list_filters("pending"), ([], []))
        self.assertEqual(CALLS[-1][2]["limit_page_length"], unknown.NAMES_MAX)   # 有界

    def test_pages_and_hooks(self):
        desk = ROOT / "apps/pearnly_cowork/pearnly_cowork/public/desk"
        listing = (desk / "intake.js").read_text(encoding="utf-8")
        self.assertIn('row?.push_unknown ? ["orange", () => __("Result unknown")]', listing)
        self.assertIn('{value: "unknown", label: __("Result unknown")}', listing)
        detail = (desk / "intake-upload.js").read_text(encoding="utf-8")
        self.assertIn("Boolean(record.push_unknown) || RESULT_UNKNOWN.test", detail)
        hooks = (ROOT / "apps/pearnly_cowork/pearnly_cowork/hooks.py").read_text(encoding="utf-8")
        self.assertIn('"pearnly_cowork.push.unknown.mark"', hooks)
        self.assertIn('cowork_history_push_filters = ["pearnly_cowork.push.unknown.list_filters"]', hooks)
        api = (ROOT / "apps/pearnly_cowork/pearnly_cowork/history/api.py").read_text(encoding="utf-8")
        self.assertNotIn("pearnly_cowork.push", api)   # 历史域不导入推送域，经钩子
        pending = (ROOT / "apps/pearnly_cowork/pearnly_cowork/companies/pending.py").read_text(encoding="utf-8")
        self.assertIn("push_unknown.is_unknown(row, unknown)", pending)   # 公司待处理同一判据


if __name__ == "__main__":
    unittest.main()
