"""开工前的余额闸只读、不加锁（2026-10-01 生产压测：余额闸锁租户行 / 钱包 / 订阅 / 本月用量行，与同租户每一笔扣费抢锁，
计费服务 8 个请求位排满，问余额 10 秒读超时，提交被当成「查不到余额」拒收）。离线：数据库用替身。"""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_control import billing


class Gate(unittest.TestCase):
    def run_gate(self, wallet=SimpleNamespace(balance_thb=100), subscription=None, kind="pdf", units=2):
        db = mock.MagicMock()
        db.get_value.return_value = "SUB-1" if subscription else None
        calls = {}

        def wallet_of(tenant, create=True):
            calls["wallet_create"] = create
            return wallet

        def pages_used(tenant, month, *, lock=True):
            calls["usage_lock"] = lock
            return 10

        with mock.patch.object(billing.frappe, "db", db), \
                mock.patch.object(billing, "wallet_of", side_effect=wallet_of), \
                mock.patch("pearnly_control.payments.usage.pages_used", side_effect=pages_used), \
                mock.patch.object(billing.frappe, "get_doc", return_value=subscription), \
                mock.patch.object(billing, "estimate_cost_thb", return_value=Decimal("5")), \
                mock.patch.object(billing, "now", return_value="2026-10-01 10:00:00"), \
                mock.patch.object(billing, "_", side_effect=lambda text: text):
            result = billing.gate("T-1", kind=kind, units=units)
        return result, calls, db

    def test_the_gate_reads_without_any_lock(self):
        result, calls, db = self.run_gate(subscription=SimpleNamespace(
            status="active", cycle_end="2026-10-30 00:00:00", quota=100, pages_used_this_cycle=0, over_rate=1))
        self.assertTrue(result["allowed"])
        self.assertEqual((calls["wallet_create"], calls["usage_lock"]), (False, False))
        db.sql.assert_not_called()  # 不锁租户行
        for call in db.get_value.call_args_list:
            self.assertFalse(call.kwargs.get("for_update"))

    def test_the_three_answers_are_unchanged(self):
        self.assertEqual(self.run_gate()[0]["error_code"], "")
        poor = self.run_gate(wallet=SimpleNamespace(balance_thb=1))[0]
        self.assertEqual((poor["allowed"], poor["error_code"]), (False, "insufficient_balance"))
        # 还没有钱包的租户：余额 0，不在问余额时建钱包
        new = self.run_gate(wallet=None)[0]
        self.assertEqual(new["error_code"], "insufficient_balance")

    def test_a_read_failure_is_lookup_error_not_insufficient_balance(self):
        with mock.patch.object(billing, "_bkk_year_month", side_effect=RuntimeError("db gone")), \
                mock.patch.object(billing.frappe, "log_error"):
            result, _calls, _db = self.run_gate()
        self.assertEqual(result["error_code"], "lookup_error")

    def test_charging_still_locks(self):
        import inspect

        source = inspect.getsource(billing.charge)
        self.assertIn("wallet_of(tenant)", source)  # 默认加锁（租户行 + 钱包）
        self.assertIn("_active_by_cycle(tenant)", source)
        self.assertNotIn("lock=False", source)


if __name__ == "__main__":
    unittest.main()
