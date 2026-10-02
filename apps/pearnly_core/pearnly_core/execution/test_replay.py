"""回放压测开关（replay.py）。离线：不连站点、不连 Redis、不调 API。"""
import random
import statistics
import unittest
from unittest.mock import patch

import frappe

from pearnly_core.execution import provider, replay


class Replay(unittest.TestCase):
    def setUp(self):
        frappe.local.site = "testcowork.pearnly.com"
        frappe.local.conf = frappe._dict()
        self.addCleanup(lambda: [delattr(frappe.local, key) for key in ("site", "conf")
                                 if key in dict(iter(frappe.local))])

    def on(self, value):
        frappe.local.conf["pearnly_ocr_replay"] = value

    def test_off_unless_enabled_on_a_test_tenant(self):
        self.assertIsNone(replay.settings())
        self.on({"enabled": 0})
        self.assertIsNone(replay.settings())
        self.on({"enabled": 1})
        self.assertEqual(replay.settings(), {"median": 5.8, "p95": 19.7})
        for site in ("testcowork2.pearnly.com", "testerp.pearnly.com", "cowork-lab.localhost"):
            frappe.local.site = site
            self.assertTrue(replay.active(), site)

    def test_a_production_site_refuses_and_stays_real(self):
        self.on({"enabled": 1})
        with patch.object(replay, "_misconfigured") as logged:
            for site in ("cowork.pearnly.com", "testcowork.pearnly.com.evil.example", "acme.pearnly.com",
                         "testcowork.pearnly.co"):
                frappe.local.site = site
                self.assertIsNone(replay.settings(), site)
                replay.refuse_paid("gemini")  # 不拦：正式站照常真识别
        self.assertTrue(logged.called)  # 记错误日志（一小时一条，由 _misconfigured 自己限次）

    def test_profiles_and_explicit_seconds(self):
        self.on({"enabled": 1, "profile": "peak"})
        self.assertEqual(replay.settings(), {"median": 5.8, "p95": 40.0})
        self.on({"enabled": 1, "median": 3, "p95": 9})
        self.assertEqual(replay.settings(), {"median": 3.0, "p95": 9.0})
        self.on({"enabled": 1, "median": -1, "p95": True})        # 不合规的值回到默认档
        self.assertEqual(replay.settings(), {"median": 5.8, "p95": 19.7})

    def test_delays_follow_the_configured_median_and_p95(self):
        draws = random.Random(7)
        samples = sorted(replay.delay({"median": 5.8, "p95": 19.7}, rand=draws.random) for _ in range(20000))
        self.assertAlmostEqual(statistics.median(samples), 5.8, delta=0.25)
        self.assertAlmostEqual(samples[int(len(samples) * 0.95)], 19.7, delta=1.2)
        self.assertLessEqual(samples[-1], replay.MAX_DELAY)
        self.assertEqual(replay.delay({"median": 4.0, "p95": 4.0}), 4.0)

    def test_paid_calls_are_refused_while_replaying(self):
        self.on({"enabled": 1})
        with patch.object(provider, "_post", side_effect=AssertionError("不应发出付费调用")):
            with self.assertRaises(replay.Refused):
                provider.post("https://example.invalid", {}, {}, 10, "gemini-3.1-flash-lite", "bucket")

    def test_no_site_context_means_off(self):
        with patch.object(frappe, "conf", None), patch.object(frappe.local, "site", None, create=True):
            self.assertIsNone(replay.settings())


if __name__ == "__main__":
    unittest.main()
