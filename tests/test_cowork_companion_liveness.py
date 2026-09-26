"""助手是否实际连上（`credentials._online`）：旧线上 `erp-agent-liveness.ts` 同一判据。

在 bench 的 Python 下运行（需要 frappe 可导入，不连库）。
"""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest

from pearnly_cowork.companion import credentials

NOW = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)


class Doc(dict):
    """连接文档只用到 `get`。"""


def row(**changes):
    return SimpleNamespace(**{"name": "CRED-1", "token_generation": 2, "active_key": "k", "revoked_at": None,
                              **changes})


def doc(seen, credential="CRED-1", generation=2):
    return Doc(report_credential=credential, report_generation=generation,
               report_json=json.dumps({"agent_last_seen_at": seen}))


class AgentLiveness(unittest.TestCase):
    def test_current_key_reporting_within_three_minutes_is_online(self):
        for age in (0, 1, 179):
            seen = (NOW - timedelta(seconds=age)).isoformat()
            self.assertTrue(credentials._online(doc(seen), row(), NOW), age)

    def test_stale_offline_or_unreadable_heartbeat_is_not_online(self):
        for seen in ((NOW - timedelta(seconds=180)).isoformat(), "1970-01-01 00:00:00+00",
                     "", None, "not-a-time", "2026-09-26T04:00:00",  # 无时区：拿不准，不算
                     (NOW + timedelta(seconds=5)).isoformat()):
            self.assertFalse(credentials._online(doc(seen), row(), NOW), seen)
        self.assertFalse(credentials._online(Doc(report_credential="CRED-1", report_generation=2,
                                                 report_json="{broken"), row(), NOW))

    def test_issued_key_without_its_own_report_is_not_online(self):
        seen = NOW.isoformat()
        self.assertFalse(credentials._online(Doc(), row(), NOW))                       # 只生成了密钥
        self.assertFalse(credentials._online(doc(seen, credential="CRED-0"), row(), NOW))  # 旧密钥报的
        self.assertFalse(credentials._online(doc(seen, generation=1), row(), NOW))
        self.assertFalse(credentials._online(doc(seen), row(revoked_at=NOW), NOW))
        self.assertFalse(credentials._online(doc(seen), row(active_key=None), NOW))
        self.assertFalse(credentials._online(doc(seen), None, NOW))


if __name__ == "__main__":
    unittest.main()
