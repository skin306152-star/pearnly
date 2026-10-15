"""助手是否实际连上（`credentials._fresh`）：旧线上 `erp-agent-liveness.ts` 同一判据。

报到时间记在密钥行（`last_seen_at` / `last_seen_generation`，2026-10-07 起），不再读连接上报记录里的时间戳。
多台电脑后（ADR-021 第 3 批）不再要求这把密钥是连接当前的上报凭据：池里每台电脑看自己的密钥（`pool_peers`）。

在 bench 的 Python 下运行（需要 frappe 可导入，不连库）。
"""
from datetime import datetime, timedelta, timezone
import unittest

from pearnly_cowork.companion import credentials

NOW = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)


def fresh(seen, seen_generation=2, generation=2):
    return credentials._fresh(seen, seen_generation, generation, NOW)


class AgentLiveness(unittest.TestCase):
    def test_a_key_reporting_within_three_minutes_is_online(self):
        for age in (0, 1, 179):
            seen = NOW - timedelta(seconds=age)
            self.assertTrue(fresh(seen), age)
            self.assertTrue(fresh(seen.replace(tzinfo=None)), age)  # 库里是 UTC 无时区
            self.assertTrue(fresh(seen.isoformat()), age)

    def test_stale_offline_or_unreadable_heartbeat_is_not_online(self):
        for seen in (NOW - timedelta(seconds=180), None, "", "not-a-time", NOW + timedelta(seconds=5)):
            self.assertFalse(fresh(seen), seen)

    def test_a_report_before_the_key_was_reissued_does_not_count(self):
        self.assertFalse(fresh(NOW, seen_generation=1))
        self.assertFalse(fresh(NOW, seen_generation=None))

    def test_the_connection_report_no_longer_decides_who_is_online(self):
        self.assertFalse(hasattr(credentials, "_online"))
        self.assertFalse(hasattr(credentials, "online"))


if __name__ == "__main__":
    unittest.main()
