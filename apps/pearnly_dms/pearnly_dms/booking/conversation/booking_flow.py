# -*- coding: utf-8 -*-
"""预览卡的确认 / 取消 / 重试：真正把订车单写进 DMS 的那一层。

照搬老产品 `services/line_dms/booking_flow.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。它是"提交登记"（`dms_booking_attempt`）的**唯一调用方**，顺序不能变：

    确认按钮 → 消费 nonce → 后台执行体
      ① 先查"这张草稿是不是已经登记过"（登记过就**不再提交**，回"结果未知"）
      ② 解端点 → 下附件 → 建单（**提交前登记**，登记失败就不写）
      ③ 成功 → 台账 + 回执 + 清理草稿；失败 → 按能不能重试分流

几条不能动的规矩：

· **确认是一次性的**：nonce 被 `consume_nonce` 原子消费，同一张卡的第二次点击必然
  mismatch（防双击双建单）。
· **取消**不清会话里的登记标记 —— 登记过就说明请求可能已经发出去了，这时说"已取消"
  是撒谎，要改说"结果未知、请让管理员按这个号核"。
· **重试只对"明确发生在建单之前"的阻塞开放**（主档暂时读不到、并发挤下线、
  客户查不到）。主档已变更、以及任何"已经写下去过"的结果都**不重放**。
· **建单成功但附件没挂全要如实补一句**，绝不谎报附件成功。

新平台的接口差异：async → 同步；后台任务走 `dms_out.enqueue`（webhook 立刻返回）；
多 OA 那层（`binding_guard`）去掉 —— 一个站点一条 OA，绑定随事件带进来。

还有三处**不是漏搬**：

· 线上的 `_spawn` / `_book_in_session` / `_card_payload` 在这边没有对应物：
  后台调度统一走 `dms_out.enqueue`；建单执行体早就是 `dms_booking.book_in_session`
  （本批只给它补了 `on_attempt` 透传）；而 `_card_payload` 在新平台是**死代码** ——
  建单层自己用 `card_from_customer` 从客户档造卡，线上那个辅助函数只喂给旧的
  `_book_in_session`，那个函数我们已经不走了。
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple

from pearnly_core import line_session as store
from pearnly_dms.booking import booking_attempt as booking_attempt
from pearnly_dms.booking import booking_ledger as booking_ledger
from pearnly_dms.booking import recovery
from pearnly_dms.booking.attachments import download as _download_attach_files
from pearnly_dms.contracts.endpoint import endpoint_name
from pearnly_dms.line import cards as cards
from pearnly_dms.booking.conversation import qa_cards as qa_cards
from pearnly_dms.line import out as _out
from pearnly_dms.booking.service import book_in_session
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.line.out import enqueue as _enqueue_shared
from pearnly_dms.line.out import send as _send
from pearnly_dms import i18n

logger = logging.getLogger(__name__)

BOOKING_ACTIONS = frozenset(
    {cards.ACT_CONFIRM_BOOKING, cards.ACT_CANCEL_BOOKING, cards.ACT_RETRY_BOOKING}
)
_RETRY_TTL_MINUTES = 30


def _push(line_user_id: str, text: str) -> None:
    _out.push_text(line_user_id, text)


def _reply(reply_token: str, text: str) -> None:
    _out.reply_text(reply_token, text)


def _enqueue(job: str, fn, *args) -> None:
    """后台执行体。入队失败不能静默（用户会一直等一张永远不来的卡）。"""
    try:
        _enqueue_shared(job, fn, *args)
    except Exception:
        logger.exception("[dms booking] 后台任务入队失败")
        raise


# ── 预览卡 postback：确认建单 / 取消 / 重试 ──────────────────────────────────
def handle_postback(
    binding: dict, line_user_id: str, reply_token: str, action: str, pb: dict
) -> None:
    """预览卡按钮：核对 `booking_review` 态 + nonce → 建单 / 取消 / 重试。"""
    tenant = binding["tenant_id"]
    if action == cards.ACT_CANCEL_BOOKING:
        session = store.get_session(line_user_id)
        marker = ((session or {}).get("payload") or {}).get("booking_attempt")
        if marker:
            # 登记过就别假装"已取消"：请求可能已经发出去了，如实让管理员按号核
            _reply(reply_token, booking_attempt.message(marker))
            return
        cancelled = store.cancel_review(line_user_id, "booking_review", pb.get("nonce"))
        _reply(reply_token, cards.TXT_BOOKING_CANCELLED if cancelled else cards.TXT_EXPIRED)
        return

    if action == cards.ACT_RETRY_BOOKING:
        payload = store.consume_nonce(line_user_id, "booking_review", pb.get("nonce"))
        if payload is None:
            _reply(reply_token, cards.TXT_EXPIRED)
            return
        _queue_claimed(binding, line_user_id, payload, reply_token)
        return

    # 确认守卫（`booking_review` 态 + nonce 吻合）**原子清 nonce** 并回 payload；
    # 不符 / 过期 → 过期话术、**绝不建单**。清掉之后同一 nonce 的二次点击此后必 mismatch。
    payload = store.consume_nonce(line_user_id, "booking_review", pb.get("nonce"))
    if payload is None:
        _reply(reply_token, cards.TXT_EXPIRED)
        return
    _queue_claimed(binding, line_user_id, payload, reply_token)


def _queue_claimed(binding, line_user_id, payload, reply_token=""):
    # 点「确认建单」那一下的令牌带进后台：这次点击本身什么都不回，建单结果、回执、
    # 失败提示因此能在一分钟内走免费回覆（以前一律计费推播）。
    try:
        _enqueue("booking", _execute_booking, binding, line_user_id, payload, reply_token)
    except Exception:
        # Even an ambiguous enqueue cannot authorize two writes: rotating the
        # claim revokes any old job before its customer/order write guard.
        nonce = secrets.token_hex(8)
        if _arm_retry(binding["tenant_id"], line_user_id, payload, nonce):
            _send(line_user_id, qa_cards.booking_retry_card(cards.TXT_SYSTEM_ERROR, nonce))


# ── 建订车单 ─────────────────────────────────────────────────────────────────
def _execute_booking(binding: dict, line_user_id: str, payload: dict,
                     reply_token: str = "") -> None:
    """令牌由 `run_guarded` 的 `line.batch` 使用：本任务发给本人的消息合成一次发送。"""
    try:
        _execute_owned_booking(binding, line_user_id, payload)
    except Exception:
        logger.exception("Booking worker interrupted; preserving submission boundary")
        # Safe only before the order-attempt marker; an unknown order is never replayed.
        retry_nonce = secrets.token_hex(8)
        if _arm_retry(binding["tenant_id"], line_user_id, payload, retry_nonce):
            _send(line_user_id, qa_cards.booking_retry_card(cards.TXT_SYSTEM_ERROR, retry_nonce))
        else:
            recovery.request(line_user_id)


def _execute_owned_booking(binding: dict, line_user_id: str, payload: dict) -> None:
    tenant, user_id = binding["tenant_id"], binding["user_id"]
    current = store.get_session(line_user_id) or {}
    claimed = current.get("payload") or {}
    if (current.get("state") != "booking_review" or claimed.get("nonce") is not None
            or claimed.get("booking_claim") != payload.get("nonce")):
        return
    qa = payload.get("qa") or {}
    pending = booking_attempt.pending(tenant, line_user_id, payload)
    if pending:
        # 这张草稿已经登记过（上次提交的结果还不知道）→ 不再提交第二次
        _push(line_user_id, booking_attempt.message(pending))
        return
    _out.start_loading(line_user_id)
    endpoint = resolve_dms_endpoint(user_id, qa.get("endpoint_id"))
    if not endpoint:
        raise ValueError("Booking endpoint is unavailable")
    attach_files, attach_failed = _download_attach_files(qa)
    result = book_in_session(
        endpoint,
        payload,
        attach_files,
        attach_failed,
        on_attempt=booking_attempt.recorder(
            tenant, line_user_id, payload, binding=binding, endpoint=endpoint_name(endpoint),
            endpoint_fingerprint=recovery.endpoint_fingerprint(endpoint),
        ),
    )
    if result.get("preflight"):
        _resume_after_master_change(binding, line_user_id, payload, result)
        return
    if (result.get("response_body") or {}).get("customer_conflict"):
        _resume_after_customer_change(line_user_id, payload, result)
        return
    qa = payload.get("qa") or {}
    booking_ledger.log_booking(endpoint=endpoint, payload=payload, result=result, actor=user_id)
    if result.get("ok"):
        recovery.finish(line_user_id, payload.get("nonce"), qa, result)
        return

    friendly = result.get("error_friendly") or {}
    if result.get("error_code") == booking_attempt.UNKNOWN:
        _push(line_user_id, booking_attempt.message(result))
        return
    if result.get("error_code") == "ERR_DMS_PAYMENT_INCOMPLETE":
        # 钱没填够属"可改"，回预览卡让人补齐（轮换 nonce → 新卡能再确认）
        nonce = secrets.token_hex(8)
        if not _arm_retry(tenant, line_user_id, payload, nonce):
            return
        _push(line_user_id, friendly.get(i18n.line_lang()) or cards.TXT_BOOKING_FAIL)
        _send(line_user_id, qa_cards.preview_card(qa, nonce))
        return
    if _retryable_result(result):
        retry_nonce = secrets.token_hex(8)
        if _arm_retry(tenant, line_user_id, payload, retry_nonce):
            _send(
                line_user_id,
                qa_cards.booking_retry_card(
                    friendly.get(i18n.line_lang()) or cards.TXT_BOOKING_FAIL,
                    retry_nonce,
                    code=result.get("error_code") or "",
                ),
            )
            return
    _push(line_user_id, friendly.get(i18n.line_lang()) or cards.TXT_BOOKING_FAIL)


def _resume_after_customer_change(line_user_id, payload, result):
    from pearnly_dms.intake.draft import master_snapshot
    from pearnly_dms.intake.review import conflict_notice
    conflict = result["response_body"]["customer_conflict"]
    qa = dict(payload.get("qa") or {})
    qa["customer_baseline"] = conflict["current"]
    qa["customer_changes"] = conflict["desired"]
    qa["draft"] = {**conflict["current"], **conflict["desired"]}
    qa["customer"] = {**qa.get("customer", {}), "name": qa["draft"].get("name", "")}
    _, qa["summary"] = master_snapshot(qa["draft"])
    qa["customer_dirty"] = True
    nonce = secrets.token_hex(8)
    if store.replace_claimed_booking_payload(line_user_id, payload.get("nonce"),
                                             "booking_review", {**payload, "qa": qa, "nonce": nonce}):
        _send(line_user_id, qa_cards.preview_card(qa, nonce,
            notice=conflict_notice(conflict)))


def _resume_after_master_change(
    binding: dict, line_user_id: str, payload: dict, result: dict
) -> None:
    """确认瞬间主档有变化：改名 → 重发摘要；被删 → 退回对应步骤重选。"""
    from pearnly_dms.booking.conversation import booking_qa as booking_qa

    tenant = binding["tenant_id"]
    qa = result.get("qa") or payload.get("qa") or {}
    changed = result.get("preflight") == "changed"
    nonce = secrets.token_hex(8) if changed else None
    state = (
        "booking_review"
        if changed
        else (None if result.get("field") == "advisor" else "booking_qa")
    )
    updated = {**payload, "qa": qa, "nonce": nonce} if changed else {"qa": qa}
    if not store.replace_claimed_booking_payload(
        line_user_id, payload.get("nonce"), state, updated
    ):
        return
    if changed:
        # 原来这里是**两条**消息（先说"主档变了"、再发汇总卡）—— 额度按条算，能并就并：
        # 那句提示并进汇总卡的第一行（`notice=`），一次操作只发一条。
        _send(line_user_id, qa_cards.preview_card(qa, nonce,
                                                 notice=qa_cards.TXT_MASTER_CHANGED))
        return

    code = str(result.get("error_code") or "ERR_DMS_MASTER_UNMATCHED")
    _send(line_user_id, qa_cards.master_problem(code))
    if result.get("field") == "advisor":
        return
    booking_qa.send_step(tenant, line_user_id, qa, qa.get("step") or "place")


def _retryable_result(result: Dict[str, Any]) -> bool:
    """只对**明确发生在建单之前**的 DMS 阻塞开放重试，不重放未知写入结果。

    主档暂时读不到可以等它恢复后重试；主档已变更重试只会拿到同一份主档，
    必须让操作员重新选 —— 那不是"重试"，是"改内容"。
    """
    if result.get("booking_id") or (result.get("response_body") or {}).get("submitted"):
        return False
    if result.get("error_code") == "ERR_DMS_MASTER_UNMATCHED":
        return False
    if result.get("error_code") in (
        "ERR_DMS_BUSY",  # 排队等账号超时：锁没拿到，什么都没提交
        # 打不开登录页：还没进 DMS，更谈不上提交（提交后的失败由上面 booking_id / submitted
        # 与提交前登记的尝试标记拦下）。以前不在名单里，一次网络抖动就把草稿卡死，
        # 用户只能从头再录（2026-09-27 测试机实测）；与 `config/pacing` 的"给重试"口径一致。
        "ERR_DMS_UNREACHABLE",
        "ERR_DMS_CONCURRENT_LOGIN",
        "ERR_DMS_MASTER_UNAVAILABLE",
        "ERR_DMS_CUSTOMER_LOOKUP",
    ):
        return True
    raw = str(result.get("raw_error") or "")
    response = result.get("response_body") or {}
    raw += " " + str(response.get("raw_error") or "")
    return "company bank master did not become ready" in raw


def _arm_retry(tenant: str, line_user_id: str, payload: dict, nonce: str) -> bool:
    """只重挂**本次尚未提交**的草稿；不得覆盖用户后续新建或取消的会话。"""
    return store.replace_claimed_booking_payload(
        line_user_id,
        payload.get("nonce"),
        "booking_review",
        {**payload, "nonce": nonce},
        ttl_minutes=_RETRY_TTL_MINUTES,
    )
