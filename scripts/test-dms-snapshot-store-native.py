"""Real native snapshot storage tests, only in the task-owned internal lab.

Run in dms-snapshot-lab.yml: env/bin/python
/workspace/scripts/test-dms-snapshot-store-native.py. No external transport is
allowed. MariaDB transactions, encrypted Endpoint credentials, native document
permissions and Redis are real. A second Python/DB connection observes committed
state, not a mocked store. No scheduler/worker is started.

UUID-owned snapshots are removed precisely; synthetic identities are disabled
and retained for audit until the dedicated lab is removed. The shared synthetic
template is removed, allowing repeat runs. No business acceptance is claimed.
"""
import json
import os
from pathlib import Path
import runpy
import select
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

import frappe
import requests

from pearnly_dms.snapshots import store

SITE = "dms-snapshot-lab.localhost"
BENCH = Path("/home/frappe/frappe-bench")
HTTP_ATTEMPTS = []


def deny_http(*args, **kwargs):
    HTTP_ATTEMPTS.append(True)
    raise AssertionError("External HTTP is forbidden in the snapshot lab")


def connect():
    if os.environ.get("PEARNLY_DMS_SNAPSHOT_LAB") != "1":
        raise RuntimeError("Requires the task-owned snapshot lab")
    if Path.cwd() not in (BENCH, BENCH / "sites"):
        raise RuntimeError("Refusing a foreign working directory")
    os.chdir(BENCH / "sites")
    if not Path(".dms-snapshot-lab").is_file():
        raise RuntimeError("Refusing an unmarked Site volume")
    frappe.init(site=SITE, force=True)
    frappe.connect()
    if not (frappe.conf.get("pearnly_dms_snapshot_lab")
            and frappe.conf.get("db_host") == "db"
            and frappe.conf.get("pearnly_line_channel_access_token") == "lab:" + SITE
            and frappe.conf.get("pause_scheduler")):
        raise RuntimeError("Refusing a Site outside the dedicated snapshot lab")
    for field in ("redis_cache", "redis_queue"):
        url = urlsplit(frappe.conf.get(field) or "")
        if url.hostname != "redis" or url.password or url.username:
            raise RuntimeError("Refusing non-lab Redis")
    frappe.set_user("Administrator")


def directory(label="old"):
    return {
        "masters": {"cars": [{"id": "car-1", "text": label}]},
        "paints": {"car-1": [{"id": "paint-1", "text": label}]},
        "customers": {"customer-1": {"customer_id": "customer-1", "name": label}},
    }


def geo(label="old"):
    return {"provinces": {"": [["P1", label]]}, "districts": {"P1": [["D1", label]]},
            "subdistricts": {"D1": [["S1", label]]}, "zipcodes": {"S1": [["Z1", "10000"]]}}


class SnapshotLabCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        connect()
        cls.http_guard = patch.object(requests.sessions.Session, "request", deny_http)
        cls.http_guard.start()
        cls.addClassCleanup(cls.http_guard.stop)
        # Frappe's native test flag runs its User contact hook inline rather than
        # leaving unrelated create_contact jobs in this daemon-free lab queue.
        cls.old_in_test, frappe.in_test = frappe.in_test, True
        cls.uid = "snapshot-" + uuid.uuid4().hex
        cls.password = "fake-only-" + cls.uid
        cls.users, cls.endpoints, cls.scope_keys = [], [], set()
        cls.template = None
        cls.addClassCleanup(cls.cleanup)
        if frappe.db.exists("Pearnly ERP Endpoint", {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]}):
            raise RuntimeError("A shared Endpoint already exists; refusing to alter another fixture")
        template = frappe.get_doc({"doctype": "Pearnly ERP Endpoint", "label": cls.uid,
            "adapter": "mrerp_dms", "enabled": 1, "system_url": "https://snapshot.invalid/dms/",
            "admin_username": cls.uid, "admin_password": cls.password,
            "booking_defaults": "{}"}).insert(ignore_permissions=True)
        cls.template = template.name
        for suffix in ("a", "b"):
            user = cls.uid + "-" + suffix + "@example.invalid"
            frappe.get_doc({"doctype": "User", "email": user, "first_name": cls.uid,
                "enabled": 1, "send_welcome_email": 0}).insert(ignore_permissions=True)
            cls.users.append(user)
            # Identical sales credentials deliberately exercise owner/Endpoint
            # isolation independently of the upstream login name.
            frappe.get_doc({"doctype": "DMS Operator", "user": user, "display_name": cls.uid,
                "status": "active", "dms_role": "sales", "dms_username": cls.uid + "-sales",
                "dms_password": cls.password, "can_query_dms": 1}).insert(ignore_permissions=True)
            cls.endpoints.append(frappe.db.get_value("Pearnly ERP Endpoint", {"owner_user": user}, "name"))
        frappe.db.commit()
        if not frappe.cache.ping():
            raise RuntimeError("Real lab Redis is unavailable")

    @classmethod
    def cleanup(cls):
        frappe.db.rollback()
        frappe.set_user("Administrator")
        for key in cls.scope_keys:
            frappe.db.delete(store.DT, {"scope_key": key})
        for endpoint in cls.endpoints:
            frappe.db.set_value("Pearnly ERP Endpoint", endpoint, "enabled", 0)
        for user in cls.users:
            if frappe.db.exists("DMS Operator", user):
                frappe.db.set_value("DMS Operator", user, "status", "inactive")
            frappe.db.set_value("User", user, "enabled", 0)
        if cls.template:
            frappe.delete_doc("Pearnly ERP Endpoint", cls.template, ignore_permissions=True)
        frappe.db.commit()
        frappe.in_test = cls.old_in_test
        frappe.destroy()
        if HTTP_ATTEMPTS:
            raise AssertionError("An HTTP attempt was swallowed")

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.flags.dms_snapshot_status = None
        self.endpoint = self.endpoints[0]

    def tearDown(self):
        frappe.db.rollback()
        frappe.set_user("Administrator")
        for key in self.scope_keys:
            frappe.db.delete(store.DT, {"scope_key": key})
        for user, endpoint in zip(self.users, self.endpoints):
            frappe.db.set_value("User", user, "enabled", 1)
            frappe.db.set_value("DMS Operator", user, {"status": "active", "can_query_dms": 1})
            doc = frappe.get_doc("Pearnly ERP Endpoint", endpoint)
            doc.enabled, doc.admin_password = 1, self.password
            doc.save(ignore_permissions=True)
        frappe.db.commit()
        self.assertEqual(HTTP_ATTEMPTS, [])

    def key(self, endpoint=None, reader="authority"):
        key = store.scope(endpoint or self.endpoint, reader=reader)
        self.scope_keys.add(key)
        return key

    def publish(self, data=None, *, start=None, endpoint=None, dataset="customers", reader="authority"):
        endpoint = endpoint or self.endpoint
        return store.publish(endpoint, dataset, directory() if data is None else data,
            time.time() - 1 if start is None else start, reader=reader,
            expected_scope=self.key(endpoint, reader))

    def unavailable(self, code, fn, *args, **kwargs):
        with self.assertRaises(store.SnapshotUnavailable) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.error_code, code)

    def observe(self, *, dataset="customers", reader="authority"):
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--observe",
            self.endpoint, reader, dataset], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr[-1500:])
        return json.loads(result.stdout)


