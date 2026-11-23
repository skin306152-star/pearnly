"""Zero-I/O checks for the DMS operator roster search (customer request 2026-11-23: nearly 100 staff).

`list_operators(search=...)` filters by display name or DMS username with Frappe's parameterised LIKE,
keeps the native paging (`limit_start` / `limit_page_length`) and counts with the same conditions.
Frappe is replaced by an in-memory table; nothing touches a Site, DMS or LINE.
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

NAMES = [("Somchai Jaidee", "somchai"), ("Somsak Dee", "somsak"), ("Anan Sukjai", "anan01"),
         ("Kanya Rak", "kanya"), ("Preecha Som", "preecha")]


class Doc:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __getattr__(self, name):  # fields the row builder reads but this test does not care about
        return None


class Roster(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(sys.modules)
        guard.start()
        self.addCleanup(guard.stop)
        for name in list(sys.modules):
            if name == "frappe" or name.startswith(("frappe.", "pearnly_core", "pearnly_dms")):
                del sys.modules[name]
        self.rows = [Doc(name=f"OP-{i:03d}", display_name=d, dms_username=u, user=f"{u}@operators.local",
                         dms_role="sales", status="active", creation=i)
                     for i, (d, u) in enumerate(NAMES * 3)]  # 15 rows, each name three times
        self.calls = []
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text, *a, **k: text
        frappe.session = types.SimpleNamespace(user="admin@example.test")
        frappe.get_roles = lambda user=None: ["DMS Admin"]
        frappe.whitelist = lambda *a, **k: (lambda fn: fn)

        def throw(message, exc=None):
            raise (exc or ValueError)(message)

        frappe.throw = throw
        frappe.PermissionError = frappe.AuthenticationError = type("Denied", (Exception,), {})
        frappe.get_all = self.get_all
        frappe.get_doc = lambda doctype, name: next(r for r in self.rows if r.name == name)
        frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None)  # no LINE bindings
        sys.modules["frappe"] = frappe
        endpoints = types.ModuleType("pearnly_core.endpoints")
        endpoints.read_password = lambda *a, **k: ""
        sys.modules["pearnly_core.endpoints"] = endpoints
        operators = importlib.import_module("pearnly_dms.operators")
        for name in ("advisors", "line_account"):
            stub = types.ModuleType(f"pearnly_dms.operators.{name}")
            sys.modules[stub.__name__] = stub
            setattr(operators, name, stub)
        self.roster = importlib.import_module("pearnly_dms.operators.roster")

    def get_all(self, doctype, filters=None, or_filters=None, fields=None, order_by=None,
                limit_start=0, limit_page_length=0, **kwargs):
        self.calls.append({"or_filters": or_filters, "fields": fields, "limit_start": limit_start,
                           "limit_page_length": limit_page_length})
        assert doctype == "DMS Operator" and not filters

        def hit(row):
            if not or_filters:
                return True
            for field, op, pattern in or_filters:
                assert op == "like" and pattern.startswith("%") and pattern.endswith("%")
                if pattern[1:-1].lower() in str(getattr(row, field) or "").lower():
                    return True
            return False

        rows = sorted((r for r in self.rows if hit(r)), key=lambda r: (r.creation, r.name))
        if fields == [{"COUNT": "name", "as": "total"}]:
            return [{"total": len(rows)}]
        end = limit_start + limit_page_length if limit_page_length else None
        return [Doc(name=r.name) for r in rows[limit_start:end]]

    def test_search_returns_only_matching_rows_and_counts_them(self):
        out = self.roster.api_list_operators(0, 20, "  somchai ")
        self.assertEqual({row["display_name"] for row in out["items"]}, {"Somchai Jaidee"})
        self.assertEqual(len(out["items"]), 3)
        self.assertEqual(out["total"], 3)
        # Both list and count are filtered by name or DMS username, with the value as a parameter.
        self.assertEqual(self.calls[0]["or_filters"], [["display_name", "like", "%somchai%"],
                                                       ["dms_username", "like", "%somchai%"]])
        self.assertEqual(self.calls[-1]["or_filters"], self.calls[0]["or_filters"])

    def test_username_matches_too(self):
        out = self.roster.list_operators(0, 20, "anan01")
        self.assertEqual({row["dms_username"] for row in out["items"]}, {"anan01"})

    def test_paging_still_applies_to_the_search(self):
        first = self.roster.list_operators(0, 4, "som")  # Somchai, Somsak, Preecha Som -> 9 rows
        second = self.roster.list_operators(4, 4, "som")
        third = self.roster.list_operators(8, 4, "som")
        self.assertEqual((len(first["items"]), len(second["items"]), len(third["items"])), (4, 4, 1))
        self.assertEqual(first["total"], 9)
        names = [r["name"] for page in (first, second, third) for r in page["items"]]
        self.assertEqual(len(set(names)), 9)
        listed = [c for c in self.calls if c["fields"] == ["name"]]
        self.assertEqual([(c["limit_start"], c["limit_page_length"]) for c in listed], [(0, 4), (4, 4), (8, 4)])

    def test_empty_search_is_the_whole_roster(self):
        out = self.roster.list_operators(0, 20, "   ")
        self.assertEqual((len(out["items"]), out["total"]), (15, 15))
        self.assertIsNone(self.calls[0]["or_filters"])

    def test_bad_paging_is_still_refused(self):
        with self.assertRaises(ValueError):
            self.roster.list_operators(-1, 20, "som")
        with self.assertRaises(ValueError):
            self.roster.list_operators(0, 0, "som")


if __name__ == "__main__":
    unittest.main()
