"""Zero-I/O checks: a read account must see every DMS customer (2026-10-06).

DMS filters its customer list by account permission. On the test DMS at the same moment the
read account saw 39 customers, the write accounts 44 and a salesperson 16, so the daytime
sync and the nightly full read silently missed customers. Registration now counts both sides.
Frappe, the session and the DMS are stand-ins; nothing logs in.
"""
from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))


def _stub_frappe():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text, *args: text
    frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
    frappe.db = types.SimpleNamespace(get_value=lambda *args, **kwargs: "admin-user")
    endpoints = types.ModuleType("pearnly_core.endpoints")
    endpoints.read_password = lambda *args, **kwargs: "admin-secret"
    core = types.ModuleType("pearnly_core")
    core.endpoints = endpoints
    return {"frappe": frappe, "pearnly_core": core, "pearnly_core.endpoints": endpoints}


class Account:
    def __init__(self, username, password="pw"):
        self.username, self.password = username, password


class ReadScopeTests(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules, _stub_frappe())
        guard.start()
        self.addCleanup(guard.stop)
        for name in [n for n in sys.modules if n.startswith("pearnly_dms")]:
            sys.modules.pop(name)
        from pearnly_dms.connections import api
        from pearnly_dms.adapter import account_probe, admin_pool
        from pearnly_dms import i18n
        self.api, self.probe, self.pool, self.i18n = api, account_probe, admin_pool, i18n
        self.enterContext(patch.object(api, "_endpoint_name", return_value="EP-1"))
        self.text = self.enterContext(patch.object(
            i18n, "t", side_effect=lambda key, **kw: f"{key} {kw['seen']}/{kw['total']} {kw['reference']}"))

    def gap(self, counts, references):
        self.enterContext(patch.object(self.pool, "members", return_value=references))
        self.enterContext(patch.object(self.probe, "customer_count",
                                       side_effect=lambda url, user, pw: counts[user]))
        return self.api._read_scope_gap("https://dms.invalid/dms/index.php", "dmstest", "pw")

    def test_read_account_that_sees_fewer_customers_is_refused_with_both_counts(self):
        message = self.gap({"dmstest": 39, "dmstest001": 44}, [Account("dmstest001")])
        self.assertEqual(message, "dms.connect.read.scope 39/44 dmstest001")

    def test_read_account_that_sees_everything_passes(self):
        self.assertEqual(self.gap({"dmstest": 44, "dmstest001": 44}, [Account("dmstest001")]), "")

    def test_no_reference_account_never_blocks(self):
        self.assertEqual(self.gap({"dmstest": 39}, []), "")

    def test_unreadable_count_never_blocks(self):
        self.assertEqual(self.gap({"dmstest": 39, "dmstest001": None}, [Account("dmstest001")]), "")


if __name__ == "__main__":
    unittest.main()
