#!/usr/bin/env python3
"""Real native customer client/three-way contract with fake transport, zero IO.

Run: python3 scripts/test-dms-customer-conflicts.py. No Frappe/Site, files written,
credentials, LINE, OCR or DMS requests. Does not prove upstream atomic CAS.
"""
import copy
from pathlib import Path
import runpy
import socket
import unittest
from unittest.mock import patch

fixture = runpy.run_path(str(Path(__file__).with_name("test-dms-customer-identity.py")))
DmsClient, Transport, record = (fixture[key] for key in ("DmsClient", "Transport", "record"))
FIELDS, PID, OTHER = (fixture[key] for key in ("FIELDS", "PID", "OTHER"))
from pearnly_dms.contracts.customer_merge import CustomerConflict, flatten, merge, split
from pearnly_dms.contracts.client_base import DmsClientError


class CustomerWrites(unittest.TestCase):
    def client(self):
        transport = Transport({"own": record()})
        client = DmsClient(transport, "https://identity.invalid/dms/")
        return client, transport, client.read_customer("own")

    def test_create_collision_never_edits(self):
        cl, tr, baseline = self.client()
        with self.assertRaises(CustomerConflict) as caught:
            cl.save_customer(fields=FIELDS, mode="create")
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_EXISTS")
        self.assertEqual(caught.exception.response_body["customer_conflict"]["current"], baseline)
        self.assertEqual(tr.writes(), [])

    def test_duplicate_response_is_new_review_not_idempotency_proof(self):
        tr = Transport()
        cl = DmsClient(tr, "https://identity.invalid/dms/")
        tr.create_collision, tr.collision_record = True, record()
        with self.assertRaises(CustomerConflict):
            cl.save_customer(fields=FIELDS, mode="create")
        self.assertEqual([path for path, _ in tr.writes()], ["cus/new.php"])

    def test_unedited_live_changes_preserved(self):
        cl, tr, baseline = self.client()
        tr.records["own"]["txtcusname"] = "Changed upstream"
        cl.save_customer(fields={"name": baseline["name"], "phone": "0800000999"},
                         mode="overwrite", customer_id="own", baseline=baseline)
        self.assertEqual(tr.records["own"]["txtcusname"], "Changed upstream")
        self.assertEqual(tr.records["own"]["txttel"], "0800000999")

    def test_same_field_conflict_writes_nothing(self):
        cl, tr, baseline = self.client()
        tr.records["own"]["txttel"] = "0800000888"
        with self.assertRaises(CustomerConflict) as caught:
            cl.save_customer(fields={"phone": "0800000999"}, mode="overwrite",
                             customer_id="own", baseline=baseline)
        conflict = caught.exception.response_body["customer_conflict"]
        self.assertEqual(conflict["conflicts"], [{"field": "phone", "old": "0800000888", "new": "0800000999"}])
        self.assertEqual(tr.writes(), [])

    def test_explicit_clear_and_independent_addresses(self):
        cl, tr, baseline = self.client()
        before = copy.deepcopy(tr.records["own"])
        tr.records["own"]["txtroad_sd"] = "new shipping road"
        cl.save_customer(fields={}, addresses={"": {"road": ""}, "_ct": {"house_no": "77"}},
                         mode="overwrite", customer_id="own", baseline=baseline)
        self.assertEqual(tr.records["own"]["txtroad"], "")
        self.assertEqual(tr.records["own"]["txthousenum_ct"], "77")
        self.assertEqual(tr.records["own"]["txtroad_sd"], "new shipping road")
        self.assertEqual(tr.records["own"]["txthousenum"], before["txthousenum"])

    def test_unchanged_address_block_does_not_overwrite_concurrent_value(self):
        cl, tr, baseline = self.client()
        tr.records["own"]["txtroad_ct"] = "new contact road"
        cl.save_customer(fields={"phone": "0800000999"},
                         addresses={"_ct": {"road": baseline["road_ct"]}},
                         mode="overwrite", customer_id="own", baseline=baseline)
        self.assertEqual(tr.records["own"]["txtroad_ct"], "new contact road")

    def test_baseline_missing_fail_closed(self):
        cl, tr, _ = self.client()
        with self.assertRaises(CustomerConflict):
            cl.save_customer(fields={"phone": "0800000999"}, mode="overwrite", customer_id="own")
        self.assertFalse(tr.writes())

    def test_same_desired_concurrent_value_is_not_conflict(self):
        changes, conflicts = merge({"phone": "old"}, {"phone": "new"}, {"phone": "new"})
        self.assertEqual((changes, conflicts), ({"phone": "new"}, []))

    def test_timeout_after_dispatch_is_unknown_not_retryable(self):
        cl, tr, baseline = self.client()
        post = tr.post
        def fail(url, **kwargs):
            if url.endswith("cus/edit.php"):
                raise TimeoutError()
            return post(url, **kwargs)
        tr.post = fail
        attempts = []
        with self.assertRaises(DmsClientError) as caught:
            cl.save_customer(fields={"phone": "0800000999"}, mode="overwrite", customer_id="own",
                             baseline=baseline, on_attempt=lambda: attempts.append(True))
        self.assertEqual(attempts, [True])
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertTrue(caught.exception.response_body["submitted"])

    def test_readback_verifies_nonidentity_fields_too(self):
        cl, tr, baseline = self.client()
        tr.ignore_fields = {"txttel"}
        with self.assertRaises(DmsClientError) as caught:
            cl.save_customer(fields={"phone": "0800000999"}, mode="overwrite",
                             customer_id="own", baseline=baseline)
        self.assertEqual(caught.exception.error_code, "ERR_DMS_CUSTOMER_UNCONFIRMED")
        self.assertEqual(len(tr.writes()), 1)

    def test_flatten_split_keep_explicit_blank_three_blocks(self):
        values = flatten({"phone": "", "people_id": PID}, {"": {"road": ""}, "_sd": {"road": "ship"}})
        fields, addresses = split(values)
        self.assertEqual(fields["phone"], "")
        self.assertEqual(fields["tax_id"], PID)
        self.assertEqual(addresses, {"": {"road": ""}, "_ct": {}, "_sd": {"road": "ship"}})


if __name__ == "__main__":
    with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")), \
         patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")):
        unittest.main(verbosity=2)
