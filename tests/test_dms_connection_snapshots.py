"""Zero-I/O RPC boundary tests; real connection-role checks, fake DB/queue service."""
from __future__ import annotations

import importlib
import csv
import json
from pathlib import Path
import sys
import re
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))


class Denied(Exception):
    pass


class Unsigned(Exception):
    pass


class Invalid(Exception):
    pass


def throws(message, exception=Invalid):
    raise exception(message)


def whitelist(**kwargs):
    def wrap(function):
        function.allowed_methods = kwargs.get("methods")
        return function
    return wrap


class ConnectionSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"name": "PRIMARY-LOCAL", "enabled": 1}]
        self.frappe = types.ModuleType("frappe")
        self.frappe._ = lambda text: text
        self.frappe.session = types.SimpleNamespace(user="admin@example.test")
        self.frappe.get_roles = Mock(return_value=["DMS Admin"])
        self.frappe.get_all = Mock(side_effect=lambda *a, **k: self.rows)
        self.frappe.whitelist = whitelist
        self.frappe.throw = throws
        self.frappe.PermissionError = Denied
        self.frappe.AuthenticationError = Unsigned
        self.manual = types.ModuleType("pearnly_dms.snapshots.manual")
        self.manual.status = Mock(side_effect=lambda ep, ds, **kw: {"state": "idle", "dataset": ds})
        self.manual.request = Mock(side_effect=lambda ep, ds, **kw: {
            "state": "queued", "dataset": ds, "request_id": "ab" * 16})
        endpoints = types.ModuleType("pearnly_core.endpoints")
        endpoints.read_password = Mock(side_effect=AssertionError("Status must not read passwords"))
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe,
            "pearnly_core.endpoints": endpoints, "pearnly_dms.snapshots.manual": self.manual})
        self.modules.start()
        for name in ("pearnly_dms.connections.api", "pearnly_dms.connections.snapshots"):
            sys.modules.pop(name, None)
        self.api = importlib.import_module("pearnly_dms.connections.snapshots")
        self.addCleanup(self.modules.stop)

    def test_status_is_the_only_rpc_and_bootstrap_is_internal(self):
        """连接页不再有手动触发：`get_status` 是唯一入口，`bootstrap` 只给绑定路径用。"""
        self.assertEqual(self.api.get_status.allowed_methods, ["GET"])
        self.assertFalse(hasattr(self.api, "request_refresh"),
                         "手动触发的 RPC 必须不存在（读取只发生在绑定与操作顺手带回）")
        self.assertFalse(hasattr(self.api.bootstrap, "allowed_methods"),
                         "bootstrap 不能是可被前端调用的白名单方法")
        for field in ("endpoint", "actor", "reader", "params", "datasets"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.api.get_status(**{field: "forged"})

    def test_guest_and_operator_denied_before_lookup_or_queue(self):
        for user, roles, error in (("Guest", [], Unsigned), ("staff", ["DMS User"], Denied)):
            self.frappe.session.user = user
            self.frappe.get_roles.return_value = roles
            with self.subTest(user=user), self.assertRaises(error):
                self.api.get_status()
        self.frappe.get_all.assert_not_called()
        self.manual.request.assert_not_called()
        self.manual.status.assert_not_called()

    def test_admin_roles_use_only_site_primary_authority(self):
        for role in ("DMS Admin", "System Manager"):
            self.frappe.get_roles.return_value = [role]
            self.manual.request.reset_mock()
            # 状态是**本站唯一那条主连接**的，不看客户端传来的任何目标。
            self.api.get_status()
            result = self.api.bootstrap("PRIMARY-LOCAL", "admin@example.test")
            self.assertEqual([row["dataset"] for row in result], list(self.api.DATASETS))
            self.manual.request.assert_any_call("PRIMARY-LOCAL", "geo", reader="authority",
                                               actor="admin@example.test")
        self.assertEqual(self.frappe.get_all.call_args.kwargs, {
            "filters": {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]},
            "fields": ["name", "enabled"], "limit_page_length": 2})

    def test_status_is_local_only_and_lists_every_group(self):
        # 四组都在：地址也是底座的一部分，它与其它三组一起在绑定时读。
        result = self.api.get_status()
        self.assertTrue(result["configured"])
        self.assertEqual([r["dataset"] for r in result["datasets"]],
                         ["vehicles", "customers", "company", "geo"])
        self.assertEqual(self.manual.status.call_count, 4)
        self.manual.request.assert_not_called()

    def test_bootstrap_reads_every_group_in_a_fixed_order(self):
        result = self.api.bootstrap("PRIMARY-LOCAL", "admin@example.test")
        self.assertEqual([row["dataset"] for row in result], list(self.api.DATASETS))
        self.assertEqual([call.args[1] for call in self.manual.request.call_args_list],
                         list(self.api.DATASETS))
        self.assertTrue(all(row["state"] == "queued" for row in result))

    def test_bootstrap_keeps_going_and_never_leaks_a_raw_error(self):
        """一组登记失败不能拖垮别组，也不能把上游原文写进返回。"""
        self.manual.request.side_effect = [RuntimeError("private upstream data"),
            {"state": "queued", "request_id": "ab" * 16},
            {"state": "queued", "request_id": "cd" * 16},
            {"state": "queued", "request_id": "ef" * 16}]
        result = self.api.bootstrap("PRIMARY-LOCAL", "admin@example.test")
        self.assertEqual([row["state"] for row in result],
                         ["failed", "queued", "queued", "queued"])
        self.assertEqual(result[0]["error_code"], "ERR_DMS_SNAPSHOT_INTERRUPTED")
        self.assertNotIn("private", json.dumps(result))

    def test_bootstrap_permission_error_propagates(self):
        self.manual.request.side_effect = self.frappe.PermissionError("denied")
        with self.assertRaises(Denied):
            self.api.bootstrap("PRIMARY-LOCAL", "admin@example.test")

    def test_unconfigured_status_is_empty_and_request_rejects(self):
        self.rows = []
        self.assertEqual(self.api.get_status(), {"configured": False, "datasets": []})
        self.manual.status.assert_not_called()
        self.manual.request.assert_not_called()

    def test_ambiguous_primary_rejected_even_for_status(self):
        self.rows += [{"name": "OTHER", "enabled": 1}]
        with self.assertRaises(Invalid):
            self.api.get_status()
        self.manual.status.assert_not_called()
        self.manual.request.assert_not_called()

    def test_disabled_connection_still_reports_status_without_queueing(self):
        self.rows[0]["enabled"] = 0
        result = self.api.get_status()
        self.assertEqual(len(result["datasets"]), len(self.api.DATASETS))
        self.manual.request.assert_not_called()

    def test_projection_only_fixed_metadata_no_raw_error_or_payload(self):
        self.manual.status.return_value = None
        self.manual.status.side_effect = lambda *a, **k: {
            "state": "failed", "as_of": 1720000000.5, "completed_at": 1720000010,
            "count": 21, "stale": True, "progress": {"processed": 7, "total": 25, "unit": "secret"},
            "error_code": "ERR_DMS_SNAPSHOT_INCOMPLETE", "password": "never-export",
            "customers": [{"name": "private"}], "raw_error": "credential", "endpoint": "private"}
        result = self.api.get_status()
        row = result["datasets"][0]
        self.assertEqual(row["as_of"], 1720000000.5)
        self.assertEqual(row["completed_at"], 1720000010)
        self.assertEqual(row["progress"], {"processed": 7, "total": 25, "unit": "vehicles"})
        self.assertTrue(row["stale"])
        self.assertEqual(row["error_code"], "ERR_DMS_SNAPSHOT_INCOMPLETE")
        for secret in ("never-export", "private", "credential", "secret"):
            self.assertNotIn(secret, json.dumps(result))
        row = self.api._public_status("geo", {"error_code": "err password=secret", "as_of": float("nan"),
            "count": -2, "completed_at": float("inf"), "progress": {"total": None}})
        self.assertEqual((row["error_code"], row["as_of"], row["completed_at"], row["count"]), ("", 0, 0, None))

    def test_bootstrap_uses_the_domain_entry_point_verbatim(self):
        """底座读取必须走域内的 `manual.request`，不自己造队列任务、不绕过去重。"""
        self.api.bootstrap("PRIMARY-LOCAL", "admin@example.test")
        self.assertEqual(self.manual.request.call_count, len(self.api.DATASETS))
        for call in self.manual.request.call_args_list:
            self.assertEqual(call.kwargs, {"reader": "authority", "actor": "admin@example.test"})

    def test_product_owned_four_language_keys_and_named_placeholders(self):
        source = (ROOT / "apps/pearnly_dms/pearnly_dms/public/desk/connect.js").read_text()
        self.assertIn('p.texts("dms.connect.cache.")', source)
        keys = set(re.findall(r'cacheText\("(dms\.connect\.cache\.[\w.]+)"', source))
        keys = {key for key in keys if not key.endswith(".")}
        keys |= {f"dms.connect.cache.dataset.{key}" for key in self.api.DATASETS}
        keys |= {f"dms.connect.cache.state.{key}" for key in self.api.STATES}
        keys |= {f"dms.connect.cache.field.{key}" for key in
                 ("state", "progress", "count", "as_of", "completed_at", "error_code")}
        keys |= {"dms.card.query.MANUAL_AS_OF", "dms.card.query.MANUAL_PROGRESS",
                 "dms.err.ERR_DMS_CUSTOMER_EXISTS", "dms.err.ERR_DMS_CUSTOMER_CHANGED",
                 "dms.err.ERR_DMS_CUSTOMER_UNCONFIRMED"}
        placeholders = None
        for lang in ("en", "zh", "th", "ja"):
            path = ROOT / f"apps/pearnly_dms/pearnly_dms/translations/{lang}.csv"
            with path.open(newline="") as handle:
                table = dict(row[:2] for row in csv.reader(handle) if len(row) >= 2)
            self.assertFalse(keys - table.keys(), f"Missing {lang}: {keys - table.keys()}")
            actual = {key: sorted(re.findall(r"\{[^{}]+\}", table[key])) for key in keys}
            if placeholders is None:
                placeholders = actual
            self.assertEqual(actual, placeholders, lang)
            self.assertEqual(actual["dms.card.query.MANUAL_AS_OF"], ["{time}"])
            self.assertEqual(actual["dms.card.query.MANUAL_PROGRESS"], ["{processed}", "{total}"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
