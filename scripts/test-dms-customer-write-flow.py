"""Pinned Frappe: real LINE queue/client/ledger policy, memory DB and fake transport.

No Site, Redis, credentials, network, messages or business writes. Not a native DB
concurrency test. Only the outer authenticated DMS session and storage/HTTP are
substituted. Run env/bin/python scripts/test-dms-customer-write-flow.py.
"""
import copy
from pathlib import Path
import runpy
import unittest
from unittest.mock import MagicMock, patch

import frappe

queue = runpy.run_path(str(Path(__file__).with_name("test-dms-intake-queue.py")))
identity = runpy.run_path(str(Path(__file__).with_name("test-dms-customer-identity.py")))
Base = queue["IntakeQueue"]
UID, USER = queue["LINE_USER"], queue["USER"]
from pearnly_dms.intake import service
from pearnly_dms.adapter.operations import err
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.customer_merge import unknown

REAL_PUSH = service.push_idcard_fields


class CustomerWriteFlow(unittest.TestCase):
    setUpClass = classmethod(Base.setUpClass.__func__)
    fixture, event, worker, messages, http_post = (getattr(Base, key) for key in
        ("fixture", "event", "worker", "messages", "http_post"))

    def setUp(self):
        Base.setUp(self)
        self.enterContext(patch.object(service, "push_idcard_fields", REAL_PUSH))
        self.db.rows["Pearnly ERP Endpoint", "offline-endpoint"] = {
            "name": "offline-endpoint", "system_url": "https://identity.invalid/dms/"}
        self.db.set_value = lambda kind, name, values: self.db.rows[kind, name].update(values)
        doc = queue["MemoryDocument"]
        def insert(record, **kwargs):
            record.name = kwargs.get("set_name") or record.get("name")
            if record.name and (record.doctype, record.name) in self.db.rows:
                raise RuntimeError("duplicate ledger key")
            return doc.save(record)
        self.enterContext(patch.object(doc, "insert", insert))
        frappe.cache().lock.side_effect = lambda *a, **kw: MagicMock()
        from pearnly_dms.adapter import snapshot_writeback
        self.enterContext(patch.object(snapshot_writeback, "capture_scope", return_value=None))
        self.enterContext(patch.object(snapshot_writeback, "customer", return_value=False))
        from pearnly_dms.operators import roster
        self.enterContext(patch.object(roster, "get_profile", return_value={"dms_role": "admin", "status": "active"}))
        self.enterContext(patch.object(self.flow.draft, "has_admin_creds", return_value=True))
        self.transport = identity["Transport"]()
        self.client = identity["DmsClient"](self.transport, "https://identity.invalid/dms/")
        def logged_in(endpoint, fn, **kwargs):
            try:
                return fn(self.client, None)
            except DmsClientError as exc:
                return err(exc.error_code, response_body=getattr(exc, "response_body", None))
        self.login = self.enterContext(patch.object(service, "run_logged_in", side_effect=logged_in))

    def review(self):
        self.fixture()
        sess = self.store.get_session(UID)
        sess["payload"]["id_card"].update(people_id=identity["PID"], name=identity["NAME"], prefix_name="นาย")
        self.store.set_session(UID, "collecting", sess["payload"])
        self.lookup.return_value = {"ok": True, "scenario": "none", "prefixes": [["17", "นาย"]],
                                   "geo": {"selected": identity["ADDR"]}}
        self.event(text=queue["PHONE"])
        return self.store.get_session(UID)["payload"]

    def confirm(self, payload):
        self.event(action=self.cards.ACT_CREATE, nonce=payload["nonce"])
        self.worker()

    def ledgers(self):
        return [row for (kind, name), row in self.db.rows.items()
                if kind == "Pearnly Push Log" and name.startswith("CW-")]

    def test_create_collision_returns_new_nonce_and_live_diff_without_write(self):
        payload = self.review()
        self.transport.records["own"] = identity["record"]()
        self.confirm(payload)
        new = self.store.get_session(UID)["payload"]
        self.assertEqual(new["customer_id"], "own")
        self.assertNotEqual(new["nonce"], payload["nonce"])
        self.assertEqual(new["customer_baseline"]["phone"], "0800000001")
        self.assertEqual(new["scenario"], "exact_diff")
        self.assertFalse(self.transport.writes())
        self.assertFalse(self.ledgers())
        self.event(action=self.cards.ACT_CREATE, nonce=payload["nonce"])
        self.assertFalse(self.jobs)

    def test_verified_success_is_durable_and_same_request_does_not_repost(self):
        payload = self.review()
        self.confirm(payload)
        self.assertEqual(len(self.transport.writes()), 1)
        self.assertEqual(len(self.ledgers()), 1)
        row = self.ledgers()[0]
        self.assertEqual((row["status"], row["business_key"], row["actor"]), ("success", "created", USER))
        # Same already-claimed execution can recover its own verified result.
        self.store.set_session(UID, "reviewing", {**payload, "nonce": None})
        self.flow.write_create({"user_id": USER, "binding_revision": "revision-1"}, UID, payload)
        self.assertEqual(len(self.transport.writes()), 1)
        self.assertEqual(self.login.call_count, 1)

    def test_unknown_marker_survives_reset_and_new_nonce(self):
        payload = self.review()
        post = self.transport.post
        def fail(url, **kwargs):
            result = post(url, **kwargs)
            if url.endswith("cus/new.php"):
                raise TimeoutError("lost acknowledgement")
            return result
        self.transport.post = fail
        self.confirm(payload)
        self.assertEqual(self.ledgers()[0]["status"], "manual")
        self.assertEqual(len(self.transport.writes()), 1)
        # Simulate upstream search invisibility; don't assume absence authorizes replay.
        self.transport.searches = [[], []]
        self.transport.post = post
        again = self.review()
        self.assertNotEqual(again["nonce"], payload["nonce"])
        self.confirm(again)
        self.assertEqual(len(self.transport.writes()), 1)
        self.assertEqual(len(self.ledgers()), 1)

    def test_preflight_failure_does_not_reserve_dispatch(self):
        payload = self.review()
        self.transport.searches = ["err::unavailable"]
        self.confirm(payload)
        self.assertFalse(self.transport.writes())
        self.assertFalse(self.ledgers())

    def test_pending_same_request_does_not_log_in_or_post_again(self):
        payload = self.review()
        from pearnly_dms.intake.customer_attempt import Attempt
        fields = {key: payload["draft"].get(key, "") for key in self.flow._CREATE_ID_KEYS}
        block = {key: payload["draft"].get(key, "") for key in self.flow._ADDR_BLOCK_KEYS}
        addresses = {suffix: dict(block) for suffix in ("", "_ct", "_sd")}
        marker = Attempt("offline-endpoint", "line:" + payload["nonce"], USER,
                         fields=fields, mode="create", customer_id=None, addresses=addresses, baseline=None)
        marker.begin()
        self.confirm(payload)
        self.assertEqual(self.login.call_count, 0)
        self.assertFalse(self.transport.writes())
        self.assertEqual(self.ledgers()[0]["status"], "manual")

    def test_reset_during_preflight_revokes_customer_dispatch(self):
        payload = self.review()
        post = self.transport.post
        def reset(url, **kwargs):
            if url.endswith("cus/form.php"):
                self.store.set_session(UID, "menu", {"mode": "customer"})
            return post(url, **kwargs)
        self.transport.post = reset
        self.confirm(payload)
        self.assertFalse(self.transport.writes())
        self.assertFalse(self.ledgers())
        self.assertEqual(self.store.get_session(UID)["state"], "menu")

    def test_update_conflict_reconfirmed_with_new_baseline_then_preserves_other_changes(self):
        payload = self.review()
        self.transport.records["own"] = identity["record"]()
        baseline = self.client.read_customer("own")
        payload.update(customer_id="own", customer_baseline=baseline, scenario="exact_diff",
                       field_diffs=[{"field": "phone", "old": baseline["phone"], "new": queue["PHONE"]}])
        self.store.set_session(UID, "reviewing", payload)
        self.transport.records["own"]["txttel"] = "0800000888"
        self.transport.records["own"]["txtemail"] = "changed@example.invalid"
        self.event(action=self.cards.ACT_UPDATE, nonce=payload["nonce"])
        self.worker()
        self.assertFalse(self.transport.writes())
        fresh = self.store.get_session(UID)["payload"]
        self.assertEqual(fresh["customer_baseline"]["phone"], "0800000888")
        self.assertNotEqual(fresh["nonce"], payload["nonce"])
        self.event(action=self.cards.ACT_UPDATE, nonce=fresh["nonce"])
        self.worker()
        self.assertEqual(len(self.transport.writes()), 1)
        self.assertEqual(self.transport.records["own"]["txtemail"], "changed@example.invalid")
        self.assertEqual(self.transport.records["own"]["txttel"], queue["PHONE"])

    def test_old_approval_cannot_authorize_new_conflicting_value(self):
        from pearnly_dms.approval import approval_flow, approval_store
        payload = self.review()
        self.transport.records["own"] = identity["record"]()
        baseline = self.client.read_customer("own")
        payload.update(customer_id="own", customer_baseline=baseline, has_admin=True)
        binding = {"user_id": USER, "binding_revision": "revision-1"}
        self.store.set_session(UID, "approval_pending", {
            "approval_request": "approval-one", "source": payload, "binding": binding})
        req = {"id": "approval-one", "operator_user_id": USER, "customer_id": "own",
               "field_diffs": [{"field": "phone", "old": baseline["phone"], "new": queue["PHONE"]}],
               "draft": {"fields": payload["draft"], "baseline": baseline}}
        self.transport.records["own"]["txttel"] = "0800000888"
        finish = self.enterContext(patch.object(approval_store, "finish", return_value=True))
        self.enterContext(patch.object(approval_flow.roster_store, "line_user_id_of", return_value=UID))
        self.enterContext(patch.object(approval_flow, "resolve_dms_endpoint", return_value={"name": "offline-endpoint"}))
        approval_flow._execute_approved(binding, UID, req)
        finish.assert_called_once_with("approval-one", "rejected")
        fresh = self.store.get_session(UID)
        self.assertEqual(fresh["state"], "reviewing")
        self.assertEqual(fresh["payload"]["customer_baseline"]["phone"], "0800000888")
        self.assertFalse(self.transport.writes())

    def test_old_approval_conflict_does_not_replace_new_operator_draft(self):
        from pearnly_dms.approval import approval_flow, approval_store
        payload = self.review()
        self.transport.records["own"] = identity["record"]()
        baseline = self.client.read_customer("own")
        self.transport.records["own"]["txttel"] = "0800000888"
        self.enterContext(patch.object(approval_store, "finish", return_value=True))
        self.enterContext(patch.object(approval_flow.roster_store, "line_user_id_of", return_value=UID))
        self.enterContext(patch.object(approval_flow, "resolve_dms_endpoint", return_value={"name": "offline-endpoint"}))
        approval_flow._execute_approved({"user_id": USER}, UID, {
            "id": "old-request", "operator_user_id": USER, "customer_id": "own",
            "field_diffs": [{"field": "phone", "new": queue["PHONE"]}],
            "draft": {"fields": payload["draft"], "baseline": baseline}})
        self.assertEqual(self.store.get_session(UID)["payload"], payload)
        self.assertFalse(self.transport.writes())


if __name__ == "__main__":
    unittest.main(defaultTest="CustomerWriteFlow", verbosity=2)
