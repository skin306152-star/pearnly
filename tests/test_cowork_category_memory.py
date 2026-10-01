#!/usr/bin/env python3
"""Category memory: same seller + same item first, then same seller (user 2026-09-30).

Runs inside the product image (needs Frappe importable); the database is an in-memory dict.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps/pearnly_cowork"), str(ROOT / "apps/pearnly_core")]

from pearnly_cowork.history import categories  # noqa: E402

WS = "WS-1"


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Store:
    def __init__(self):
        self.rows = {}

    def get_value(self, doctype, name, field, for_update=False):
        return name if name in self.rows else None

    def sql(self, query, values):
        row = self.rows.get(values[0])
        return [(row["category"],)] if row else []

    def get_doc(self, doctype_or_body, name=None, for_update=False):
        if isinstance(doctype_or_body, dict):
            return Row(doctype_or_body)
        return self.rows[name]

    def persist(self, row):
        self.rows[row["record_key"]] = row
        return row


def invoice(seller, category="", *items):
    return [{"fields": {"seller_name": seller, "category": category,
                        "items": [{"name": name} for name in items]}}]


class CategoryMemory(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        for target, value in (("scope.require", None), ("scope.require_scope", None), ("_lock", None)):
            p = patch.object(categories.scope if target.startswith("scope.") else categories,
                             target.split(".")[-1], lambda *a, **k: value)
            p.start(); self.addCleanup(p.stop)
        for target, value in (("_persist", self.store.persist),):
            p = patch.object(categories, target, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(categories.frappe, "get_doc", self.store.get_doc); p.start(); self.addCleanup(p.stop)
        p = patch.object(categories.frappe, "db", self.store); p.start(); self.addCleanup(p.stop)
        # Savepoint wrapper and the tree are not what is under test.
        p = patch.object(categories, "_optional", lambda fn, fallback, label: fn()); p.start(); self.addCleanup(p.stop)

    def recall(self, pages):
        fields = categories._primary(pages)
        return categories._memory(WS, None, fields.get("seller_name"), categories._item(fields))

    def test_learning_writes_item_and_seller_rows(self):
        categories.learn(WS, None, invoice("Sincere Ice", "Food/Drink", "น้ำแข็งหลอดเล็ก"))
        items = sorted((row["item_name"], row["category"]) for row in self.store.rows.values())
        self.assertEqual(items, [("", "Food/Drink"), ("น้ำแข็งหลอดเล็ก", "Food/Drink")])

    def test_same_item_wins_over_the_seller_wide_choice(self):
        categories.learn(WS, None, invoice("Sincere Ice", "Food/Drink", "น้ำแข็งหลอดเล็ก"))
        categories.learn(WS, None, invoice("Sincere Ice", "Packaging", "ถุงพลาสติก"))
        self.assertEqual(self.recall(invoice("Sincere Ice", "", " น้ำแข็ง หลอดเล็ก ")), "Food/Drink")
        self.assertEqual(self.recall(invoice("Sincere Ice", "", "ถุงพลาสติก")), "Packaging")
        # An item never seen falls back to the seller's latest choice.
        self.assertEqual(self.recall(invoice("Sincere Ice", "", "ของใหม่")), "Packaging")

    def test_no_seller_means_no_memory(self):
        categories.learn(WS, None, invoice("", "Food/Drink", "น้ำแข็ง"))
        self.assertEqual(self.store.rows, {})
        self.assertIsNone(self.recall(invoice("", "", "น้ำแข็ง")))



class SeedLock(unittest.TestCase):
    """存识别结果时不锁工作空间（2026-10-01 生产压测：同一家的识别结果被这把锁排成一队）。"""

    def run_seed(self, tree_exists):
        calls = []

        class Db:
            def exists(self, doctype, filters):
                calls.append(("exists", doctype))
                return tree_exists

            def get_value(self, doctype, name, field, for_update=False):
                calls.append(("get_value", doctype, for_update))
                return 1 if doctype == "Cowork Workspace" else "existing-root"

        with patch.object(categories.frappe, "db", Db()):
            categories._seed(WS)
        return calls

    def test_an_existing_tree_locks_nothing(self):
        self.assertEqual(self.run_seed(True), [("exists", categories.CATEGORY)])

    def test_the_first_seed_still_locks_and_rechecks(self):
        calls = self.run_seed(False)
        self.assertIn(("get_value", "Cowork Workspace", True), calls)
        self.assertIn(("get_value", categories.CATEGORY, True), calls)

if __name__ == "__main__":
    unittest.main()
