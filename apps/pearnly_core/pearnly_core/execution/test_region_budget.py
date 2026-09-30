"""每个区域一个额度桶、限流计数（用户 2026-09-30：Document AI 三区域切换、压测时看得到限流）。

离线跑：Redis 用替身，不连站点、不连 Redis、不调 API。
"""
import unittest
from unittest.mock import MagicMock, patch

from pearnly_core.execution import budget, provider
from pearnly_core.execution.errors import Deferred


class RegionBudget(unittest.TestCase):
    def acquire(self, **kwargs):
        client = MagicMock()
        client.get.return_value = '{"concurrency": 8, "rpm": 120, "tenant_concurrency": 3}'
        client.eval.return_value = [1, 0]
        with patch.object(budget, "config", return_value={"concurrency": 8, "tenant_concurrency": 3, "rpm": 120}), \
                patch.object(budget, "redis", return_value=client), patch.object(budget, "tenant", return_value="t"):
            with budget.permit("bucket", 600, **kwargs):
                pass
        acquire = [call for call in client.eval.call_args_list if call.args[0] == budget.ACQUIRE][0]
        return acquire.args[-2]  # rpm 是倒数第二个参数（最后是租期）

    def test_a_bucket_can_carry_its_own_minute_quota(self):
        self.assertEqual(self.acquire(rpm=10), 10)
        self.assertEqual(self.acquire(), 120)
        self.assertEqual(self.acquire(rpm=0), 120)

    def test_without_waiting_a_rate_limit_goes_straight_back_to_the_caller(self):
        slept = []
        with patch.object(provider, "_post", side_effect=Deferred("provider_429", 3)), \
                patch.object(provider, "_in_queue_job", return_value=True), \
                patch.object(provider.time, "sleep", side_effect=slept.append), \
                patch.object(budget, "note") as note:
            with self.assertRaises(Deferred):
                provider.post("https://example.invalid", {}, {}, 30, "m", "bucket", wait=False, label="documentai:us")
        self.assertEqual(slept, [])
        note.assert_called_once_with("deferred:provider_429", "documentai:us")

    def test_counters_never_break_a_call(self):
        with patch.object(budget, "redis", side_effect=RuntimeError("no site")):
            budget.note("http_429", "x")  # 写不进就算了


if __name__ == "__main__":
    unittest.main()
