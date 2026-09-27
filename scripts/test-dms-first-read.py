"""First full read without the long wait (user 2026-09-27); pinned Frappe runtime, no Site/IO.

Customer index published after the list phase, Directory answering from it, the one-customer
fill, the password-free cache scope and its one-time re-key. Run: docker run ... <image>
/candidate/scripts/test-dms-first-read.py
"""
import unittest
from unittest.mock import Mock, patch

import frappe

from pearnly_dms.adapter import snapshot_sync, snapshot_writeback
from pearnly_dms.client import snapshot_reader
from pearnly_dms.snapshots import store
from pearnly_dms.snapshots.directory import CustomerDetailPending, Directory

ID1, ID2 = "1103702587460", "3100100123456"
FULL = {"customer_id": "7", "name": "A", "people_id": ID1, "cuscode": "C7", "phone": "081"}


def snapshot(data):
    return {"data": data, "as_of": 1.0, "completed_at": 2.0, "generation": "g", "stale": False}


class IndexModeTests(unittest.TestCase):
    def directory(self, *, full=None, index=None, overlays=None):
        def read(endpoint, dataset, **kwargs):
            source = {"customers": full, "customer_index": index}.get(dataset)
            if source is None:
                raise store.SnapshotUnavailable()
            return snapshot(source)
        self.enterContext(patch.object(store, "read", side_effect=read))
        self.enterContext(patch.object(store, "customer_overlays", return_value=overlays or {}))
        return Directory("EP-1")

    INDEX = {"index": {"7": {"cuscode": "C7", "name": "A", "people_id": ID1},
                       "8": {"cuscode": "C8", "name": "B", "people_id": ID2}},
             "prefixes": [["1", "Mr"]]}

    def test_full_customers_win_over_the_index(self):
        view = self.directory(full={"customers": {"7": FULL}, "prefixes": [["1", "Mr"]]}, index=self.INDEX)
        self.assertEqual(view.lookup_customer(ID1)["fields"]["phone"], "081")
        self.assertFalse(view.lookup_customer(ID2)["found"])

    def test_index_answers_new_customers_and_prefixes_before_details(self):
        view = self.directory(index=self.INDEX)
        self.assertEqual(view.lookup_customer("1999999999990"), {"found": False, "customer_id": None, "fields": {}})
        self.assertEqual(view.list_prefixes(), [["1", "Mr"]])
        self.assertEqual([row["customer_id"] for row in view.search_customers_detailed("B")], ["8"])

    def test_known_customer_without_details_asks_for_a_fill(self):
        view = self.directory(index=self.INDEX)
        with self.assertRaises(CustomerDetailPending) as pending:
            view.lookup_customer(ID1)
        self.assertEqual(pending.exception.customer_id, "7")
        self.assertEqual(pending.exception.error_code, "ERR_DMS_SNAPSHOT_PENDING")

    def test_verified_write_back_supplies_the_details(self):
        view = self.directory(index=self.INDEX, overlays={"7": FULL})
        self.assertEqual(view.lookup_customer(ID1)["fields"], FULL)
        self.assertEqual(view.read_customer("7"), FULL)

    def test_no_index_keeps_the_original_pending_error(self):
        view = self.directory()
        with self.assertRaises(store.SnapshotUnavailable):
            view.lookup_customer(ID1)


class FillTests(unittest.TestCase):
    def test_pending_customer_is_filled_once_then_read_again(self):
        calls = []

        def read():
            calls.append(1)
            if len(calls) == 1:
                raise CustomerDetailPending("7")
            return "ok"

        with patch.object(snapshot_writeback, "fill_customer", return_value=True) as fill:
            self.assertEqual(snapshot_writeback.with_customer_detail("EP-1", read), "ok")
        fill.assert_called_once_with("EP-1", "7")

    def test_failed_fill_keeps_the_pending_error(self):
        def read():
            raise CustomerDetailPending("7")

        with patch.object(snapshot_writeback, "fill_customer", return_value=False):
            with self.assertRaises(CustomerDetailPending):
                snapshot_writeback.with_customer_detail("EP-1", read)


class IndexCaptureTests(unittest.TestCase):
    def test_page_rows_become_the_index_only_when_they_match_the_ids(self):
        client = Mock()
        client._parse_customer_rows.return_value = [
            {"customer_id": "7", "cuscode": "C7", "name": "A", "people_id": ID1}]
        self.assertEqual(snapshot_reader._index_rows(client, "body", ["7"]),
                         {"7": {"cuscode": "C7", "name": "A", "people_id": ID1}})
        self.assertIsNone(snapshot_reader._index_rows(client, "body", ["7", "8"]))
        client._parse_customer_rows.side_effect = ValueError("odd html")
        self.assertIsNone(snapshot_reader._index_rows(client, "body", ["7"]))

    def test_index_is_published_only_after_a_complete_list(self):
        rows = {"7": {"cuscode": "C7", "name": "A", "people_id": ID1}}
        tree = {"phase": "details", "count": 1, "ids": ["7"], "index": rows}
        self.assertEqual(snapshot_sync._ready_index(tree, [["1", "Mr"]]),
                         {"index": rows, "prefixes": [["1", "Mr"]]})
        self.assertIsNone(snapshot_sync._ready_index({**tree, "phase": "list"}, [["1", "Mr"]]))
        self.assertIsNone(snapshot_sync._ready_index({**tree, "index": None}, [["1", "Mr"]]))
        self.assertIsNone(snapshot_sync._ready_index({**tree, "count": 2}, [["1", "Mr"]]))
        self.assertIsNone(snapshot_sync._ready_index(tree, []))


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(frappe.local, "site", "dms.test", create=True))

    def test_the_cache_key_ignores_the_password(self):
        url = "https://dms.example.test/dms/index.php"
        plain = store.scope_identity(url, "boss", "authority")
        self.assertNotIn("secret", plain)
        self.assertEqual(plain, store.scope_identity(url, "boss", "authority"))
        self.assertNotEqual(store._scope_hash(plain), store._scope_hash(store.scope_identity(url, "other", "authority")))
        self.assertEqual(store.scope_identity(url, "boss", "authority", password="secret")[-1], "secret")

    def test_rekey_moves_rows_to_the_password_free_key_once(self):
        from pearnly_dms.snapshots import scope_rekey

        row = frappe._dict(name="EP-1", system_url="https://dms.example.test/dms/index.php",
                           username="sale", admin_username="boss", owner_user=None)
        with patch.object(scope_rekey, "read_password", return_value="pw"):
            [(old, new)] = scope_rekey._keys(row, "authority")
        self.assertEqual(new, store._scope_hash(store.scope_identity(row.system_url, "boss", "authority")))
        self.assertEqual(old, store._scope_hash(store.scope_identity(row.system_url, "boss", "authority",
                                                                     password="pw")))
        db = Mock()
        db.exists.side_effect = lambda dt, name: name == store._name(new, "geo")
        rows = [frappe._dict(name="OLD-C", dataset="customers"), frappe._dict(name="OLD-G", dataset="geo")]
        with patch.object(frappe, "get_all", return_value=rows), patch.object(frappe, "db", db):
            self.assertEqual(scope_rekey._rekey(old, new), 1, "an existing new-key row is kept, not overwritten")
        self.assertEqual(db.sql.call_args.args[1], (store._name(new, "customers"), new, "OLD-C"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
