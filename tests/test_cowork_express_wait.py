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

    def test_per_account_set(self):
        self.assertTrue(express_wait.account("CONN", "s1", NOW))
        self.assertFalse(express_wait.account("CONN", "s2", NOW))
        self.assertFalse(express_wait.account("OTHER", "s1", NOW))
        self.assertFalse(express_wait.account("CONN", "", NOW))


if __name__ == "__main__":
    unittest.main()
