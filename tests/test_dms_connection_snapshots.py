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

    def test_methods_and_no_client_endpoint_actor_reader(self):
        self.assertEqual(self.api.get_status.allowed_methods, ["GET"])
        self.assertEqual(self.api.request_refresh.allowed_methods, ["POST"])
        for field in ("endpoint", "actor", "reader", "params"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                self.api.request_refresh(["vehicles"], **{field: "forged"})
            with self.assertRaises(TypeError):
                self.api.get_status(**{field: "forged"})

    def test_guest_and_operator_denied_before_lookup_or_queue(self):
        for user, roles, error in (("Guest", [], Unsigned), ("staff", ["DMS User"], Denied)):
            self.frappe.session.user = user
            self.frappe.get_roles.return_value = roles
            for call in (self.api.get_status, lambda: self.api.request_refresh(["vehicles"])):
                with self.subTest(user=user), self.assertRaises(error):
                    call()
        self.frappe.get_all.assert_not_called()
        self.manual.request.assert_not_called()
        self.manual.status.assert_not_called()

    def test_admin_roles_use_only_site_primary_authority(self):
        for role in ("DMS Admin", "System Manager"):
            self.frappe.get_roles.return_value = [role]
            result = self.api.request_refresh('["geo", "vehicles", "geo"]')
            self.assertEqual([r["dataset"] for r in result["datasets"]], ["vehicles", "geo"])
            self.manual.request.assert_any_call("PRIMARY-LOCAL", "vehicles", reader="authority",
                                               actor="admin@example.test")
        self.assertEqual(self.frappe.get_all.call_args.kwargs, {
            "filters": {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]},
            "fields": ["name", "enabled"], "limit_page_length": 2})

    def test_status_is_local_only_and_all_datasets(self):
        result = self.api.get_status()
        self.assertTrue(result["configured"])
        self.assertEqual([r["dataset"] for r in result["datasets"]], ["vehicles", "customers", "geo", "company"])
        self.assertEqual(self.manual.status.call_count, 4)
        self.manual.request.assert_not_called()

    def test_each_group_is_independently_selectable_and_all_four_have_fixed_order(self):
        datasets = ["vehicles", "customers", "geo", "company"]
        for dataset in datasets:
            with self.subTest(dataset=dataset):
                self.manual.request.reset_mock()
                result = self.api.request_refresh([dataset])
                self.assertEqual([row["dataset"] for row in result["datasets"]], [dataset])
                self.manual.request.assert_called_once_with("PRIMARY-LOCAL", dataset,
                    reader="authority", actor="admin@example.test")
        self.manual.request.reset_mock()
        result = self.api.request_refresh(json.dumps(list(reversed(datasets))))
        self.assertEqual([row["dataset"] for row in result["datasets"]], datasets)
        self.assertEqual([call.args[1] for call in self.manual.request.call_args_list], datasets)

    def test_unconfigured_status_is_empty_and_request_rejects(self):
        self.rows = []
        self.assertEqual(self.api.get_status(), {"configured": False, "datasets": []})
        with self.assertRaises(Invalid):
            self.api.request_refresh(["geo"])
        self.manual.status.assert_not_called()
        self.manual.request.assert_not_called()

    def test_ambiguous_primary_rejected_even_for_status(self):
        self.rows += [{"name": "OTHER", "enabled": 1}]
        for call in (self.api.get_status, lambda: self.api.request_refresh(["geo"])):
            with self.assertRaises(Invalid):
                call()
        self.manual.status.assert_not_called()
        self.manual.request.assert_not_called()

    def test_disabled_cannot_enqueue(self):
        self.rows[0]["enabled"] = 0
        with self.assertRaises(Invalid):
            self.api.request_refresh(["customers"])
        self.manual.request.assert_not_called()

    def test_invalid_dataset_input_never_queues(self):
        for value in (None, [], {}, "vehicles", '{"endpoint":"x"}', ["sales"],
                      ["masters"], ["directory"], ["query.capabilities"], ["company", "unknown"],
                      ["vehicles", "customers", "geo", "company", "geo"], [1], [["geo"]]):
            with self.subTest(value=value), self.assertRaises(Invalid):
                self.api.request_refresh(value)
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

    def test_repeat_requests_preserve_domain_dedup_identity(self):
        first = self.api.request_refresh(["vehicles"])
        second = self.api.request_refresh(["vehicles"])
        self.assertEqual(first, second)
        self.assertEqual(first["datasets"][0]["request_id"], "ab" * 16)
        self.assertEqual(self.manual.request.call_count, 2)
        # The real domain owns atomic dedup; the RPC must not bypass it or invent jobs.

    def test_failure_in_one_group_does_not_hide_another_accepted_request(self):
        self.manual.request.side_effect = [RuntimeError("private upstream data"),
            {"state": "queued", "request_id": "ab" * 16}]
        result = self.api.request_refresh(["vehicles", "customers"])
        self.assertEqual([r["state"] for r in result["datasets"]], ["failed", "queued"])
        self.assertNotIn("private", json.dumps(result))

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
