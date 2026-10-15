"""Pure queue decisions from the deployed Express and confirmed-push writers.

Only the legacy branch (binding generation 0) is accepted. The old platform's
managed shared-agent branch was never enabled and is retired (D9): a managed
branch or a nonzero generation is rejected or never matches.

This is not an executor. Callers must authorize, lock the endpoint and record,
check current generation, and apply a decision with CAS in one transaction.
No lease/ACK RPC or writer is registered by this module.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re

from pearnly_cowork.contracts.push import INTERNAL_SOURCES
from pearnly_cowork.push.express import common
from pearnly_cowork.push.retry import classify_push_status, get_erp_retry_delay_sec, is_user_data_error

LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
CONFIRMED_OWNER = "confirmed:legacy"
UNKNOWN_RESULT = "ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"
# 小助手回报的这些拒绝，重试多少次结论都一样（要人改票面、选对方档或核对账套）：直接转人工，不烧重试。
# 有意变化（旧线上按普通失败重试 3 次；2026-09-28 压测：同税号两个客户档的票在列表上一直是「录入中」）。
# 回报格式 `[错误码] 原始信息`（小助手 `queue_client._ack`）；老版本小助手没登记 outcome 的也按码认。
NEEDS_PERSON_CODES = frozenset({
    "CUSTOMER_BRANCH_AMBIGUOUS", "SUPPLIER_BRANCH_AMBIGUOUS", "CUSTOMER_DUP_SUSPECTED", "SUPPLIER_DUP_SUSPECTED",
    "FIELD_TOO_LONG", "PAYLOAD_INVALID", "ACCOUNT_DIR_NOT_ALLOWED", "ACCOUNT_COMPANY_MISMATCH",
    "DUPLICATE_CONTENT_DIFFERS", "PRIOR_DOC_STILL_IN_ERP",
    # 这台电脑的小助手里没有这份账套 / 文件夹在但不可写：重试结论一样。暂时连不上的，小助手 1.3.2 起回报「等账套」。
    "NO_ACCOUNT_SELECTION",
    # POS 日汇总票的付款方式转账凭证（载荷 3）：账套没有可用的普通日记账单据簿 / 选的收款科目在 Express 里不能记账 /
    # `pos` 块小助手不收。
    "JOURNAL_BOOK_NOT_FOUND", "ACCOUNT_NOT_POSTABLE", "POS_INVALID",
})
_ERROR_CODE = re.compile(r"^\[([A-Z0-9_]+)\]")


def error_code(error):
    """小助手回报原因里的错误码（`[CODE] …`）；没有就是空串。"""
    match = _ERROR_CODE.match(str(error or "").strip())
    return match.group(1) if match else ""
CONFIRMED_UNACKED = "confirmed_push_unacked"


class LifecycleError(ValueError):
    pass


@dataclass(frozen=True)
class Transition:
    status: str
    attempt: int
    stage: str
    reason: str = ""
    retry: bool = False


def _branch(branch):
    if branch != "legacy":
        raise LifecycleError("push.branch_invalid")


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LifecycleError("push.utc_required")
    return value.astimezone(timezone.utc)


def _body(row):
    body = row.get("request_body")
    return body if isinstance(body, dict) else {}


def account_key(row):
    """Postgres COALESCE semantics: an explicit empty top-level key stays empty."""
    body = _body(row)
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    value = body.get("account_set")
    if value is None:
        value = meta.get("account_set")
    # SQL btrim removes ordinary spaces, not tabs/newlines. Its lower is not
    # Python casefold (e.g. ß must not match SS). Allowed input normalization
    # below intentionally follows the separate source Python helper.
    return str(value if value is not None else "").strip(" ").lower()


def duplicate_confirmed(value):
    """Match SQL JSON ->> and lower(), deliberately NOT Python bool()/strip()."""
    if value is None:
        value = "false"
    elif not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value.lower() not in {"false", "0", "", "null", "no", "off"}


def acknowledgement(*, branch, attempt, success, outcome=None, error=None):
    """State effects after a valid owned ACK. Transport/lease auth is separate.

    Legacy waiting_lock leaves the wire attempt unchanged; the same counter
    drives failure exhaustion.
    """
    _branch(branch)
    if type(attempt) is not int or attempt < 0:
        raise LifecycleError("push.attempt_invalid")
    stage = outcome if outcome in common.ACK_OUTCOMES else (
        common.STAGE_SUCCESS if success else common.STAGE_FAILED)
    if stage == common.STAGE_SUCCESS:
        return Transition("success", attempt, stage)
    if stage == common.STAGE_WAITING_LOCK:
        return Transition("pending", attempt, stage, retry=True)
    if stage in {common.STAGE_NEEDS_MAPPING, common.STAGE_NEEDS_REVIEW}:
        return Transition("manual", attempt, stage, str(error or "")[:500] or stage)
    if stage == common.STAGE_FAILED and error_code(error) in NEEDS_PERSON_CODES:
        return Transition("manual", attempt, common.STAGE_NEEDS_REVIEW, str(error)[:500])
    # Legacy applies `int(log.attempt or 1)` only on the failure branch;
    # success/waiting/manual leave the stored attempt unchanged, including zero.
    attempt = attempt or 1
    status = "manual" if attempt >= MAX_ATTEMPTS else "pending"
    reason = (str(error or "")[:500] or "agent_failed")
    return Transition(status, attempt + 1, stage, reason, status == "pending")


def lease_decision(row, *, branch, endpoint_id, binding_generation, owner,
                   now, allowed_accounts=None, history_source=None):
    """Return a detached patch for one eligible locked row, else None.

    Input contains server-loaded row/history values. It is never client input.
    A nonzero binding generation never leases.
    The caller must persist the returned lease before emitting the payload.
    """
    _branch(branch)
    now = _utc(now)
    if not owner or not endpoint_id:
        raise LifecycleError("push.lease_identity_required")
    if binding_generation != 0:
        return None
    if row.get("endpoint_id") != endpoint_id or row.get("status") != "pending":
        return None
    if history_source in INTERNAL_SOURCES:
        return None
    if allowed_accounts is not None:
        allowed = {str(v or "").strip().casefold() for v in allowed_accounts if str(v or "").strip()}
        if account_key(row) not in allowed:
            return None
    was_leased = row.get("lease_owner") is not None
    if was_leased:
        if duplicate_confirmed(_body(row).get("duplicate_confirmed")):
            return None
        expires = row.get("lease_expires_at")
        if expires is not None and _utc(expires) >= now:
            return None
    return {"lease_owner": owner, "lease_expires_at": now + timedelta(seconds=LEASE_SECONDS)}


def close_expired_legacy_confirmed(row, *, endpoint_id, binding_generation, now):
    """The deployed legacy sweep is at-most-once only for duplicate-confirmed jobs."""
    now = _utc(now)
    if binding_generation != 0 or row.get("endpoint_id") != endpoint_id:
        return None
    if row.get("status") != "pending" or row.get("lease_owner") is None:
        return None
    if not duplicate_confirmed(_body(row).get("duplicate_confirmed")):
        return None
    expires = row.get("lease_expires_at")
    if expires is not None and _utc(expires) >= now:
        return None
    return {"status": "manual", "error_msg": "EXPRESS_MANUAL: " + CONFIRMED_UNACKED,
            "lease_owner": None, "lease_expires_at": None,
            "stage": common.STAGE_NEEDS_REVIEW, "reason": CONFIRMED_UNACKED}


def ack_matches(row, *, branch, endpoint_id, owner, attempt=None, binding_generation=0):
    """Contract predicate, not token authentication. No expiry check in old ACKs.

    Old legacy acknowledges a terminal success idempotently before owner check
    and has no attempt fence (`attempt` is accepted and ignored). Native service
    must add an unforgeable attempt token for legacy workers.
    """
    _branch(branch)
    if row.get("endpoint_id") != endpoint_id or not owner:
        return False
    return binding_generation == 0 and (row.get("status") == "success"
        or (row.get("lease_owner") or "") == owner)


def owned_ack_decision(row, *, branch, endpoint_id, owner, success, outcome=None,
                       error=None, attempt=None, binding_generation=0):
    """Compose matching and terminal idempotency before any transition decision.

    None transition means no mutation: already-success legacy ACKs may only
    return the saved receipt, even if a duplicate callback now reports failure.
    This still requires token authorization and transactional CAS from a caller.
    """
    if not ack_matches(row, branch=branch, endpoint_id=endpoint_id, owner=owner,
                       attempt=attempt, binding_generation=binding_generation):
        return {"accepted": False, "idempotent": False, "transition": None}
    if row.get("status") == "success":
        return {"accepted": True, "idempotent": True, "transition": None}
    transition = acknowledgement(branch=branch, attempt=row["attempt"],
                                  success=success, outcome=outcome, error=error)
    return {"accepted": True, "idempotent": False, "transition": transition}


def recover_reserved(row, *, now):
    """A taken HTTP reservation may have written: expiration never requeues it."""
    now = _utc(now)
    expires = row.get("lease_expires_at")
    if (row.get("status") != "retrying" or row.get("lease_owner") != CONFIRMED_OWNER
            or expires is None or _utc(expires) > now):
        return None
    return {"status": "manual", "http_status": None, "error_msg": UNKNOWN_RESULT,
            "next_retry_at": None, "lease_owner": None, "lease_expires_at": None}


def reserved_result(*, adapter, result):
    """Result classification of confirmed dispatch; None means uncertain exception.

    No MRERP success is strengthened to independent readback: source raw '1'
    can be success without a report. Unknown MRERP technical failures go manual.
    """
    if adapter not in {"express", "mrerp"}:
        raise LifecycleError("push.adapter_invalid")
    if result is None:
        return {"status": "manual", "error_msg": UNKNOWN_RESULT, "retry_delay_sec": None}
    status = classify_push_status(bool(result.get("success")), result.get("error_msg"))
    if adapter == "mrerp" and status == "failed" and not is_user_data_error(result.get("error_msg")):
        return {"status": "manual", "error_msg": UNKNOWN_RESULT, "retry_delay_sec": None}
    delay = (get_erp_retry_delay_sec(0) if adapter == "express" and status == "failed"
             and not is_user_data_error(result.get("error_msg")) else None)
    return {"status": status, "error_msg": result.get("error_msg"), "retry_delay_sec": delay}


def recover_finalize_result(persisted):
    """A lost finalize acknowledgement requires durable readback, never guessing.

    Input must come from the same tenant/endpoint/history reservation lookup.
    A read error, still-leased row or unrecognized status remains unknown/manual.
    """
    known = {"success", "skipped_dup", "failed", "pending", "manual"}
    if (not isinstance(persisted, dict) or persisted.get("status") not in known
            or persisted.get("lease_owner")):
        return {"status": "manual", "error_msg": UNKNOWN_RESULT, "retry_scheduled": False}
    return {"status": persisted["status"], "error_msg": persisted.get("error_msg"),
            "retry_scheduled": persisted["status"] == "failed" and persisted.get("next_retry_at") is not None}


def frozen_request(reservation, request_body):
    """Final adapter output cannot replace the server's previously reserved target."""
    body = deepcopy(request_body) if isinstance(request_body, dict) else {}
    body["source"] = reservation["source"]
    body["account_set"] = reservation["account_set"]
    body["target_intent"] = deepcopy(reservation["target_intent"])
    return body
