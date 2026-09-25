"""Snapshot storage codec: compression round-trip, legacy rows, bound semantics.

2026-09-25 lesson: the rayong tenant's 15,135-customer snapshot was 22MB in one
row; `mariadb-dump` dropped the connection while backing it up and blocked two
releases, and reading it needed 800MB+ of memory. Large fields are compressed
now, but rows written before the change must still read.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))
sys.path.insert(0, str(ROOT / "apps"))

# `store` 模块顶层 `import frappe`，还牵到 pearnly_core 的凭据/锁工具。这里只测
# 编解码两个纯函数，所以给它们最小替身 —— 与 test_dms_connection_snapshots 同一手法。
fake_frappe = types.ModuleType("frappe")
fake_frappe._ = lambda text: text
fake_frappe.session = types.SimpleNamespace(user="admin@example.test")
fake_frappe.db = Mock()
fake_frappe.cache = Mock()
fake_frappe.conf = Mock()
fake_frappe.throw = Mock()
fake_frappe.PermissionError = PermissionError
fake_frappe.ValidationError = ValueError

core_endpoints = types.ModuleType("pearnly_core.endpoints")
core_endpoints.read_password = lambda *a, **k: ""
core_endpoints._quiet_decrypt = lambda: __import__("contextlib").nullcontext()
core_lock = types.ModuleType("pearnly_core.session_lock")
core_lock.dms_login_key = lambda url, user: f"{url}|{user}"
core_lock.account_lock = Mock()
core_lock.SessionLockUnavailable = RuntimeError
core = types.ModuleType("pearnly_core")

with patch.dict(sys.modules, {
    "frappe": fake_frappe,
    "pearnly_core": core,
    "pearnly_core.endpoints": core_endpoints,
    "pearnly_core.session_lock": core_lock,
}):
    from pearnly_dms.contracts.client_base import DmsClientError
    from pearnly_dms.snapshots import store


class SnapshotStorageTests(unittest.TestCase):
    def test_round_trip_is_lossless(self):
        data = {"customers": {str(i): {"fields": {"txtname": "customer %d" % i}} for i in range(500)}}
        stored = store._dump_big(data)
        self.assertTrue(stored.startswith(store._GZ), "large fields carry the compression marker")
        self.assertEqual(store._load_big(stored), data)

    def test_compression_actually_shrinks_a_large_snapshot(self):
        data = {"customers": {str(i): {"fields": {"id": "1103702587460", "name": "synthetic name",
                                                  "addr": "250/75 synthetic road, bangkok"}}
                              for i in range(2000)}}
        raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        stored = store._dump_big(data)
        self.assertLess(len(stored), len(raw.encode("utf-8")) / 2,
                        "without real shrinkage a backup is still dragged down by one huge row")

    def test_rows_written_before_the_change_still_read(self):
        plain = json.dumps({"customers": {"1": {"fields": {}}}}, ensure_ascii=False)
        self.assertFalse(plain.startswith(store._GZ))
        self.assertEqual(store._load_big(plain), {"customers": {"1": {"fields": {}}}})
        self.assertIsNone(store._load_big(""))
        self.assertIsNone(store._load_big(None))

    def test_bound_still_applies_to_the_uncompressed_size(self):
        # The bound is about how much data we accept, not how many bytes it lands
        # in: compressing must not quietly widen what a tenant may store.
        huge = {"blob": "x" * (store.MAX_BYTES + 1)}
        with self.assertRaises(DmsClientError) as raised:
            store._dump_big(huge)
        self.assertEqual(raised.exception.error_code, "ERR_DMS_SNAPSHOT_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
