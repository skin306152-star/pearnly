"""原件保护的加锁查询只锁命中的原件行（三租户压测 2026-10-01：开始对账 24/105 报 1020）。在镜像里跑，不连数据库。

真实数据库上的对照（本机样板，MariaDB 11.8.9、innodb_snapshot_isolation=1）：一个事务先读、另一个事务改一行原件并提交，
再对一个无关的新文件跑加锁查询——旧写法 1020，新写法（带补丁里的两个索引）不报；现有 62 个文件新旧判定完全一致。
"""
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "pearnly_cowork"))
for name in [name for name in sys.modules if name == "pearnly_cowork" or name.startswith("pearnly_cowork.")]:
    del sys.modules[name]

import frappe  # noqa: E402

from pearnly_cowork.files import access  # noqa: E402

assert Path(access.__file__).is_relative_to(ROOT), access.__file__


class FakeDb:
    def __init__(self, files=None, sources=None):
        self.queries = []
        self.files = files or {}      # (column, value) -> [File names]
        self.sources = sources or {}  # (column, value) -> [source names]

    def table_exists(self, name):
        return True

    def get_value(self, *args, **kwargs):
        return None

    def sql(self, query, values=None, pluck=False):
        self.queries.append(query)
        table = "files" if "`tabFile`" in query else "sources"
        column = re.search(r"WHERE `(\w+)` IN", query).group(1)
        found = []
        for value in values["values"]:
            found += getattr(self, table).get((column, value), [])
        return found


class SourceLockScopeTest(unittest.TestCase):
    def run_guard(self, db, doc, include_hash):
        with patch.object(frappe, "db", db, create=True), \
                patch.object(frappe, "get_doc", side_effect=lambda doctype, name: (doctype, name)), \
                patch.object(access, "canonical_storage_url", side_effect=lambda value, *a: value):
            return access.protected_sources(doc, include_hash=include_hash)

    def test_every_lookup_is_an_exact_indexed_match_and_only_sources_are_locked(self):
        db = FakeDb()
        doc = frappe._dict(name="F1", file_url="/private/files/a.pdf", is_private=1, content_hash="h" * 32,
                           attached_to_doctype=access.SOURCE, attached_to_name="S9")
        self.assertEqual(self.run_guard(db, doc, True), [])
        for query in db.queries:
            self.assertNotIn(" OR ", query)
            self.assertNotIn("JOIN", query)
            self.assertEqual(" FOR UPDATE" in query, "`tabCowork Source File`" in query, query)
        columns = [re.search(r"FROM `([^`]+)` WHERE `(\w+)`", query).groups() for query in db.queries]
        self.assertEqual(columns, [("tabFile", "file_url"), ("tabFile", "content_hash"),
                                   ("tabCowork Source File", "stored_file"), ("tabCowork Source File", "storage_url"),
                                   ("tabCowork Source File", "name"), ("tabCowork Source File", "native_content_hash")])

    def test_reads_do_not_lock_and_skip_the_hash(self):
        db = FakeDb()
        doc = frappe._dict(name="F1", file_url="/private/files/a.pdf", content_hash="h" * 32)
        self.run_guard(db, doc, False)
        self.assertFalse(any("FOR UPDATE" in query or "content_hash" in query for query in db.queries))

    def test_same_protection_as_before(self):
        """存的那份、别名 URL、同内容（任一侧的哈希）、挂在原件上的，都还认得出来；无关的文件不算。"""
        db = FakeDb(files={("file_url", "/private/files/alias.pdf"): ["F-STORED"],
                           ("content_hash", "same"): ["F-OTHER-COPY"]},
                    sources={("stored_file", "F-STORED"): ["S1"], ("stored_file", "F-OTHER-COPY"): ["S2"],
                             ("storage_url", "gs://bucket/o#1"): ["S3"], ("name", "S4"): ["S4"],
                             ("native_content_hash", "same"): ["S5"], ("stored_file", "F-SELF"): ["S6"]})
        cases = [
            (frappe._dict(file_url="/private/files/alias.pdf"), True, ["S1"]),
            (frappe._dict(file_url="/private/files/new.pdf", content_hash="same"), True, ["S2", "S5"]),
            (frappe._dict(file_url="/private/files/new.pdf", content_hash="same"), False, []),
            (frappe._dict(file_url="gs://bucket/o#1"), False, ["S3"]),
            (frappe._dict(attached_to_doctype=access.SOURCE, attached_to_name="S4"), False, ["S4"]),
            (frappe._dict(name="F-SELF"), False, ["S6"]),
            (frappe._dict(file_url="/private/files/unrelated.pdf", content_hash="other"), True, []),
        ]
        for doc, include_hash, expected in cases:
            with self.subTest(doc=doc, include_hash=include_hash):
                found = self.run_guard(db, doc, include_hash)
                self.assertEqual(found, [(access.SOURCE, name) for name in expected])

    def test_nothing_to_match_queries_nothing(self):
        db = FakeDb()
        self.assertEqual(self.run_guard(db, frappe._dict(), True), [])
        self.assertEqual(db.queries, [])

    def test_patch_registers_the_indexes(self):
        patches = (ROOT / "apps/pearnly_cowork/pearnly_cowork/patches.txt").read_text()
        self.assertIn("pearnly_cowork.patches.v0_0_10_source_lookup_indexes", patches)
        from pearnly_cowork.patches import v0_0_10_source_lookup_indexes as patch_module
        calls = []
        fake = type("Db", (), {
            "add_index": lambda self, *a: calls.append(("add_index",) + a),
            "has_index": lambda self, *a: False,
            "commit": lambda self: None,
            "sql_ddl": lambda self, query: calls.append(("ddl", query)),
        })()
        with patch.object(frappe, "db", fake, create=True), \
                patch.object(patch_module, "make_property_setter",
                             side_effect=lambda *a, **k: calls.append(("setter",) + a)):
            patch_module.execute()
        self.assertEqual(calls[0], ("setter", "File", "content_hash", "search_index", "1", "Check"))
        self.assertEqual(calls[1], ("add_index", "File", ["content_hash"]))
        self.assertIn("`storage_url_prefix` (`storage_url`(255))", calls[2][1])


class ReconStartRetryTest(unittest.TestCase):
    def test_start_retries_a_snapshot_conflict_like_the_upload(self):
        from pearnly_cowork.recon_center import api

        attempts = []

        def start(*args):
            attempts.append(args)
            if len(attempts) < 3:
                raise frappe.QueryDeadlockError("1020")
            return {"job": "J"}

        rollbacks = []
        fake_db = type("Db", (), {"rollback": lambda self: rollbacks.append(1)})()
        with patch.object(api.jobs, "start", side_effect=start), \
                patch.object(frappe, "db", fake_db, create=True), \
                patch.object(frappe.local, "request", None, create=True):
            self.assertEqual(api.start("bank"), {"job": "J"})
        self.assertEqual((len(attempts), len(rollbacks)), (3, 2))
        attempts.clear()
        with patch.object(api.jobs, "start", side_effect=frappe.QueryDeadlockError("1020")), \
                patch.object(frappe, "db", fake_db, create=True), \
                patch.object(frappe.local, "request", None, create=True):
            with self.assertRaises(frappe.QueryDeadlockError):
                api.start("bank")


if __name__ == "__main__":
    unittest.main()
