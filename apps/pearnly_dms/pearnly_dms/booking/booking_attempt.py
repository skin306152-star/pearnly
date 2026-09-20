# -*- coding: utf-8 -*-
"""跨过写边界**之前**把"我要提交这张订车草稿"登记下来（防重复建单）。

照搬老产品 `services/line_dms/booking_attempt.py`（线上基准 `501589f0`，逻辑未改）。
契约（四条怎么定、为什么）写在 [docs/p4/43](../../../docs/p4/43-booking-attempt-contract.md)。

它只能防住一件事，但那一件事的代价很大：**同一个执行体因为超时/重发再次提交，
在 DMS 里写成两张业务单据**。所以：

· 登记成功 = **可以**写 DMS；登记失败 = **绝不写**（宁可少写一张，不可多写一张）。
· 结果未知（提交了但读不回）时，话术要求用户**不要重发**，并把号报给管理员 ——
  `ERR_DMS_BOOKING_OUTCOME_UNKNOWN`。

⚠️ 这是 LINE 层：它用会话（`pearnly_core.line_session`）判断"这张草稿还是不是当初那张"，
并把结果说成用户看得懂的话。真正的登记判据在会话层（Redis 锁 + 锁内读改写），
不要在这里另写一套。
"""

from __future__ import annotations

import secrets
from typing import Any, Dict, Optional

from pearnly_core import line_session as store
from pearnly_dms.line import errors as dms_errors
from pearnly_dms.contracts.client_base import DmsClientError

UNKNOWN = "ERR_DMS_BOOKING_OUTCOME_UNKNOWN"


def unknown_result(booking_no) -> Dict[str, Any]:
    """"提交了但读不回结果"的统一说法：**不可重发**，把号交给管理员核。"""
    return {
        "ok": False,
        "booking_no": str(booking_no or ""),
        "error_code": UNKNOWN,
        "error_friendly": dms_errors.friendly(UNKNOWN),
        "response_body": {
            "submitted": True,
            "retry_safe": False,
            "submission_status": "unknown",
        },
    }


def pending(tenant, line_user_id, payload) -> Optional[Dict[str, Any]]:
    """这张草稿**已经登记过且号一样** → 直接回"结果未知"，不再提交一次。

    用户重复点确认时走的就是这条路：同一个 nonce 的第二次点击不会重新提交。
    """
    current = (store.get_session(line_user_id) or {}).get("payload") or {}
    marker = current.get("booking_attempt") or {}
    if marker and marker.get("nonce") == (payload or {}).get("nonce"):
        return unknown_result(marker.get("booking_no"))
    return None


def recorder(tenant, line_user_id, payload, *, binding=None, endpoint="", endpoint_fingerprint=""):
    """造一个"只能记一次"的登记器（每次执行一个 owner）。

    同一个执行体在**顺号重试**时可以再记一次（DMS 说这个号重复了，换下一个号）——
    判据在会话层：只有 `owner` 与上一个号都对得上才放行。
    """
    owner = secrets.token_hex(16)
    previous_no = None

    def assert_current():
        current = store.get_session(line_user_id) or {}
        draft = current.get("payload") or {}
        marker = draft.get("booking_attempt") or {}
        if (current.get("state") != "booking_review" or draft.get("nonce") is not None
                or draft.get("booking_claim") != payload.get("nonce")
                or (marker and (marker.get("owner") != owner or marker.get("booking_no") != previous_no))):
            raise DmsClientError('Booking draft is no longer owned by this worker',
                                 'ERR_DMS_BOOKING_ATTEMPT_BLOCKED')
        if binding:
            from pearnly_dms.line.identity import matches
            if not matches(binding, line_user_id):
                raise DmsClientError('Operator binding changed before submission',
                                     'ERR_DMS_BOOKING_ATTEMPT_BLOCKED')

    def record(booking_no, submitted=None):
        nonlocal previous_no
        assert_current()
        if not store.record_booking_attempt(
            line_user_id, (payload or {}).get("nonce"), str(booking_no), owner, previous_no,
            context={"binding": binding or {}, "endpoint": endpoint,
                     "endpoint_fingerprint": endpoint_fingerprint,
                     "submitted": submitted or {}, "qa": (payload or {}).get("qa") or {}},
        ):
            raise DmsClientError(
                "booking draft was replaced or attempt already exists; not submitted",
                "ERR_DMS_BOOKING_ATTEMPT_BLOCKED",
            )
        previous_no = str(booking_no)

    # Plain one-argument callbacks retain their public contract. Production's
    # recorder additionally saves the exact readback contract before external IO.
    record.capture_submission = record
    record.assert_current = assert_current
    return record


def clear_completed(tenant, line_user_id, payload) -> bool:
    """提交收尾：只清**自己认领的那张**草稿（别人的草稿不动）。"""
    return store.clear_booking_attempt(line_user_id, (payload or {}).get("nonce"))


def message(result) -> str:
    """"结果未知"要说给用户听的那句话；有单号就把单号带上。"""
    text = dms_errors.friendly(UNKNOWN)["th"]
    number = str((result or {}).get("booking_no") or "")
    return f"{text}\nเลขที่รายการ: {number}" if number else text


def review_message(session, fallback) -> str:
    """会话里若留着登记标记（说明提交过、结果未知），待确认态就别再说"请确认"了。"""
    marker = ((session or {}).get("payload") or {}).get("booking_attempt")
    return message(marker) if marker else fallback
