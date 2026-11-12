"""Cowork 推送记录「在等 Express 关闭」的只读查询：读的时候算、90 秒过期、不认别的原因与别的状态（11-12）。"""
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_cowork"), str(ROOT / "apps/pearnly_core")]

ROWS = []


def _get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None):
    def match(row):
        for key, want in (filters or {}).items():
            if isinstance(want, list) and want[0] == "in":
                if row.get(key) not in want[1]:
                    return False
            elif row.get(key) != want:
                return False
        return True
    found = [dict(row) for row in ROWS if match(row)]
    if order_by == "updated_at desc":
        found.sort(key=lambda row: row["updated_at"], reverse=True)
    return found[:limit_page_length]


sys.modules.setdefault("frappe", types.ModuleType("frappe")).get_all = _get_all

from pearnly_core.cc_desktop.wire import ack_rules  # noqa: E402
from pearnly_cowork.push import express_wait  # noqa: E402

NOW = datetime(2026, 11, 12, 3, 0, 0)


def row(name, *, status="pending", reason=ack_rules.EXPRESS_OPEN, ago=10, key="s1"):
    return {"name": name, "status": status, "reason": reason, "updated_at": NOW - timedelta(seconds=ago),
            "connection": "CONN", "account_key": key}


class ExpressWait(unittest.TestCase):
    def setUp(self):
        ROWS[:] = [row("CWP-1"), row("CWP-2", reason="[DBF_WRITE_FAILED] io"), row("CWP-3", status="manual"),
                   row("CWP-4", ago=ack_rules.EXPRESS_OPEN_SHOWN_SECONDS + 1, key="s2"), row("CWP-5", status="leased")]

    def test_only_fresh_pending_records_waiting_for_express_count(self):
        self.assertEqual(list(express_wait.many(["CWP-1", "CWP-2", "CWP-3", "CWP-4", "CWP-5", "NOPE"], NOW)), ["CWP-1"])
        self.assertTrue(express_wait.one("CWP-1", NOW))
        self.assertFalse(express_wait.one("CWP-4", NOW))      # 电脑 90 秒没再回报：不再说在等 Express
        self.assertFalse(express_wait.one("", NOW))
        self.assertEqual(express_wait.many([], NOW), {})

    def test_ticket_rows_get_a_read_only_mark_in_one_query(self):
        # 录入列表 / 详情：票的行按 last_push_record 一次查完，在等的多带只读的 push_waiting；票的失败原因栏不动
        asked = []
        real = express_wait.frappe.get_all

        def counted(*args, **kwargs):
            asked.append(kwargs["filters"]["name"][1])
            return real(*args, **kwargs)

        rows = [{"name": "H1", "last_push_status": "pending", "last_push_record": "CWP-1", "last_push_reason": None},
                {"name": "H2", "last_push_status": "pending", "last_push_record": "CWP-2"},      # 排队中、别的原因
                {"name": "H3", "last_push_status": "manual", "last_push_record": "CWP-3", "last_push_reason": "X"},
                {"name": "H4", "last_push_status": "pending", "last_push_record": "CWP-4"},      # 90 秒没再回报
                {"name": "H5", "last_push_status": "success", "last_push_record": "CWP-1"},      # 票已不是录入中：不问
                {"name": "H6", "last_push_status": "pending", "last_push_record": ""},
                {"name": "H7"}]
        express_wait.frappe.get_all = counted
        try:
            self.assertIs(express_wait.mark(rows, NOW), rows)
        finally:
            express_wait.frappe.get_all = real
        self.assertEqual(asked, [["CWP-1", "CWP-2", "CWP-4"]])
        self.assertEqual({row["name"]: row.get("push_waiting") for row in rows},
                         {"H1": "express_open", "H2": None, "H3": None, "H4": None, "H5": None, "H6": None, "H7": None})
        self.assertEqual((rows[0]["last_push_reason"], rows[2]["last_push_reason"]), (None, "X"))

    def test_no_pending_rows_means_no_query(self):
        real = express_wait.frappe.get_all
        express_wait.frappe.get_all = lambda *a, **k: self.fail("queried")
        try:
            self.assertEqual(express_wait.mark([{"name": "H3", "last_push_status": "manual", "last_push_record": "CWP-3"}], NOW),
                             [{"name": "H3", "last_push_status": "manual", "last_push_record": "CWP-3"}])
            self.assertEqual(express_wait.mark([], NOW), [])
        finally:
            express_wait.frappe.get_all = real

    def test_per_account_set(self):
        self.assertTrue(express_wait.account("CONN", "s1", NOW))
        self.assertFalse(express_wait.account("CONN", "s2", NOW))
        self.assertFalse(express_wait.account("OTHER", "s1", NOW))
        self.assertFalse(express_wait.account("CONN", "", NOW))


if __name__ == "__main__":
    unittest.main()
