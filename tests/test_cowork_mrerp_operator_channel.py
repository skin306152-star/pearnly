"""MR.ERP 连接的密钥报到（ADR-026 第 3 步，11-03 主会话选 A：没有 Express 连接的工作空间，CC 经 MR.ERP 连接领代录单）。

在 bench 的 Python 下运行（需要 frappe 可导入，不连库；`scripts/ci-candidate-checks.sh` 的 offline 列表）。开关是共用层全局常量
`contracts.MRERP_OPERATOR_ENABLED`（默认关），两种状态都锁：

- 关着：MR.ERP 连接的密钥报到一律 403（`http_auth` 在前面已拦，这里再拦一次），不记任何东西；
- 开着：只记报到时间、电脑行上的版本与能代录的目标、本机授权的 MR.ERP 连接与用户名；不进账套采集、回复不带账套与「没变」许可；
- Express 连接的报到两种状态都走原来那条（账套采集照旧）。
"""
from datetime import datetime
import unittest
from unittest import mock

import frappe

from pearnly_core.express_operator import contracts as operator_contracts
from pearnly_cowork.companion import heartbeat


class OperatorHeartbeat(unittest.TestCase):
    BODY = {"companion_version": "1.13.0", "max_payload_version": 18, "operator_targets": ["express", "mrerp"],
            "mrerp_logins": ["C-M"], "mrerp_users": {"C-M": "pc-user"}, "account_sets": [{"code": "X"}],
            "account_set": r"Z:\X\S1", "mapping": {"ap_acc": "2100"}, "device": "PC-1",
            "link": {"kind": "cloud", "via": "ACC-01", "host": "z1"}}

    def run_report(self, *, adapter, enabled, body=None):
        context = mock.Mock(credential_id="KEY-M", token_generation=3)
        doc = frappe._dict(name="C-M" if adapter == "mrerp" else "C-E", adapter=adapter, workspace="WS", report_json="{}")
        self.calls = []
        record = lambda name: (lambda *a, **k: self.calls.append((name,) + a))
        patches = [
            mock.patch.object(operator_contracts, "MRERP_OPERATOR_ENABLED", enabled),
            mock.patch.object(heartbeat.credentials, "revalidate", return_value=context),
            mock.patch.object(heartbeat.frappe, "get_doc", return_value=doc),
            mock.patch.object(heartbeat.credentials, "device_of", return_value="DEV-1"),
            mock.patch.object(heartbeat.credentials, "record_seen", side_effect=record("seen")),
            mock.patch.object(heartbeat.credentials, "full_report_due", return_value=False),
            mock.patch.object(heartbeat.devices, "state", return_value={"companion_version": "1.12.0"}),
            mock.patch.object(heartbeat.devices, "member", return_value=mock.Mock()),
            mock.patch.object(heartbeat.devices, "record_report", side_effect=record("report")),
            mock.patch.object(heartbeat.devices, "record_mrerp_logins", side_effect=record("logins")),
            mock.patch.object(heartbeat.devices, "record_mrerp_users", side_effect=record("users")),
            mock.patch.object(heartbeat.devices, "record_link", side_effect=record("link")),
            mock.patch.object(heartbeat.collector, "ingest", side_effect=lambda *a, **k: self.calls.append(("ingest",))
                              or {"connected": True, "account_set": "S1"}),
            mock.patch.object(heartbeat.release, "latest", return_value={"version": "1.13.1", "url": "/downloads/x"}),
            # 真的 403 要请求上下文（Frappe 的 local / flags），这里只核走到了它
            mock.patch.object(heartbeat.http_auth, "_deny_permission",
                              side_effect=lambda: self.calls.append(("denied",)) or (_ for _ in ()).throw(
                                  frappe.AuthenticationError("companion.unauthorized"))),
        ]
        for patch in patches:
            self.enterContext(patch)
        return heartbeat._receive(context, dict(body or self.BODY))

    def test_with_the_constant_off_an_mrerp_key_is_refused_and_nothing_is_recorded(self):
        with self.assertRaises(frappe.AuthenticationError):
            self.run_report(adapter="mrerp", enabled=False)
        self.assertEqual(self.calls, [("denied",)])

    def test_with_the_constant_on_an_mrerp_key_records_only_its_own_computer(self):
        reply = self.run_report(adapter="mrerp", enabled=True)
        self.assertEqual(reply, {"connected": True, "companion_latest": {"version": "1.13.1", "url": "/downloads/x"}})
        names = [call[0] for call in self.calls]
        self.assertEqual(names, ["seen", "report", "logins", "users", "link"])
        self.assertNotIn("ingest", names)
        _, device, connection, plan = self.calls[1]
        self.assertEqual((device, connection), ("DEV-1", "C-M"))
        # 只有这台电脑自己的几项进补丁：没有账套、科目映射、所选
        self.assertEqual(plan.patch["companion_version"], "1.13.0")
        self.assertEqual(plan.patch["max_payload_version"], 18)
        self.assertIn("operator_targets", plan.patch)
        for key in ("reported_account_sets", "ap_acc", "account_set"):
            self.assertNotIn(key, plan.patch)
        self.assertEqual(self.calls[2], ("logins", "DEV-1", ["C-M"]))
        self.assertEqual(self.calls[3], ("users", "DEV-1", {"C-M": "pc-user"}))
        # 经哪台电脑连上来（云端 Express 的来源标记）：照原样交给电脑行，由纯约定净化
        self.assertEqual(self.calls[4], ("link", "DEV-1", {"kind": "cloud", "via": "ACC-01", "host": "z1"}))
        self.assertEqual(self.calls[0][1:3], ("KEY-M", 3))

    def test_an_mrerp_key_going_offline_records_the_offline_mark_only(self):
        reply = self.run_report(adapter="mrerp", enabled=True, body={"offline": True})
        self.assertEqual(reply["connected"], False)
        self.assertEqual([call[0] for call in self.calls], ["seen", "report"])
        self.assertTrue(self.calls[1][3].offline)

    def test_express_keys_keep_the_original_report_in_both_states(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                reply = self.run_report(adapter="express", enabled=enabled)
                self.assertIn("ingest", [call[0] for call in self.calls])
                self.assertIn(("link", "DEV-1", self.BODY["link"]), self.calls)
                self.assertEqual(reply["account_sets_unchanged_ok"], True)
                self.assertEqual(reply["account_set"], "S1")


if __name__ == "__main__":
    unittest.main()
