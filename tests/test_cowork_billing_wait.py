"""对账查余额：计费服务一时查不到就交还进程、过一会儿再查，到时限才报错；余额不足照旧马上报。镜像里跑，不连站点。"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
for name in [name for name in sys.modules if name.startswith(("pearnly_cowork", "pearnly_core"))]:
    del sys.modules[name]

import frappe  # noqa: E402
from pearnly_core.execution.errors import Deferred  # noqa: E402

from pearnly_cowork.recon_center import waiting  # noqa: E402

assert Path(waiting.__file__).is_relative_to(ROOT)


class BalanceWaitTest(unittest.TestCase):
    def run_once(self, check, clock, now):
        def settle(key, evaluate):
            self.assertTrue(key.startswith("balance:"))
            return evaluate(clock, now)
        with patch.object(waiting, "settle", side_effect=settle), \
                patch.object(frappe, "db", SimpleNamespace(commit=lambda: None), create=True):
            return waiting.balance(check, "job:left0:bank_statement")

    def unreachable(self):
        raise Deferred("lookup_error", 60)

    def test_unreachable_billing_waits_without_holding_a_process(self):
        clock = {}
        with self.assertRaises(waiting.Pending) as caught:
            self.run_once(self.unreachable, clock, 1000.0)
        self.assertEqual(caught.exception.due, 1000.0 + waiting.BALANCE_RETRY_SECONDS)
        self.assertEqual(clock["entered"], 1000.0)
        # 下一轮查到了就照常往下走
        self.assertEqual(self.run_once(lambda: "ok", clock, 1060.0), "ok")

    def test_gives_up_after_the_limit(self):
        clock = {"entered": 1000.0}
        with self.assertRaises(Deferred):
            self.run_once(self.unreachable, clock, 1000.0 + waiting.BALANCE_WAIT_SECONDS + 1)

    def test_insufficient_balance_is_reported_at_once(self):
        def poor():
            raise Deferred("insufficient_balance", 60)
        with self.assertRaises(Deferred) as caught:
            self.run_once(poor, {}, 1000.0)
        self.assertEqual(caught.exception.reason, "insufficient_balance")

    def test_both_recon_submitters_wait(self):
        for path in ("recon_center/document_ocr.py", "recon_center/invoice_ocr.py"):
            source = (ROOT / "apps/pearnly_cowork/pearnly_cowork" / path).read_text()
            self.assertIn("waiting.balance(lambda: intake.balance(", source, path)


if __name__ == "__main__":
    unittest.main()