class NativeSnapshotStore(SnapshotLabCase):
    def test_new_master_groups_require_raw_catalogues_and_publish_independently(self):
        for dataset, extra in (("vehicles", {"paints": {}}), ("company", {})):
            payload = {"masters": {}, "catalogues": {"fixture": {}}, **extra}
            self.publish(payload, dataset=dataset)
            frappe.db.commit()
            before = self.observe(dataset=dataset)
            self.assertEqual(before["data"], payload)
            self.unavailable("ERR_DMS_SNAPSHOT_INCOMPLETE", self.publish,
                             {"masters": {}, **extra}, dataset=dataset)
            frappe.db.rollback()
            self.assertEqual(self.observe(dataset=dataset), before)

    def test_full_geo_catalogues_preserved_without_weakening_tree_validation(self):
        payload = {**geo(), "catalogues": {"provinces": {
            "P1": {"fields": {"txtprovincesengname": "Synthetic English"}}}}}
        self.publish(payload, dataset="geo")
        frappe.db.commit()
        before = self.observe(dataset="geo")
        self.assertEqual(before["data"], payload)
        invalid = {**payload, "districts": {"UNKNOWN": [["D1", "bad parent"]]}}
        self.unavailable("ERR_DMS_SNAPSHOT_INCOMPLETE", self.publish, invalid, dataset="geo")
        frappe.db.rollback()
        self.assertEqual(self.observe(dataset="geo"), before)

    def test_cold_and_failed_cold_are_not_successful_empty(self):
        self.key()
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "customers")
        store.failed(self.endpoint, "customers", "ERR_SYNTHETIC_FAILURE")
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "customers")
        empty = {name: {} for name in ("masters", "paints", "customers")}
        self.assertTrue(self.publish(empty))
        result = store.read(self.endpoint, "customers")
        self.assertEqual(result["data"], empty)
        self.assertTrue(result["generation"])
        self.assertFalse(result["stale"])

    def test_full_bundle_commit_is_atomic_to_another_native_connection(self):
        self.publish(directory("old"))
        frappe.db.commit()
        old = self.observe()
        self.publish(directory("new"), start=time.time())
        self.assertEqual(store.read(self.endpoint, "customers")["data"], directory("new"))
        self.assertEqual(self.observe(), old, "Uncommitted partial/new data leaked")
        frappe.db.commit()
        new = self.observe()
        self.assertEqual(new["data"], directory("new"))
        self.assertNotEqual(new["generation"], old["generation"])

    def test_first_publish_and_rollback_do_not_implicitly_commit(self):
        self.publish()
        self.assertEqual(self.observe(), {"unavailable": "ERR_DMS_SNAPSHOT_PENDING"})
        frappe.db.rollback()
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "customers")
        self.publish()
        frappe.db.commit()
        old = self.observe()
        self.publish(directory("rolled-back"), start=time.time())
        frappe.db.rollback()
        self.assertEqual(self.observe(), old)

    def test_sync_failure_preserves_payload_generation_and_timestamps(self):
        self.publish()
        frappe.db.commit()
        before = store.read(self.endpoint, "customers")
        store.failed(self.endpoint, "customers", "private upstream body must not persist")
        after = store.read(self.endpoint, "customers")
        for field in ("data", "generation", "as_of", "completed_at"):
            self.assertEqual(after[field], before[field])
        self.assertTrue(after["stale"])
        row = frappe.db.get_value(store.DT, store._name(self.key(), "customers"), "last_error")
        self.assertEqual(row, "ERR_DMS_SNAPSHOT_INCOMPLETE")

    def test_partial_bundle_rejected_without_replacing_success(self):
        self.publish()
        frappe.db.commit()
        before = store.read(self.endpoint, "customers")
        incomplete = directory("partial")
        del incomplete["customers"]
        self.unavailable("ERR_DMS_SNAPSHOT_INCOMPLETE", self.publish, incomplete, start=time.time())
        self.assertEqual(store.read(self.endpoint, "customers"), before)

    def test_expiry_uses_collection_start_not_recent_completion(self):
        self.publish(start=time.time() - 301)
        self.assertTrue(store.read(self.endpoint, "customers")["stale"])
        self.assertLess(time.time() - store.read(self.endpoint, "customers")["completed_at"], 5)
        frappe.db.rollback()
        self.publish(start=time.time() - 1900)
        self.assertTrue(store.read(self.endpoint, "customers")["stale"])
        self.unavailable("ERR_DMS_SNAPSHOT_STALE", store.read, self.endpoint, "customers", max_age=1800)

    def test_late_older_collector_cannot_replace_newer_generation(self):
        started = time.time() - 1
        self.publish(directory("new"), start=started)
        before = store.read(self.endpoint, "customers")
        self.assertFalse(self.publish(directory("late-old"), start=started - 60))
        self.assertEqual(store.read(self.endpoint, "customers"), before)

    def test_verified_write_wins_over_inflight_scan_without_refreshing_age(self):
        self.publish(start=time.time() - 100)
        frappe.db.commit()
        before = store.read(self.endpoint, "customers")
        scan_started = time.time() - 1
        fields = {"name": "verified synthetic customer", "people_id": "TEST-NOT-A-PERSON"}
        store.verified_customer(self.endpoint, "customer-1", fields)
        # Write-through owns a separate native cache transaction; the caller's
        # preexisting RR snapshot is not refreshed or committed by the helper.
        frappe.db.rollback()
        verified = store.read(self.endpoint, "customers")
        self.assertEqual(verified["as_of"], before["as_of"])
        self.assertEqual(verified["completed_at"], before["completed_at"])
        self.assertNotEqual(verified["generation"], before["generation"])
        expected = {**fields, "customer_id": "customer-1"}
        self.assertEqual(verified["data"]["customers"]["customer-1"], expected)
        self.publish(directory("older-inflight-data"), start=scan_started)
        self.assertEqual(store.read(self.endpoint, "customers")["data"]["customers"]["customer-1"], expected)
        self.publish(directory("later-authoritative-scan"), start=time.time())
        self.assertEqual(store.read(self.endpoint, "customers")["data"], directory("later-authoritative-scan"))

    def test_verified_write_without_full_directory_is_still_cold(self):
        self.key()
        started = time.time() - 1
        store.verified_customer(self.endpoint, "customer-2", {"name": "synthetic new"})
        frappe.db.rollback()
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "customers")
        self.publish(start=started)
        self.assertEqual(store.read(self.endpoint, "customers")["data"]["customers"]["customer-2"]["name"], "synthetic new")

    def test_verified_customer_hidden_from_newer_search_survives_until_fresh_form_read(self):
        self.publish()
        frappe.db.commit()
        store.verified_customer(self.endpoint, "just-created", {"name": "verified synthetic"})
        frappe.db.rollback()
        before = store.read(self.endpoint, "customers")
        self.unavailable("ERR_DMS_SNAPSHOT_INCOMPLETE", self.publish,
                         directory("newer-list-still-omits-id"), start=time.time())
        self.assertEqual(store.read(self.endpoint, "customers"), before, "A missing fresh form must not advance freshness")
        self.assertEqual(store.read(self.endpoint, "customers")["data"]["customers"]["just-created"]["name"], "verified synthetic")
        self.assertIn("just-created", store.overlay_customer_ids(self.endpoint))
        reread = directory("newer-full-form")
        reread["customers"]["just-created"] = {"name": "freshly reread", "customer_id": "just-created"}
        self.publish(reread, start=time.time())
        self.assertEqual(store.read(self.endpoint, "customers")["data"]["customers"]["just-created"]["name"], "freshly reread")
        self.assertIn("just-created", store.overlay_customer_ids(self.endpoint),
                      "Future scans must keep rereading the confirmed ID even if search omits it")

    def test_invalid_source_time_cannot_claim_a_fresh_generation(self):
        self.publish()
        before = store.read(self.endpoint, "customers")
        for value in (0, -1, float("nan"), float("inf"), time.time() + 3600):
            with self.subTest(source=value):
                self.unavailable("ERR_DMS_SNAPSHOT_INCOMPLETE", self.publish, start=value)
                self.assertEqual(store.read(self.endpoint, "customers"), before)

    def test_credential_rotation_rejects_inflight_and_hides_old_scope(self):
        old_key = self.key()
        self.publish()
        frappe.db.commit()
        old_name = store._name(old_key, "customers")
        old_payload = frappe.db.get_value(store.DT, old_name, "payload")
        endpoint = frappe.get_doc("Pearnly ERP Endpoint", self.endpoint)
        endpoint.admin_password = self.password + "-rotated"
        endpoint.save(ignore_permissions=True)
        self.assertNotEqual(self.key(), old_key)
        self.unavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED", store.publish, self.endpoint,
            "customers", directory("old-credential-result"), time.time(), expected_scope=old_key)
        store.failed(self.endpoint, "customers", "ERR_LATE_FAILURE", expected_scope=old_key)
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "customers")
        self.assertEqual(frappe.db.get_value(store.DT, old_name, "payload"), old_payload)

    def test_sales_are_isolated_despite_same_upstream_login(self):
        a, b = self.endpoints
        self.assertEqual(self.key(a), self.key(b), "Authority scope is deliberately shared")
        self.assertNotEqual(self.key(a, "sales"), self.key(b, "sales"))
        self.publish({"records": [{"name": "only-a"}]}, endpoint=a, reader="sales", dataset="sales.active")
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, b, "sales.active", reader="sales")
        self.publish({"records": []}, endpoint=b, reader="sales", dataset="sales.active")
        self.assertEqual(store.read(a, "sales.active", reader="sales")["data"]["records"], [{"name": "only-a"}])
        self.assertEqual(store.read(b, "sales.active", reader="sales")["data"]["records"], [])

    def test_sales_revocation_and_regrant_cannot_reuse_old_generation(self):
        self.publish({"records": []}, reader="sales", dataset="sales.active")
        old_key = self.key(reader="sales")
        operator = frappe.get_doc("DMS Operator", self.users[0])
        operator.can_query_dms = 0
        operator.save(ignore_permissions=True)
        frappe.db.commit()
        self.unavailable("ERR_DMS_QUERY_FORBIDDEN", store.read, self.endpoint, "sales.active", reader="sales")
        self.unavailable("ERR_DMS_QUERY_FORBIDDEN", store.request, self.endpoint, "sales.query", {})
        operator.can_query_dms = 1
        operator.save(ignore_permissions=True)
        frappe.db.commit()
        self.assertNotEqual(self.key(reader="sales"), old_key)
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "sales.active", reader="sales")

    def test_user_operator_and_endpoint_disable_refuse_cached_authority(self):
        self.publish()
        for doctype, name, field, disabled, enabled in (
            ("User", self.users[0], "enabled", 0, 1),
            ("DMS Operator", self.users[0], "status", "inactive", "active"),
            ("Pearnly ERP Endpoint", self.endpoint, "enabled", 0, 1),
        ):
            with self.subTest(doctype=doctype):
                frappe.db.set_value(doctype, name, field, disabled)
                self.unavailable("ERR_NO_CREDS", store.read, self.endpoint, "customers")
                frappe.db.set_value(doctype, name, field, enabled)

    def test_query_requests_are_private_and_do_not_claim_data_ready(self):
        a, b = self.endpoints
        self.key(a, "sales")
        self.key(b, "sales")
        params = {"field": "customer", "query": "SYNTHETIC", "status": "active"}
        store.request(a, "sales.query", params)
        frappe.db.rollback()  # Private registration is read on the next request.
        self.assertEqual(store.requested(a), [{"dataset": "sales.query", "params": params}])
        self.assertEqual(store.requested(b), [])
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, a, "sales.query", reader="sales")
        self.assertEqual(self.observe(dataset="sales.query", reader="sales"), {"unavailable": "ERR_DMS_SNAPSHOT_PENDING"})

    def test_query_request_limit_serializes_two_old_snapshot_contenders(self):
        self.key(reader="sales")
        for index in range(31):
            store.request(self.endpoint, "sales.limit." + str(index), {"query": "SYNTHETIC"})
        frappe.db.commit()
        children = []
        try:
            # Both connections must observe 31 in their old RR snapshot before
            # either competes for the final slot. Native row locks, no DB mocks.
            for suffix in ("first", "second"):
                child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                    "--request-contender", self.endpoint, "sales.limit." + suffix],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                children.append(child)
            for child in children:
                ready, _, _ = select.select([child.stdout], [], [], 15)
                self.assertTrue(ready, "Request contender did not establish its old snapshot")
                self.assertEqual(child.stdout.readline().strip(), "READY_31")
            for child in children:
                child.stdin.write("GO\n")
                child.stdin.flush()
            results = []
            for child in children:
                output, error = child.communicate(timeout=20)
                self.assertEqual(child.returncode, 0, error[-1500:])
                results.append(json.loads(output))
            self.assertCountEqual(results, [{"ok": True}, {"error": "ERR_DMS_SNAPSHOT_REQUEST_LIMIT"}])
            frappe.db.rollback()
            requests = store.requested(self.endpoint)
            self.assertEqual(len(requests), 32)
            self.assertEqual(sum(row["dataset"] in {"sales.limit.first", "sales.limit.second"}
                                 for row in requests), 1)
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                child.communicate(timeout=10)

    def test_query_registration_never_commits_or_rolls_back_business_marker(self):
        from pearnly_core import line_session
        marker = "SNAPSHOT-BUSINESS-" + uuid.uuid4().hex
        try:
            self.key(reader="sales")
            line_session.set_session(marker, "booking_review", {"stage": "committed"})
            frappe.db.commit()
            self.assertEqual(store.requested(self.endpoint), [])
            pending = json.dumps({"stage": "not-committed"})
            frappe.db.set_value(line_session.DT, marker, "payload", pending)
            store.request(self.endpoint, "sales.marker", {"query": "SYNTHETIC"})
            self.assertEqual(frappe.db.get_value(line_session.DT, marker, "payload"), pending)
            observer = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--marker", marker],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(observer.returncode, 0, observer.stderr[-1500:])
            self.assertEqual(json.loads(observer.stdout), {"stage": "committed"})
            frappe.db.rollback()
            self.assertEqual(json.loads(frappe.db.get_value(line_session.DT, marker, "payload")),
                             {"stage": "committed"})
            self.assertEqual(store.requested(self.endpoint),
                [{"dataset": "sales.marker", "params": {"query": "SYNTHETIC"}}])
        finally:
            frappe.db.rollback()
            frappe.db.delete(line_session.DT, {"line_user_id": marker})
            frappe.db.commit()

    def test_private_geo_checkpoint_is_not_a_readable_half_tree(self):
        key = self.key()
        checkpoint = {"started_at": time.time(), "step": 1, "tree": {"todo": [["districts", "P1"]]}}
        store.checkpoint(self.endpoint, checkpoint, expected_scope=key)
        self.assertEqual(store.stage(self.endpoint), checkpoint)
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, "geo")
        frappe.db.commit()
        self.assertEqual(self.observe(dataset="geo"), {"unavailable": "ERR_DMS_SNAPSHOT_PENDING"})
        from frappe import client
        frappe.set_user(self.users[0])
        with self.assertRaises(frappe.PermissionError):
            client.get(store.DT, store._name(key, "geo"))

    def test_geo_checkpoint_failure_preserves_published_tree_until_atomic_replace(self):
        self.publish(geo(), dataset="geo")
        frappe.db.commit()
        old = self.observe(dataset="geo")
        store.checkpoint(self.endpoint, {"tree": {"partial": True}}, expected_scope=self.key())
        store.failed(self.endpoint, "geo", "ERR_SYNTHETIC_READ_FAILURE")
        frappe.db.commit()
        failed = self.observe(dataset="geo")
        self.assertEqual(failed["data"], old["data"])
        self.assertEqual(failed["generation"], old["generation"])
        self.assertTrue(failed["stale"])
        self.publish(geo("complete-new"), dataset="geo", start=time.time())
        store.checkpoint(self.endpoint, None, expected_scope=self.key())
        self.assertEqual(self.observe(dataset="geo"), failed)
        frappe.db.commit()
        self.assertEqual(self.observe(dataset="geo")["data"], geo("complete-new"))
        self.assertIsNone(store.stage(self.endpoint))

    def test_geo_checkpoint_cannot_cross_rotated_credentials(self):
        key = self.key()
        store.checkpoint(self.endpoint, {"step": 1}, expected_scope=key)
        endpoint = frappe.get_doc("Pearnly ERP Endpoint", self.endpoint)
        endpoint.admin_password = self.password + "-geo-rotation"
        endpoint.save(ignore_permissions=True)
        self.unavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED", store.checkpoint,
                         self.endpoint, {"step": 2}, expected_scope=key)
        self.assertIsNone(store.stage(self.endpoint))

    def test_authentication_failures_reject_old_snapshot_immediately(self):
        self.publish()
        for code in ("ERR_DMS_AUTH", "ERR_DMS_ADMIN_AUTH", "ERR_NO_CREDS", "ERR_DMS_QUERY_FORBIDDEN"):
            with self.subTest(code=code):
                store.failed(self.endpoint, "customers", code)
                self.unavailable(code, store.read, self.endpoint, "customers")

    def test_current_overlay_guard_sees_cross_connection_revoke_without_committing_business(self):
        from pearnly_core import line_session
        from pearnly_dms.adapter import snapshot_writeback
        from pearnly_dms.client.gateway import DmsClient
        protocol = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-reader.py")))
        marker = "SNAPSHOT-BUSINESS-" + uuid.uuid4().hex
        try:
            for action, code in (("admin-password", "ERR_DMS_SNAPSHOT_SCOPE_CHANGED"),
                                 ("disable-operator", "ERR_NO_CREDS")):
                with self.subTest(action=action):
                    operator = frappe.get_doc("DMS Operator", self.users[0])
                    operator.status = "active"
                    operator.save(ignore_permissions=True)
                    self.publish()
                    line_session.set_session(marker, "booking_review", {"stage": "committed"})
                    frappe.db.commit()
                    captured_scope = self.key()
                    snapshot_name = store._name(captured_scope, "customers")
                    old_payload = frappe.db.get_value(store.DT, snapshot_name, "payload")
                    pending = json.dumps({"stage": "business-write-not-committed"})
                    frappe.db.set_value(line_session.DT, marker, "payload", pending)
                    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--mutate",
                        self.users[0], self.endpoint, action], capture_output=True, text=True, timeout=20)
                    self.assertEqual(child.returncode, 0, child.stderr[-1500:])
                    self.assertEqual(child.stdout.strip(), "MUTATED_SYNTHETIC_FIXTURE")
                    self.unavailable(code, store.verified_customer, self.endpoint,
                        "customer-1", {"name": "must-not-publish"}, expected_scope=captured_scope)
                    transport = protocol["Transport"]()
                    self.assertFalse(snapshot_writeback.customer(self.endpoint,
                        DmsClient(transport, "https://snapshot.invalid/dms/"), "C1",
                        expected_scope=captured_scope))
                    self.assertEqual(transport.calls, [("cus/form.php", {"status": "e", "id": "C1"})])
                    self.assertEqual(frappe.db.get_value(store.DT, snapshot_name, "payload"), old_payload)
                    self.assertEqual(frappe.db.get_value(line_session.DT, marker, "payload"), pending,
                                     "The helper must not roll back the pending business draft")
                    observer = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--marker", marker],
                        capture_output=True, text=True, timeout=20)
                    self.assertEqual(observer.returncode, 0, observer.stderr[-1500:])
                    self.assertEqual(json.loads(observer.stdout), {"stage": "committed"},
                                     "The helper must not implicitly commit the pending business draft")
                    frappe.db.commit()
                    observer = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--marker", marker],
                        capture_output=True, text=True, timeout=20)
                    self.assertEqual(observer.returncode, 0, observer.stderr[-1500:])
                    self.assertEqual(json.loads(observer.stdout), json.loads(pending),
                                     "A refused optional cache write must leave business evidence committable")
        finally:
            frappe.db.rollback()
            frappe.db.delete(line_session.DT, {"line_user_id": marker})
            frappe.db.commit()

    def test_verified_cache_commit_is_independent_of_business_transaction(self):
        from pearnly_core import line_session
        from pearnly_dms.adapter import snapshot_writeback
        from pearnly_dms.client.gateway import DmsClient
        protocol = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-reader.py")))
        marker = "SNAPSHOT-BUSINESS-" + uuid.uuid4().hex
        try:
            self.publish(start=time.time() - 100)
            line_session.set_session(marker, "booking_review", {"stage": "committed"})
            frappe.db.commit()
            before = store.read(self.endpoint, "customers")
            captured = snapshot_writeback.capture_scope(self.endpoint)
            pending = json.dumps({"stage": "not-committed"})
            frappe.db.set_value(line_session.DT, marker, "payload", pending)
            transport = protocol["Transport"]()
            self.assertTrue(snapshot_writeback.customer(self.endpoint,
                DmsClient(transport, "https://snapshot.invalid/dms/"), "C1", expected_scope=captured))
            self.assertEqual(transport.calls, [("cus/form.php", {"status": "e", "id": "C1"})])
            current = self.observe()
            self.assertEqual(current["data"]["customers"]["C1"]["name"], "Synthetic C1")
            self.assertEqual(current["as_of"], before["as_of"])
            self.assertEqual(current["completed_at"], before["completed_at"])
            self.assertNotEqual(current["generation"], before["generation"])
            self.assertEqual(frappe.db.get_value(line_session.DT, marker, "payload"), pending)
            observer = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--marker", marker],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(observer.returncode, 0, observer.stderr[-1500:])
            self.assertEqual(json.loads(observer.stdout), {"stage": "committed"})
            frappe.db.rollback()
            self.assertEqual(self.observe(), current,
                             "Caller rollback must not undo the independently verified external result cache")
            self.assertEqual(json.loads(frappe.db.get_value(line_session.DT, marker, "payload")),
                             {"stage": "committed"})
        finally:
            frappe.db.rollback()
            frappe.db.delete(line_session.DT, {"line_user_id": marker})
            frappe.db.commit()

    def test_customer_native_crud_cannot_mutate_snapshot(self):
        from frappe import client, share
        self.publish()
        frappe.db.commit()
        name = store._name(self.key(), "customers")
        before = self.observe()
        frappe.set_user(self.users[0])
        actions = {
            "read": lambda: client.get(store.DT, name),
            "update": lambda: client.set_value(store.DT, name, "payload", "{}"),
            "insert": lambda: frappe.get_doc({"doctype": store.DT, "scope_key": "forged", "dataset": "customers"}).insert(),
            "delete": lambda: frappe.delete_doc(store.DT, name),
            "rename": lambda: frappe.rename_doc(store.DT, name, name + "-forged"),
            "share": lambda: share.add(store.DT, name, user=self.users[1], read=1),
        }
        for label, action in actions.items():
            with self.subTest(action=label), self.assertRaises(frappe.PermissionError):
                action()
        frappe.set_user("Administrator")
        # The controller still rejects document writes even for code requesting
        # ignore_permissions; only the private store performs controlled SQL.
        doc = frappe.get_doc(store.DT, name)
        doc.payload = "{}"
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(store.DT, name, ignore_permissions=True)
        frappe.db.rollback()
        self.assertEqual(self.observe(), before)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--request-contender":
        with patch.object(requests.sessions.Session, "request", deny_http):
            connect()
            try:
                endpoint, dataset = sys.argv[2:]
                owner = frappe.db.get_value("Pearnly ERP Endpoint", endpoint, "owner_user") or ""
                if not (owner.startswith("snapshot-") and owner.endswith("@example.invalid")
                        and dataset in {"sales.limit.first", "sales.limit.second"}):
                    raise RuntimeError("Refusing a non-owned request contender")
                assert len(store.requested(endpoint)) == 31
                print("READY_31", flush=True)
                if sys.stdin.readline().strip() != "GO":
                    raise RuntimeError("Missing parent barrier")
                try:
                    store.request(endpoint, dataset, {"query": "SYNTHETIC"})
                    frappe.db.commit()
                    print(json.dumps({"ok": True}))
                except store.SnapshotUnavailable as exc:
                    frappe.db.rollback()
                    print(json.dumps({"error": exc.error_code}))
            finally:
                frappe.db.rollback()
                frappe.destroy()
            assert not HTTP_ATTEMPTS
    elif len(sys.argv) == 5 and sys.argv[1] == "--mutate":
        with patch.object(requests.sessions.Session, "request", deny_http):
            connect()
            try:
                user, endpoint, action = sys.argv[2:]
                if not (user.startswith("snapshot-") and user.endswith("@example.invalid")
                        and frappe.db.get_value("Pearnly ERP Endpoint", endpoint, "owner_user") == user):
                    raise RuntimeError("Refusing a non-owned synthetic identity")
                if action == "admin-password":
                    doc = frappe.get_doc("Pearnly ERP Endpoint", endpoint)
                    doc.admin_password = "fake-admin-rotation-" + uuid.uuid4().hex
                elif action == "disable-operator":
                    doc = frappe.get_doc("DMS Operator", user)
                    doc.status = "inactive"
                else:
                    raise RuntimeError("Unknown fixture mutation")
                doc.save(ignore_permissions=True)
                frappe.db.commit()
                print("MUTATED_SYNTHETIC_FIXTURE")
            finally:
                frappe.db.rollback()
                frappe.destroy()
            assert not HTTP_ATTEMPTS
    elif len(sys.argv) == 3 and sys.argv[1] == "--marker":
        with patch.object(requests.sessions.Session, "request", deny_http):
            connect()
            try:
                marker = sys.argv[2]
                if not marker.startswith("SNAPSHOT-BUSINESS-"):
                    raise RuntimeError("Refusing a non-owned business marker")
                print(frappe.db.get_value("Pearnly LINE Session", marker, "payload"))
            finally:
                frappe.db.rollback()
                frappe.destroy()
            assert not HTTP_ATTEMPTS
    elif len(sys.argv) == 5 and sys.argv[1] == "--observe":
        with patch.object(requests.sessions.Session, "request", deny_http):
            connect()
            try:
                try:
                    result = store.read(sys.argv[2], sys.argv[4], reader=sys.argv[3])
                except store.SnapshotUnavailable as exc:
                    result = {"unavailable": exc.error_code}
                print(json.dumps(result, ensure_ascii=False))
            finally:
                frappe.db.rollback()
                frappe.destroy()
            assert not HTTP_ATTEMPTS
    else:
        unittest.main(verbosity=2)
