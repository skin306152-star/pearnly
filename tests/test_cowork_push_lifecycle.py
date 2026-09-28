"""Queue contracts only. These tests do not install a queue or acknowledge ERP."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from pearnly_cowork.push import lifecycle as flow, receipts, retry

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def row(**updates):
    result = {"id": "L1", "tenant_id": "T1", "history_tenant_id": "T1", "user_id": "U1",
              "endpoint_id": "E1", "history_id": "H1", "status": "pending", "attempt": 1,
              "lease_owner": None, "lease_expires_at": None, "created_at": NOW,
              "invoice_no": "INV/1", "seller_name": "supplier", "request_body": {"account_set": "BOOK"}}
    result.update(updates)
    return result


class QueueTests(unittest.TestCase):
    def lease(self, value=None, **updates):
        params = dict(branch="legacy", endpoint_id="E1", binding_generation=0,
                      owner="worker", now=NOW, allowed_accounts=["book"])
        params.update(updates)
        return flow.lease_decision(value or row(), **params)

    def test_enqueue_result_is_pending_never_success(self):
        value = flow.reserved_result(adapter="express", result={"success": False,
            "error_msg": "EXPRESS_QUEUED", "http_status": 202})
        self.assertEqual(value, {"status": "pending", "error_msg": "EXPRESS_QUEUED", "retry_delay_sec": None})
        self.assertTrue(retry.counts_as_endpoint_success("pending"))  # old non-failure statistic only

    def test_waiting_lock_has_real_branch_difference(self):
        old = flow.acknowledgement(branch="legacy", attempt=1, success=False, outcome="waiting_lock")
        managed = flow.acknowledgement(branch="managed", attempt=1, success=False, outcome="waiting_lock")
        self.assertEqual((old.status, old.attempt, old.retry), ("pending", 1, True))
        self.assertEqual((managed.status, managed.attempt, managed.retry), ("pending", 2, True))

    def test_zero_attempt_legacy_normalizes_only_on_failure(self):
        for outcome in ("success", "waiting_lock", "needs_review"):
            self.assertEqual(flow.acknowledgement(branch="legacy", attempt=0, success=False, outcome=outcome).attempt, 0)
        self.assertEqual(flow.acknowledgement(branch="legacy", attempt=0, success=False).attempt, 2)
        self.assertEqual(flow.acknowledgement(branch="managed", attempt=0, success=False).attempt, 1)

    def test_explicit_outcome_beats_success_boolean(self):
        for branch in ("legacy", "managed"):
            blocked = flow.acknowledgement(branch=branch, attempt=1, success=True, outcome="needs_mapping")
            self.assertEqual((blocked.status, blocked.stage), ("manual", "needs_mapping"))
            confirmed = flow.acknowledgement(branch=branch, attempt=1, success=False, outcome="success")
            self.assertEqual(confirmed.status, "success")

    def test_failures_exhaust_and_rollback_is_not_success(self):
        for branch in ("legacy", "managed"):
            for attempt in (1, 2, 3, 4):
                for outcome in (None, "failed", "rolled_back", "unknown"):
                    state = flow.acknowledgement(branch=branch, attempt=attempt, success=False, outcome=outcome)
                    self.assertEqual(state.attempt, attempt + 1)
                    self.assertEqual(state.status, "pending" if attempt < 3 else "manual")
                    self.assertEqual(state.retry, attempt < 3)

    def test_a_rejection_retrying_cannot_change_goes_to_a_person(self):
        # 2026-09-28 压测：同税号两个客户档先按普通失败重试 3 次，列表一直「录入中」
        for branch in ("legacy", "managed"):
            state = flow.acknowledgement(branch=branch, attempt=1, success=False,
                                         error="[CUSTOMER_BRANCH_AMBIGUOUS] ARMAS tax 0105554131661 matches 2 parties")
            self.assertEqual((state.status, state.stage, state.retry), ("manual", "needs_review", False))
            self.assertTrue(state.reason.startswith("[CUSTOMER_BRANCH_AMBIGUOUS]"))
            ordinary = flow.acknowledgement(branch=branch, attempt=1, success=False, error="[DBF_WRITE_FAILED] io")
            self.assertEqual((ordinary.status, ordinary.retry), ("pending", True))
            waiting = flow.acknowledgement(branch=branch, attempt=1, success=False, outcome="waiting_lock",
                                           error="[CUSTOMER_BRANCH_AMBIGUOUS] x")
            self.assertEqual(waiting.status, "pending")  # 小助手自己给了去向的照它
            # 这台电脑没有这份账套 → 要人处理；暂时连不上的小助手回报「等账套」，不烧重试次数（2026-09-28 录制）
            missing = flow.acknowledgement(branch=branch, attempt=1, success=False,
                                           error="[NO_ACCOUNT_SELECTION] task account is not registered")
            self.assertEqual((missing.status, missing.stage), ("manual", "needs_review"))
            unreachable = flow.acknowledgement(branch=branch, attempt=3, success=False, outcome="waiting_lock",
                                               error="[ACCOUNT_SET_UNREACHABLE] the account set cannot be reached")
            self.assertEqual((unreachable.status, unreachable.retry), ("pending", True))
        self.assertEqual(flow.error_code("  [FIELD_TOO_LONG] REFNUM"), "FIELD_TOO_LONG")
        self.assertEqual(flow.error_code("items_mismatch"), "")

    def test_duplicate_confirmation_matches_sql_truthiness(self):
        for value in (None, False, 0, "false", "FALSE", "null", "", "off", "no"):
            self.assertFalse(flow.duplicate_confirmed(value), value)
        for value in (True, "true", 1, "yes", " false ", [], {}, "unexpected", 0.0):
            self.assertTrue(flow.duplicate_confirmed(value), value)

    def test_lease_is_scoped_by_endpoint_account_and_internal_source(self):
        self.assertIsNotNone(self.lease())
        self.assertIsNone(self.lease(row(endpoint_id="E2")))
        self.assertIsNone(self.lease(row(request_body={"account_set": "other"})))
        for source in ("erp_web", "line_erp"):
            self.assertIsNone(self.lease(history_source=source))
        self.assertIsNone(self.lease(allowed_accounts=[]))
        self.assertIsNone(self.lease(binding_generation=1))

    def test_sql_coalesce_does_not_fall_through_empty_top_account(self):
        self.assertIsNone(self.lease(row(request_body={"account_set": "", "meta": {"account_set": "BOOK"}})))
        self.assertIsNotNone(self.lease(row(request_body={"account_set": None, "meta": {"account_set": "BOOK"}})))

    def test_sql_spaces_are_not_python_whitespace_or_casefold(self):
        self.assertIsNotNone(self.lease(row(request_body={"account_set": " BOOK "})))
        self.assertIsNone(self.lease(row(request_body={"account_set": "\tBOOK\t"})))
        self.assertIsNone(self.lease(row(request_body={"account_set": "ß"}), allowed_accounts=["SS"]))
        self.assertEqual(receipts.reservation_account(row(request_body={"account_set": "\tBOOK\t"})), "\tbook\t")
        self.assertEqual(receipts.reservation_account(row(request_body={"account_set": "ß"})), "ß")

    def test_lease_deadline_boundary_and_plain_reclaim(self):
        for seconds, eligible in ((-1, True), (0, False), (1, False)):
            value = row(lease_owner="prior", lease_expires_at=NOW + timedelta(seconds=seconds))
            self.assertEqual(self.lease(value) is not None, eligible)
        self.assertIsNotNone(self.lease(row(lease_owner="prior")))

    def test_confirmed_first_lease_then_expiration_is_manual_not_replay(self):
        value = row(request_body={"account_set": "BOOK", "duplicate_confirmed": True})
        value.update(self.lease(value))
        later = NOW + timedelta(seconds=121)
        self.assertIsNone(self.lease(value, now=later))
        patch = flow.close_expired_legacy_confirmed(value, endpoint_id="E1", binding_generation=0, now=later)
        self.assertEqual((patch["status"], patch["reason"]), ("manual", "confirmed_push_unacked"))
        self.assertIsNone(patch["lease_owner"])
        self.assertIsNone(flow.close_expired_legacy_confirmed(value, endpoint_id="E1", binding_generation=1, now=later))

    def test_managed_reclaim_changes_attempt_and_old_ack_is_stale(self):
        value = row(request_body={"account_set": "BOOK", "meta": {"managed_generation": 4}},
                    lease_owner="worker", lease_expires_at=NOW - timedelta(seconds=1))
        patch = self.lease(value, branch="managed", binding_generation=4)
        self.assertEqual(patch["attempt"], 2)
        value.update(patch)
        args = dict(branch="managed", endpoint_id="E1", owner="worker", binding_generation=4)
        self.assertFalse(flow.ack_matches(value, **args, attempt=1))
        self.assertTrue(flow.ack_matches(value, **args, attempt=2))
        args["binding_generation"] = 5
        self.assertFalse(flow.ack_matches(value, **args, attempt=2))

    def test_managed_requires_generation_and_account_evidence(self):
        self.assertIsNone(self.lease(branch="managed", binding_generation=4))
        value = row(request_body={"account_set": "BOOK", "managed_generation": 4})
        with self.assertRaises(flow.LifecycleError):
            self.lease(value, branch="managed", binding_generation=4, allowed_accounts=None)

    def test_legacy_ack_limitation_is_visible(self):
        value = row(status="success")
        self.assertTrue(flow.ack_matches(value, branch="legacy", endpoint_id="E1", owner="later"))
        self.assertFalse(flow.ack_matches(value, branch="legacy", endpoint_id="E2", owner="later"))
        # Legacy wire has no attempt fence: this contract is not native auth.
        value = row(lease_owner="same-worker", attempt=2)
        self.assertTrue(flow.ack_matches(value, branch="legacy", endpoint_id="E1", owner="same-worker", attempt=1))

    def test_terminal_duplicate_ack_never_reverts_success(self):
        decision = flow.owned_ack_decision(row(status="success"), branch="legacy",
            endpoint_id="E1", owner="later", success=False, outcome="failed")
        self.assertEqual(decision, {"accepted": True, "idempotent": True, "transition": None})

    def test_taken_http_reservation_cannot_auto_replay(self):
        value = row(status="retrying", lease_owner=flow.CONFIRMED_OWNER, lease_expires_at=NOW)
        self.assertEqual(flow.recover_reserved(value, now=NOW)["error_msg"], flow.UNKNOWN_RESULT)
        self.assertIsNone(flow.recover_reserved({**value, "lease_expires_at": NOW + timedelta(seconds=1)}, now=NOW))

    def test_unknown_mrerp_technical_failure_is_manual(self):
        for result in (None, {"success": False, "error_msg": "timeout"}):
            self.assertEqual(flow.reserved_result(adapter="mrerp", result=result)["status"], "manual")
        for code in ("ERR_NO_INVOICE_NO", "ERR_NO_CLIENT"):
            self.assertEqual(flow.reserved_result(adapter="mrerp", result={"success": False, "error_msg": code})["status"], "failed")
        self.assertEqual(flow.reserved_result(adapter="mrerp", result={"success": False, "error_msg": "ERR_DUPLICATE_INVOICE"})["status"], "skipped_dup")

    def test_express_retry_policy_rejects_data_errors(self):
        self.assertEqual(flow.reserved_result(adapter="express", result={"success": False, "error_msg": "timeout"})["retry_delay_sec"], 60)
        self.assertIsNone(flow.reserved_result(adapter="express", result={"success": False, "error_msg": "ERR_NO_CLIENT"})["retry_delay_sec"])

    def test_uncertain_finalize_only_uses_durable_unleased_results(self):
        for value in (None, {"status": "retrying"}, {"status": "success", "lease_owner": "writer"}):
            self.assertEqual(flow.recover_finalize_result(value)["error_msg"], flow.UNKNOWN_RESULT)
        self.assertEqual(flow.recover_finalize_result({"status": "success"})["status"], "success")
        self.assertTrue(flow.recover_finalize_result({"status": "failed", "next_retry_at": NOW})["retry_scheduled"])

    def test_freeze_survives_adapter_override_without_aliasing(self):
        reservation = {"source": "cowork_line", "account_set": "BOOK", "target_intent": {"endpoint_id": "E1"}}
        adapter = {"source": "erp_web", "account_set": "other", "target_intent": {"endpoint_id": "E2"}}
        result = flow.frozen_request(reservation, adapter)
        self.assertEqual(result, reservation)
        result["target_intent"]["endpoint_id"] = "changed"
        self.assertEqual(reservation["target_intent"]["endpoint_id"], "E1")
        self.assertEqual(adapter["account_set"], "other")

    def test_naive_clock_and_bad_branch_are_rejected(self):
        with self.assertRaises(flow.LifecycleError):
            self.lease(now=NOW.replace(tzinfo=None))
        with self.assertRaises(flow.LifecycleError):
            self.lease(branch="guess")


class ReceiptTests(unittest.TestCase):
    def decision(self, rows, **kwargs):
        args = dict(tenant_id="T1", endpoint_id="E1", history_id="H1", account_set="book")
        args.update(kwargs)
        return receipts.reservation_decision(rows, **args)

    def test_latest_display_failure_never_hides_dedup_success(self):
        rows = [row(status="success"), row(id="L2", status="failed", created_at=NOW + timedelta(seconds=1))]
        self.assertEqual(receipts.current_receipts(rows, status_filter="success"), [])
        self.assertEqual(self.decision(rows)["reason"], "already_success")
        self.assertEqual(receipts.legacy_success(rows, history_id="H1", endpoint_id="E1", user_id="U1", account_set="BOOK")["id"], "L1")

    def test_account_endpoint_tenant_and_history_cannot_cross(self):
        value = row(status="success")
        for update in ({"tenant_id": "T2"}, {"endpoint_id": "E2"}, {"history_id": "H2"},
                       {"request_body": {"account_set": "OTHER"}}):
            self.assertTrue(self.decision([{**value, **update}])["dispatch"])
        self.assertFalse(self.decision([value])["dispatch"])

    def test_unknown_and_active_block_but_final_data_failure_can_be_corrected(self):
        self.assertEqual(self.decision([row(status="manual", error_msg=flow.UNKNOWN_RESULT)])["reason"], "unknown")
        for value in (row(), row(status="failed", next_retry_at=NOW), row(status="manual", lease_owner="w")):
            self.assertEqual(self.decision([value])["reason"], "active")
        self.assertTrue(self.decision([row(status="failed")])["dispatch"])

    def test_orphans_are_independent_and_status_filter_follows_collapse(self):
        values = [row(id="a", history_id=None), row(id="b", history_id=None),
                  row(id="c", status="failed", next_retry_at=NOW), row(id="d", endpoint_id="E2", status="success")]
        self.assertEqual(len(receipts.current_receipts(values)), 4)
        self.assertEqual([r["id"] for r in receipts.current_receipts(values, status_filter="retrying")], ["c"])
        self.assertEqual([r["id"] for r in receipts.current_receipts(values, status_filter="success")], ["d"])

    def test_lookup_failure_is_not_an_empty_success(self):
        with self.assertRaises(receipts.ReceiptError):
            self.decision(None)
        with self.assertRaises(receipts.ReceiptError):
            receipts.legacy_success(None, history_id="H1", endpoint_id="E1", user_id="U1")

    def test_legacy_number_fallback_needs_same_seller_and_exact_account(self):
        values = [row(status="success")]
        args = dict(history_id="new", endpoint_id="E1", user_id="U1", invoice_no="INV/1", seller_name="supplier", account_set="BOOK")
        self.assertIsNotNone(receipts.legacy_success(values, **args))
        self.assertIsNone(receipts.legacy_success(values, **{**args, "seller_name": "other"}))
        self.assertIsNone(receipts.legacy_success(values, **{**args, "account_set": "book"}))

    def test_original_number_is_from_successful_same_target_history_join(self):
        values = [row(tenant_id=None, status="success", response_body={"express_docnum": "IV69001"}),
                  row(id="other", tenant_id=None, status="success", endpoint_id="E2", response_body={"express_docnum": "WRONG"})]
        args = dict(tenant_id="T1", endpoint_id="E1", history_id="H1", account_set="BOOK")
        self.assertEqual(receipts.prior_document(values, **args), "IV69001")
        self.assertIsNone(receipts.prior_document(values, **{**args, "account_set": "OTHER"}))
        with self.assertRaises(receipts.ReceiptError):
            receipts.prior_document(None, **args)

    def test_request_nested_shape_and_invalid_body(self):
        self.assertEqual(receipts.request_payload('{"payload":{"a":1}}'), {"a": 1})
        self.assertEqual(receipts.coerce_body('[1]'), {})
        self.assertEqual(receipts.derive_external_ref("express", {"express_docnum": " IV1 "})["external_doc_no"], "IV1")
        self.assertEqual(receipts.derive_external_ref("mrerp", '1')["external_doc_no"], "")


if __name__ == "__main__":
    unittest.main()
