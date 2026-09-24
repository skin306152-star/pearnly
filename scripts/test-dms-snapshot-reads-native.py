"""Snapshot-backed intake/editor/booking reads on the isolated native Site.

Reuses the guarded snapshot-store lab fixture. Store, MariaDB/Redis, encrypted
credentials and LINE Session persistence are real. Interactive reads must never
call a login or HTTP. Query refresh alone gets a synthetic authenticated transport
while retaining its real orchestration, parsers, permissions and publish calls.
This proves local integration, not external DMS/LINE acceptance.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path
import runpy
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import uuid

import frappe
import requests

from pearnly_core import line_session
from pearnly_dms.adapter import operations
from pearnly_dms.booking.conversation import booking_qa_sync as qa_sync
from pearnly_dms.editor import edit_snapshot
from pearnly_dms.intake import service
from pearnly_dms.masters.master_contract import MasterSyncError, SNAPSHOT_KEYS
from pearnly_dms.query import sales_readback as rb, snapshot_sync
from pearnly_dms.snapshots import store

lab = runpy.run_path(str(Path(__file__).with_name("test-dms-snapshot-store-native.py")))
query_fixture = runpy.run_path(str(Path(__file__).with_name("test-dms-query-snapshots.py")))
PID = "1000000730317"
OTHER_PID = "1000000730325"


def complete_directory():
    masters = {key: [[key + "-1", "TEST", "Synthetic " + key]] for key in (
        "cars", "place_books", "term_sales", "branches", "regis_behalfs", "advisors",
        "source_banks", "cheque_banks", "cashier_banks", "card_banks")}
    masters["prefixes"] = [["prefix-1", "นาย"]]
    masters["company_banks"] = [["bank-1", "TEST", "Synthetic bank", "0000000000", "lab"]]
    masters["employees"] = [{"id": "employee-1", "name": "Synthetic employee"}]
    customer = {"customer_id": "customer-1", "name": "Synthetic Customer", "people_id": PID,
        "tax_id": PID, "cuscode": "TEST-CUSTOMER", "prefix_id": "prefix-1", "phone": "0800000000",
        "house_no": "1", "province_id": "P1", "district_id": "D1", "subdistrict_id": "S1",
        "zipcode_id": "Z1", "province_name": "สมุทรปราการ", "district_name": "เมืองสมุทรปราการ",
        "subdistrict_name": "ปากน้ำ", "zipcode_name": "10270"}
    for suffix in ("_ct", "_sd"):
        customer.update({key + suffix: value for key, value in list(customer.items())
                         if key in ("house_no", "province_id", "district_id", "subdistrict_id", "zipcode_id")})
    return {"masters": masters,
        "paints": {"cars-1": [["paint-1", "TEST", "Synthetic color"]]},
        "geo": {"provinces": {"": [["P0", "Other Province"], ["P1", "สมุทรปราการ"]]},
            "districts": {"P0": [], "P1": [["D0", "Other District"], ["D1", "เมืองสมุทรปราการ"]]},
            "subdistricts": {"D0": [], "D1": [["S0", "ท้ายบ้าน"], ["S1", "ปากน้ำ"]]},
            "zipcodes": {"S0": [["Z0", "10280"]], "S1": [["Z1", "10270"]]}},
        "customers": {"customer-1": customer}}


ADDRESS = {"house_no": "1", "province": "จังหวัดสมุทรปราการ", "district": "อำเภอเมืองสมุทรปราการ",
           "subdistrict": "ตำบลปากน้ำ", "zipcode": "10270"}


class NativeSnapshotReads(lab["SnapshotLabCase"]):
    def setUp(self):
        super().setUp()
        self.line_id = "SNAPSHOT-READS-" + uuid.uuid4().hex
        self.logins = []
        for module in (service, operations, rb, snapshot_sync):
            if hasattr(module, "run_logged_in"):
                self.enterContext(patch.object(module, "run_logged_in", self.no_login))

    def tearDown(self):
        frappe.set_user("Administrator")
        line_session.clear_session(self.line_id)
        frappe.db.commit()
        super().tearDown()
        self.assertEqual(self.logins, [], "Interactive read attempted an external login")

    def no_login(self, *args, **kwargs):
        self.logins.append(True)
        raise AssertionError("Interactive snapshot reads must not log into DMS")

    def seed(self, data=None, *, age=15, geo_age=3600):
        data = copy.deepcopy(complete_directory() if data is None else data)
        geo = data.pop("geo")
        self.publish(geo, start=time.time() - geo_age, dataset="geo")
        self.publish(data, start=time.time() - age)
        frappe.db.commit()
        return store.read(self.endpoint)

    def lookup(self, *, people_id=PID, name="Synthetic Customer", address=None):
        return service.recognize_lookup(self.endpoint, people_id=people_id, name=name,
            ocr_address=ADDRESS if address is None else address, phone="0800000000")

    def persist(self, tenant, line_id, qa):
        self.assertEqual(tenant, frappe.local.site)
        self.assertEqual(line_id, self.line_id)
        line_session.set_session(line_id, "booking_qa", {"qa": qa})

    def qa(self):
        return {"endpoint_id": self.endpoint, "answers": {"car": {"id": "cars-1", "label": "saved"},
            "date": "30/09/2569"}, "master_snapshot": {"rows": {"cars": [["forged-old", "old"]]}}}

    def master(self, qa, key):
        return qa_sync.masters(frappe.local.site, self.line_id, qa, key, persist=self.persist)

    def paints(self, qa):
        return qa_sync.paints(frappe.local.site, self.line_id, qa, persist=self.persist)

    def test_lookup_fields_and_geo_share_real_complete_generation(self):
        snapshot = self.seed()
        result = self.lookup()
        self.assertTrue(result["ok"])
        self.assertEqual(result["scenario"], "exact")
        self.assertEqual(result["match"]["customer_id"], "customer-1")
        self.assertEqual(result["snapshot"]["generation"], snapshot["generation"])
        self.assertEqual(result["snapshot"]["as_of"], snapshot["as_of"])
        self.assertGreater(snapshot["as_of"] - result["snapshot"]["geo_as_of"], 3500)
        self.assertFalse(result["snapshot"]["geo_stale"])
        self.assertEqual(result["geo"]["selected"], {"province_id": "P1", "district_id": "D1",
            "subdistrict_id": "S1", "zipcode_id": "Z1"})
        fields = service.customer_fields(self.endpoint, customer_id="customer-1")
        self.assertTrue(fields["ok"])
        self.assertEqual(fields["current_fields"]["people_id"], PID)
        self.assertEqual(service.geo_options(self.endpoint, level="zipcodes", parent_id="S1"),
                         {"ok": True, "options": [["Z1", "10270"]]})

    def test_absent_customer_differs_from_selected_missing_customer(self):
        self.seed()
        result = self.lookup(people_id=OTHER_PID, name="No matching name")
        self.assertTrue(result["ok"])
        self.assertEqual(result["scenario"], "none")
        self.assertFalse(result["match"]["found"])
        missing = service.customer_fields(self.endpoint, customer_id="missing")
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error_code"], "ERR_DMS_CUSTOMER_LOOKUP")

    def test_duplicate_identity_refuses_automatic_match(self):
        data = complete_directory()
        data["customers"]["customer-2"] = {**data["customers"]["customer-1"], "customer_id": "customer-2"}
        self.seed(data)
        result = self.lookup()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "ERR_DMS_CUSTOMER_LOOKUP")

    def test_missing_and_expired_snapshot_do_not_become_empty_results(self):
        for state in ("cold", "expired"):
            with self.subTest(state=state):
                if state == "expired":
                    self.publish(complete_directory(), start=time.time() - 1900)
                    frappe.db.commit()
                code = "ERR_DMS_SNAPSHOT_PENDING" if state == "cold" else "ERR_DMS_SNAPSHOT_STALE"
                for result in (self.lookup(), service.customer_fields(self.endpoint, customer_id="customer-1"),
                               service.geo_options(self.endpoint, level="provinces")):
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error_code"], code)
                with self.assertRaises(store.SnapshotUnavailable):
                    edit_snapshot.read_edit_snapshot(self.endpoint)
                with self.assertRaises(MasterSyncError) as caught:
                    self.master(self.qa(), "cars")
                self.assertEqual(caught.exception.code, code)

    def test_unmatched_and_ambiguous_address_never_choose_first_option(self):
        self.seed()
        result = self.lookup(address={**ADDRESS, "subdistrict": "Unknown Town"})
        self.assertTrue(result["ok"])
        selected = result["geo"]["selected"]
        self.assertEqual(selected["district_id"], "D1")
        self.assertEqual(selected["subdistrict_id"], "")
        self.assertEqual(selected["zipcode_id"], "")
        data = complete_directory()
        data["geo"]["subdistricts"]["D1"].append(["S2", "ปากน้ำ"])
        data["geo"]["zipcodes"]["S2"] = []
        self.seed(data, age=0)
        self.assertEqual(self.lookup()["geo"]["selected"]["subdistrict_id"], "")
        result = self.lookup(address={**ADDRESS, "province": "Unknown Province"})
        self.assertEqual(set(result["geo"]["selected"].values()), {""})

    def test_editor_projects_same_generation_and_preserves_explicit_empty_geo(self):
        snapshot = self.seed()
        draft = {"people_id": PID, "province_id": "P1", "district_id": "D1",
                 "subdistrict_id": "", "zipcode_id": ""}
        result = edit_snapshot.read_edit_snapshot(self.endpoint, car_ids=["cars-1"],
            customer=draft, customer_id="customer-1", preserve_empty_geo=True)
        self.assertEqual(result["snapshot"]["generation"], snapshot["generation"])
        self.assertEqual(result["snapshot"]["as_of"], snapshot["as_of"])
        self.assertEqual(result["resolved_customer"]["subdistrict_id"], "")
        self.assertEqual(result["resolved_customer"]["zipcode_id"], "")
        self.assertNotIn("zipcodes", result["geo"])
        self.assertEqual(result["paints"]["cars-1"][0][0], "paint-1")
        normal = edit_snapshot.read_edit_snapshot(self.endpoint, customer=draft, customer_id="customer-1")
        self.assertEqual(normal["resolved_customer"]["subdistrict_id"], "S1")

    def test_editor_cannot_fill_from_different_identity(self):
        self.seed()
        from pearnly_dms.contracts.client_base import DmsClientError
        with self.assertRaises(DmsClientError) as caught:
            edit_snapshot.read_edit_snapshot(self.endpoint, customer_id="customer-1", customer={"people_id": OTHER_PID})
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_LOOKUP")

    def test_all_booking_master_and_paint_steps_use_real_store_and_session(self):
        self.seed()
        qa = self.qa()
        answers = copy.deepcopy(qa["answers"])
        for key in SNAPSHOT_KEYS:
            with self.subTest(key=key):
                self.assertEqual(self.master(qa, key)[0][0], complete_directory()["masters"][key][0][0])
        self.assertEqual(self.paints(qa), [["paint-1", "TEST", "Synthetic color"]])
        saved = line_session.get_session(self.line_id)
        self.assertEqual(saved["state"], "booking_qa")
        self.assertEqual(saved["payload"]["qa"]["answers"], answers)
        self.assertEqual(saved["payload"]["qa"]["master_snapshot"]["rows"]["cars"][0][0], "cars-1")

    def test_missing_colour_cannot_fallback_to_saved_qa_colour(self):
        data = complete_directory()
        del data["paints"]["cars-1"]
        self.seed(data)
        qa = self.qa()
        qa["paint_snapshots"] = {"cars-1": {"rows": [["stale-paint", "stale"]]}}
        with self.assertRaises(MasterSyncError) as caught:
            self.paints(qa)
        self.assertEqual(caught.exception.code, "ERR_DMS_MASTER_CHANGED")

    def test_saved_qa_does_not_bypass_changed_credentials_or_disabled_owner(self):
        self.seed()
        qa = self.qa()
        self.master(qa, "cars")
        self.paints(qa)
        endpoint = frappe.get_doc("Pearnly ERP Endpoint", self.endpoint)
        endpoint.admin_password = self.password + "-new"
        endpoint.save(ignore_permissions=True)
        for read in (lambda: self.master(qa, "cars"), lambda: self.paints(qa)):
            with self.assertRaises(MasterSyncError) as caught:
                read()
            self.assertEqual(caught.exception.code, "ERR_DMS_SNAPSHOT_PENDING")
        frappe.db.set_value("User", self.users[0], "enabled", 0)
        with self.assertRaises(MasterSyncError) as caught:
            self.master(qa, "cars")
        self.assertEqual(caught.exception.code, "ERR_NO_CREDS")

    def test_same_generation_never_restamps_qa_master_or_paint_age(self):
        snapshot = self.seed(age=60)
        qa = self.qa()
        self.master(qa, "cars")
        self.paints(qa)
        captured = datetime.fromtimestamp(snapshot["as_of"], timezone.utc).isoformat(timespec="seconds")
        for value in (qa["master_snapshot"], qa["paint_snapshots"]["cars-1"]):
            self.assertEqual(value["captured_at"], captured)
            self.assertEqual(value["source_generation"], snapshot["generation"])
        before = copy.deepcopy(qa)
        self.master(qa, "cars")
        self.paints(qa)
        self.assertEqual(qa, before)

    def test_geo_daily_age_is_independent_of_five_minute_business_generation(self):
        self.seed(geo_age=2 * 86400)
        result = self.lookup()
        self.assertTrue(result["ok"])
        self.assertFalse(result["snapshot"]["stale"])
        self.assertTrue(result["snapshot"]["geo_stale"])
        frappe.db.delete(store.DT, {"name": store._name(self.key(), "geo")})
        self.publish(complete_directory()["geo"], start=time.time() - 4 * 86400, dataset="geo")
        result = self.lookup()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "ERR_DMS_SNAPSHOT_STALE")
        # Expired address data cannot disable unrelated car/paint choices.
        self.assertEqual(self.master(self.qa(), "cars")[0][0], "cars-1")

    def test_query_refresh_to_native_store_then_local_reads_and_revoke(self):
        calls = []
        client = query_fixture["NativeClient"]()
        adapter = query_fixture["adapter"]()
        self.key(reader="sales")
        def authenticated(endpoint, callback, **kwargs):
            self.assertEqual(endpoint, self.endpoint)
            self.assertEqual(kwargs, {"authoritative_read": False, "background": True})
            calls.append(endpoint)
            return callback(client, adapter)
        with patch.object(snapshot_sync, "run_logged_in", authenticated):
            result = snapshot_sync.refresh(self.endpoint)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(calls), 1)
        read = rb.fetch_sales_records(self.users[0], field="customer", query="MATCH", status="active")
        self.assertTrue(read["ok"], read)
        self.assertEqual([row["id"] for row in read["rows"]], ["2", "0"])
        self.assertFalse(rb.fetch_sales_records(self.users[1])["ok"])
        operator = frappe.get_doc("DMS Operator", self.users[0])
        operator.can_query_dms = 0
        operator.save(ignore_permissions=True)
        rejected = rb.fetch_sales_records(self.users[0])
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["error_code"], "ERR_DMS_QUERY_FORBIDDEN")

    def test_query_credentials_changed_during_capture_prevent_publication(self):
        self.key(reader="sales")
        def authenticated(endpoint, callback, **kwargs):
            result = callback(query_fixture["NativeClient"](), query_fixture["adapter"]())
            operator = frappe.get_doc("DMS Operator", self.users[0])
            operator.dms_password = self.password + "-changed-during-io"
            operator.save(ignore_permissions=True)
            return result
        with patch.object(snapshot_sync, "run_logged_in", authenticated):
            result = snapshot_sync.refresh(self.endpoint)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["error_code"], "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        self.unavailable("ERR_DMS_SNAPSHOT_PENDING", store.read, self.endpoint, rb.SALES_DATASET, reader="sales")

    def test_separate_connection_revocation_during_capture_is_not_hidden_by_rr(self):
        for action, expected in (("revoke", "ERR_DMS_QUERY_FORBIDDEN"),
                                 ("password", "ERR_DMS_SNAPSHOT_SCOPE_CHANGED")):
            with self.subTest(action=action):
                operator = frappe.get_doc("DMS Operator", self.users[0])
                operator.can_query_dms = 1
                operator.save(ignore_permissions=True)
                frappe.db.commit()
                key = self.key(reader="sales")
                self.publish({"ok": True, "sentinel": "old generation"},
                             dataset=rb.SALES_DATASET, reader="sales")
                frappe.db.commit()
                name = store._name(key, rb.SALES_DATASET)
                before = frappe.db.get_value(store.DT, name, ["generation", "payload"])
                def authenticated(endpoint, callback, **kwargs):
                    result = callback(query_fixture["NativeClient"](), query_fixture["adapter"]())
                    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--mutate",
                        self.users[0], endpoint, action], capture_output=True, text=True, timeout=20)
                    self.assertEqual(child.returncode, 0, child.stderr[-1500:])
                    self.assertEqual(child.stdout.strip(), "MUTATED_SYNTHETIC_FIXTURE")
                    return result
                with patch.object(snapshot_sync, "run_logged_in", authenticated):
                    result = snapshot_sync.refresh(self.endpoint)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error_code"], expected)
                self.assertEqual(frappe.db.get_value(store.DT, name, ["generation", "payload"]), before)
                self.assertEqual(frappe.get_all(store.DT, filters={"scope_key": key}, pluck="name"), [name])


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--mutate":
        with patch.object(requests.sessions.Session, "request", lab["deny_http"]):
            lab["connect"]()
            user, endpoint, action = sys.argv[2:]
            if not (user.startswith("snapshot-") and user.endswith("@example.invalid")
                    and frappe.db.get_value("Pearnly ERP Endpoint", endpoint, "owner_user") == user):
                raise RuntimeError("Refusing a non-owned synthetic identity")
            if action == "revoke":
                doc = frappe.get_doc("DMS Operator", user)
                doc.can_query_dms = 0
            elif action == "password":
                doc = frappe.get_doc("Pearnly ERP Endpoint", endpoint)
                doc.password = "fake-concurrent-rotation-" + uuid.uuid4().hex
            else:
                raise RuntimeError("Unknown fixture mutation")
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            frappe.destroy()
            assert not lab["HTTP_ATTEMPTS"]
            print("MUTATED_SYNTHETIC_FIXTURE")
    else:
        unittest.main(verbosity=2)
