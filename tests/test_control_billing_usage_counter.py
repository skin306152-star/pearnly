"""扣费读钱包上的本月按量页数计数（用户 2026-10-01 同意）：不再每笔都在租户锁里锁住、加一遍本月全部用量行。

守住：计数对得上就不扫台账；换月 / 第一次从台账算；每天第一笔再核台账，对不上记日志、以台账为准；问余额不加锁不改钱包；
台账行的月份与计数同一个；价格口径（estimate_cost_thb 的输入）不变。离线：数据库与台账用替身。"""
import unittest
from decimal import Decimal
from unittest import mock

import frappe

from pearnly_control import billing


class Counter(unittest.TestCase):
    def setUp(self):
        self.scans = []
        self.logged = []
        patches = [
            mock.patch.object(billing, "_pages_used_this_month",
                              side_effect=lambda tenant, ym, lock=True: self.scans.append((ym, lock)) or self.ledger),
            mock.patch.object(billing, "_bkk_today", return_value="2026-10-01"),
            mock.patch.object(billing.frappe, "log_error", side_effect=lambda *a, **k: self.logged.append(a)),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ledger = 1200

    def test_a_counter_checked_today_is_used_without_a_scan(self):
        wallet = frappe._dict(pages_used_this_month=1200, usage_month="2026-10", usage_checked="2026-10-01")
        self.assertEqual(billing._payg_pages_used("T", wallet, "2026-10", lock=True), 1200)
        self.assertEqual(self.scans, [])

    def test_a_new_month_or_first_charge_counts_the_ledger_once(self):
        for wallet in (frappe._dict(pages_used_this_month=999, usage_month="2026-09", usage_checked="2026-09-30"),
                       frappe._dict(pages_used_this_month=5)):  # 上线前的钱包：没有月份
            self.scans.clear()
            self.assertEqual(billing._payg_pages_used("T", wallet, "2026-10", lock=True), 1200)
            self.assertEqual(self.scans, [("2026-10", True)])
            self.assertEqual((wallet.usage_month, wallet.usage_checked, wallet.pages_used_this_month),
                             ("2026-10", "2026-10-01", 1200))
        self.assertEqual(self.logged, [])  # 换月不是漂移

    def test_the_daily_check_fixes_and_reports_drift(self):
        wallet = frappe._dict(pages_used_this_month=1190, usage_month="2026-10", usage_checked="2026-09-30")
        self.assertEqual(billing._payg_pages_used("T", wallet, "2026-10", lock=True), 1200)
        self.assertEqual(len(self.logged), 1)
        self.assertIn("counter=1190 ledger=1200", self.logged[0][0])
        self.assertEqual(wallet.pages_used_this_month, 1200)

    def test_the_gate_reads_without_lock_and_never_writes(self):
        wallet = frappe._dict(pages_used_this_month=1200, usage_month="2026-10", usage_checked="2026-09-01")
        self.assertEqual(billing._payg_pages_used("T", wallet, "2026-10", lock=False), 1200)
        self.assertEqual(self.scans, [])
        self.assertEqual(wallet.usage_checked, "2026-09-01")
        self.assertEqual(billing._payg_pages_used("T", None, "2026-10", lock=False), 1200)
        self.assertEqual(self.scans, [("2026-10", False)])


class Charge(unittest.TestCase):
    def test_a_charge_prices_from_the_counter_and_moves_it_with_the_ledger_row(self):
        wallet = mock.MagicMock()
        wallet.get.side_effect = lambda key: {"usage_month": "2026-10", "usage_checked": "2026-10-01"}.get(key)
        wallet.pages_used_this_month, wallet.balance_thb = 1200, 500
        rows = []
        db = mock.MagicMock(**{"get_value.return_value": None})
        with mock.patch.object(billing, "wallet_of", return_value=wallet), \
                mock.patch.object(billing.frappe, "db", db), \
                mock.patch.object(billing, "_active_by_cycle", return_value=None), \
                mock.patch.object(billing, "_bkk_year_month", return_value="2026-10"), \
                mock.patch.object(billing, "_bkk_today", return_value="2026-10-01"), \
                mock.patch.object(billing, "_pages_used_this_month", side_effect=AssertionError("no ledger scan")), \
                mock.patch.object(billing, "estimate_cost_thb", return_value=Decimal("2.40")) as estimate, \
                mock.patch.object(billing, "_write_txn", side_effect=lambda *a, **k: rows.append(k)), \
                mock.patch.object(billing, "now", return_value="2026-10-01 10:00:00"):
            result = billing.charge("T", product="cowork", kind="pdf", units=3, reference="r1")
        self.assertTrue(result["ok"])
        self.assertEqual(estimate.call_args.args[:3], (1200, "pdf", 3))  # 阶梯价的输入同原来：本月已用页数
        self.assertEqual((wallet.pages_used_this_month, wallet.usage_month), (1203, "2026-10"))
        self.assertEqual((rows[0]["billing_mode"], rows[0]["month"], rows[0]["pages"]), ("payg", "2026-10", 3))
        # 钱包一条 UPDATE 写回（不整份保存、不每笔多写一条修改留痕），与台账行同一个事务
        wallet.save.assert_not_called()
        db.set_value.assert_called_once()
        doctype, name, fields = db.set_value.call_args.args
        self.assertEqual((doctype, name), (billing.WALLET, wallet.name))
        self.assertEqual((fields["balance_thb"], fields["pages_used_this_month"], fields["usage_month"]),
                         (wallet.balance_thb, 1203, "2026-10"))


if __name__ == "__main__":
    unittest.main()
