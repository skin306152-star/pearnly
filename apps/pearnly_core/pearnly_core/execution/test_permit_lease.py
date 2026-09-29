"""结果不明之后名额还占多久（2026-09-29 Cowork 压测：几次读超时 / 连接重置把每租户 3 个名额锁 10 分钟）。

离线跑：Redis 用替身，不连站点、不连 Redis、不调 API。
"""
import unittest
from unittest.mock import MagicMock, patch

import requests

from pearnly_core.execution import budget, provider
from pearnly_core.execution.errors import Uncertain


class PermitLease(unittest.TestCase):
    def run_permit(self, error):
        client = MagicMock()
        client.get.return_value = '{"concurrency": 8, "rpm": 120, "tenant_concurrency": 3}'
        client.eval.return_value = [1, 0]
        with patch.object(budget, "config", return_value={"concurrency": 8, "tenant_concurrency": 3, "rpm": 120}), \
                patch.object(budget, "redis", return_value=client), \
                patch.object(budget, "tenant", return_value="tenant"):
            with self.assertRaises(type(error)):
                with budget.permit("bucket", 600):
                    raise error
        shortened = [call for call in client.eval.call_args_list if call.args[0] == budget.SHORTEN]
        return client, shortened

    def test_a_read_timeout_holds_the_permit_briefly(self):
        client, shortened = self.run_permit(Uncertain("read timeout", lease_seconds=60))
        self.assertEqual(len(shortened), 1)
        self.assertEqual(shortened[0].args[-1], 60)
        client.pipeline.assert_not_called()

    def test_a_closed_connection_gives_the_permit_back_now(self):
        client, shortened = self.run_permit(Uncertain("reset by peer", lease_seconds=0))
        self.assertEqual(shortened, [])
        client.pipeline.return_value.zrem.assert_called()

    def test_an_interruption_we_cannot_see_the_end_of_keeps_the_conservative_lease(self):
        client, shortened = self.run_permit(RuntimeError("job timeout"))
        self.assertEqual(shortened, [])
        client.pipeline.assert_not_called()

    def test_read_timeouts_and_resets_are_told_apart(self):
        self.assertEqual(provider._lease_after(requests.exceptions.ReadTimeout()), provider.READ_TIMEOUT_LEASE_SECONDS)
        self.assertEqual(provider._lease_after(requests.exceptions.ConnectionError()), 0)
        self.assertEqual(provider._lease_after(requests.exceptions.ChunkedEncodingError()), 0)


if __name__ == "__main__":
    unittest.main()
