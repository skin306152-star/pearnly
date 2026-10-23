"""Queue wire contracts without a Site, writer, credential or external request."""
from copy import deepcopy
from decimal import Decimal
import unittest

from pearnly_core.cc_desktop.wire.queue_contracts import (
    QueueContractError, ack_request, ack_response, job, lease_request, lease_response,
)


ID = "972778cf-0b5a-4815-a687-bec2d3738db5"
REFRESH_ID = "18a84c4b-023d-49c6-bcf3-c277472c836a"


def ack(**extra):
    return {"log_id": ID, "result": "success", **extra}


def record(**extra):
    return {"name": "INTERNAL", "history": "CWOH-1", "invoice_no": "INV-1",
            "frozen_payload": {"payload_version": 1, "items": [{"amount": "100.00"}]},
            "frozen_target": {"account": "private"}, "fence": 4,
            "attempt_token": "native-attempt-secret", "actor": "actor@example.test", **extra}


def receipt(stage="success", status="success", **extra):
    return {"response_body": {"meta": {"stage": stage}}, "status": status,
            "external_docnum": "RR-1", "wire_attempt": 2, "result_unknown": False,
            "actor": "private", "fence": 3, "attempt_token": "secret", **extra}


class QueueContractTests(unittest.TestCase):
    def assert_invalid(self, callback, response=False):
        with self.assertRaises(QueueContractError) as caught:
            callback()
        self.assertEqual(caught.exception.code,
                         "companion.queue_response_invalid" if response else "companion.queue_request_invalid")

    def test_lease_default_bounds_and_unknown_fields_match_old_model(self):
        self.assertEqual(lease_request({}), {"max": 10, "agent_id": None})
        for maximum in (1, 10, 50):
            self.assertEqual(lease_request({"max": maximum, "agent_id": " A ", "new_client_field": 1}),
                             {"max": maximum, "agent_id": " A "})

    def test_lease_strict_types_are_an_explicit_coercion_difference(self):
        for value in (True, False, "1", " 2 ", 1.0, None, 0, -1, 51, [], {}):
            with self.subTest(value=value):
                self.assert_invalid(lambda: lease_request({"max": value}))
        self.assert_invalid(lambda: lease_request({"agent_id": 2}))

    def test_ack_preserves_all_eight_fields_and_ignores_extras(self):
        actual = ack_request(ack(extra_from_future_client={"value": True}))
        self.assertEqual(actual, {"log_id": ID, "result": "success", "express_docnum": None,
                                 "error": None, "agent_id": None, "line_modes": None,
                                 "outcome": None, "meta": None})

    def test_success_failure_and_outcome_do_not_discard_or_rewrite_meta(self):
        for result in ("success", "failed"):
            for outcome in (None, "success", "waiting_lock", "rolled_back", "needs_mapping", "needs_review", "unknown"):
                with self.subTest(result=result, outcome=outcome):
                    raw = ack(result=result, outcome=outcome, error="error" * 300,
                              express_docnum="number" * 100, agent_id=" A ",
                              line_modes=[{"mode": "direct_account", "reason": "no mapping", "custom": [1]}],
                              meta={"stage": "client-stage", "written": False, "evidence": {"rows": [1, 2]}, "unknown": {"x": None}})
                    self.assertEqual(ack_request(raw), raw)

    def test_uuid_mapping_identifier_is_not_a_record_or_signed_secret_handle(self):
        for value in ("record-1", "m1_secret", ID.upper(), ID.replace("-", ""), " " + ID, "", None, 4):
            with self.subTest(value=value):
                self.assert_invalid(lambda: ack_request(ack(log_id=value)))
        self.assertEqual(ack_request(ack())["log_id"], ID)

    def test_malformed_ack_is_rejected_before_any_caller_mutation(self):
        for key, values in {
            "result": (None, "SUCCESS", " success ", True, "unknown"),
            "express_docnum": (1, [], {}), "error": (False, {}), "agent_id": (1,),
            "outcome": ([], {}), "line_modes": ({}, [None], ["row"], [["row"]]),
            "meta": ([], "json", True),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    raw = ack(**{key: value}); before = deepcopy(raw)
                    self.assert_invalid(lambda: ack_request(raw))
                    self.assertEqual(raw, before)

    def test_non_json_meta_is_rejected_without_lossy_conversion(self):
        cycle = []; cycle.append(cycle)
        for value in (float("nan"), float("inf"), Decimal("1.23"), {1: "int-key"}, (1, 2), cycle):
            with self.subTest(value=type(value).__name__):
                self.assert_invalid(lambda: ack_request(ack(meta={"value": value})))

    def test_ack_nested_values_are_detached_without_stage_override(self):
        raw = ack(meta={"stage": "success", "nested": [1]}, line_modes=[{"custom": [1]}])
        planned = ack_request(raw)
        planned["meta"]["nested"].append(2)
        planned["line_modes"][0]["custom"].append(2)
        self.assertEqual(raw["meta"]["nested"], [1])
        self.assertEqual(raw["line_modes"][0]["custom"], [1])

    def test_job_exposes_only_frozen_payload_and_legacy_identifiers(self):
        raw = record(); output = job(ID, raw)
        self.assertEqual(set(output), {"log_id", "history_id", "invoice_no", "payload"})
        self.assertEqual(output["log_id"], ID)
        self.assertEqual(output["history_id"], "CWOH-1")
        self.assertEqual(output["payload"], raw["frozen_payload"])
        output["payload"]["items"][0]["amount"] = "999.00"
        self.assertEqual(raw["frozen_payload"]["items"][0]["amount"], "100.00")
        empty = job(ID, record(history=None, invoice_no=None, frozen_payload={}))
        self.assertEqual((empty["history_id"], empty["invoice_no"], empty["payload"]), ("", None, {}))

    def test_job_rejects_unfrozen_or_malformed_payload(self):
        for value in (None, [], "{}"):
            self.assert_invalid(lambda: job(ID, record(frozen_payload=value)), response=True)
        self.assert_invalid(lambda: job("native-record", record()), response=True)
        self.assert_invalid(lambda: job(ID, record(invoice_no=7)), response=True)

    def test_lease_response_retains_source_shape_and_optional_refresh(self):
        self.assertEqual(lease_response([]), {"ok": True, "lease_seconds": 120, "jobs": []})
        self.assertEqual(lease_response([], {}), lease_response([]))
        command = {"request_id": REFRESH_ID, "account_set_key": "@endpoint", "scope_kind": "endpoint"}
        jobs = [job(ID, record())]
        response = lease_response(jobs, command)
        self.assertEqual(set(response), {"ok", "lease_seconds", "jobs", "master_refresh"})
        response["jobs"][0]["payload"]["items"].clear()
        response["master_refresh"]["account_set_key"] = "edited"
        self.assertEqual(len(jobs[0]["payload"]["items"]), 1)
        self.assertEqual(command["account_set_key"], "@endpoint")

    def test_lease_response_cannot_leak_native_projection_or_extra_refresh_fields(self):
        self.assert_invalid(lambda: lease_response([record()]), response=True)
        self.assert_invalid(lambda: lease_response([{**job(ID, record()), "attempt_token": "secret"}]), response=True)
        command = {"request_id": REFRESH_ID, "account_set_key": "@endpoint", "scope_kind": "endpoint"}
        for changed in ({"attempt_token": "secret"}, {"scope_kind": "account_set"}, {"account_set_key": ""}):
            self.assert_invalid(lambda: lease_response([], {**command, **changed}), response=True)
        self.assert_invalid(lambda: lease_response([job(ID, record())] * 51), response=True)

    def test_ack_success_and_success_replay_are_distinct_wire_responses(self):
        self.assertEqual(ack_response(receipt()), {"ok": True, "status": "success", "express_docnum": "RR-1"})
        self.assertEqual(ack_response(receipt(replayed=True)), {"ok": True, "status": "success", "idempotent": True})

    def test_ack_waiting_manual_and_failure_wire_results(self):
        self.assertEqual(ack_response(receipt("waiting_lock", "pending")),
                         {"ok": True, "status": "pending", "stage": "waiting_lock", "retry": True})
        for stage in ("needs_mapping", "needs_review"):
            self.assertEqual(ack_response(receipt(stage, "manual")), {"ok": True, "status": "manual", "stage": stage})
        for stage in ("failed", "rolled_back"):
            for attempt, status in ((2, "pending"), (3, "pending"), (4, "manual")):
                value = receipt(stage, status, wire_attempt=attempt)
                expected = {"ok": True, "status": status, "stage": stage, "attempt": attempt, "agent_failures": attempt - 1}
                self.assertEqual(ack_response(value), expected)
                self.assertEqual(ack_response({**value, "replayed": True}), expected)

    def test_unknown_or_inconsistent_receipt_is_never_serialized_as_ack(self):
        for value in (receipt("writing", "leased"), receipt("success", "manual"),
                      receipt("waiting_lock", "manual"), receipt("needs_review", "pending"),
                      receipt("failed", "success"), receipt(result_unknown=True),
                      receipt(response_body=None), receipt(response_body={"meta": {}}),
                      receipt("failed", "pending", wire_attempt="2")):
            self.assert_invalid(lambda: ack_response(value), response=True)

    def test_quarantined_finished_attempt_replay_returns_only_manual_ack(self):
        for stage in ("waiting_lock", "failed", "rolled_back"):
            for reason in ("push.native_history_changed", "push.native_target_changed",
                           "push.native_target_mismatch", "companion.queue_access_revoked"):
                with self.subTest(stage=stage, reason=reason):
                    value = receipt(stage, "manual", replayed=True, reason=reason,
                                    frozen_payload={"private": "payload"}, error="private error")
                    before = deepcopy(value)
                    self.assertEqual(ack_response(value), {"ok": True, "status": "manual", "idempotent": True})
                    self.assertEqual(value, before)

    def test_unknown_result_still_rejects_quarantined_replay(self):
        for stage in ("waiting_lock", "failed", "rolled_back"):
            for reason in ("push.native_history_changed", "push.native_target_changed",
                           "push.native_target_mismatch", "companion.queue_access_revoked"):
                value = receipt(stage, "manual", replayed=True, reason=reason, result_unknown=True)
                self.assert_invalid(lambda: ack_response(value), response=True)

    def test_quarantine_extension_does_not_expand_other_ack_states(self):
        base = receipt("waiting_lock", "manual", replayed=True, reason="push.native_history_changed")
        for changes in ({"replayed": False}, {"replayed": 1}, {"reason": "push.native_history_changed "},
                        {"reason": "push.native_other"}, {"reason": None}, {"status": "leased"}):
            self.assert_invalid(lambda: ack_response({**base, **changes}), response=True)
        for stage in ("writing", "leased", "success", "unknown"):
            value = {**base, "response_body": {"meta": {"stage": stage}}}
            self.assert_invalid(lambda: ack_response(value), response=True)
        # Other valid receipts retain their original wire contract; matching
        # reason alone must not turn a new ACK or another stage into a replay.
        for stage in ("failed", "rolled_back"):
            expected = {"ok": True, "status": "manual", "stage": stage, "attempt": 4, "agent_failures": 3}
            self.assertEqual(ack_response(receipt(stage, "manual", wire_attempt=4, replayed=False,
                                                  reason="push.native_history_changed")), expected)
            self.assertEqual(ack_response(receipt(stage, "manual", wire_attempt=4, replayed=True,
                                                  reason="agent_failed")), expected)
        self.assertEqual(ack_response(receipt("needs_review", "manual", replayed=True,
                                              reason="push.native_history_changed")),
                         {"ok": True, "status": "manual", "stage": "needs_review"})


if __name__ == "__main__":
    unittest.main()
