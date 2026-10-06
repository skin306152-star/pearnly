"""助手是否实际连上（`credentials._online`）：旧线上 `erp-agent-liveness.ts` 同一判据。

报到时间记在密钥行（`last_seen_at` / `last_seen_generation`，2026-10-07 起），不再读连接上报记录里的时间戳。

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


def row(seen=None, seen_generation=2, **changes):
    return SimpleNamespace(**{"name": "CRED-1", "token_generation": 2, "active_key": "k", "revoked_at": None,
                              "last_seen_at": seen, "last_seen_generation": seen_generation, **changes})


def doc(credential="CRED-1", generation=2, **report):
    return Doc(report_credential=credential, report_generation=generation, report_json=json.dumps(report))


class AgentLiveness(unittest.TestCase):
    def test_current_key_reporting_within_three_minutes_is_online(self):
        for age in (0, 1, 179):
            seen = NOW - timedelta(seconds=age)
            self.assertTrue(credentials._online(doc(), row(seen), NOW), age)
            self.assertTrue(credentials._online(doc(), row(seen.replace(tzinfo=None)), NOW), age)  # 库里是 UTC 无时区
            self.assertTrue(credentials._online(doc(), row(seen.isoformat()), NOW), age)

    def test_stale_offline_or_unreadable_heartbeat_is_not_online(self):
        for seen in (NOW - timedelta(seconds=180), None, "", "not-a-time", NOW + timedelta(seconds=5)):
            self.assertFalse(credentials._online(doc(), row(seen), NOW), seen)

    def test_the_old_report_timestamp_no_longer_counts(self):
        self.assertFalse(credentials._online(doc(agent_last_seen_at=NOW.isoformat()), row(None), NOW))

    def test_issued_key_without_its_own_report_is_not_online(self):
        self.assertFalse(credentials._online(Doc(), row(NOW), NOW))                        # 只生成了密钥
        self.assertFalse(credentials._online(doc(credential="CRED-0"), row(NOW), NOW))     # 旧密钥报的
        self.assertFalse(credentials._online(doc(generation=1), row(NOW), NOW))
        self.assertFalse(credentials._online(doc(), row(NOW, seen_generation=1), NOW))    # 换钥前的报到
        self.assertFalse(credentials._online(doc(), row(NOW, revoked_at=NOW), NOW))
        self.assertFalse(credentials._online(doc(), row(NOW, active_key=None), NOW))
        self.assertFalse(credentials._online(doc(), None, NOW))


if __name__ == "__main__":
    unittest.main()
