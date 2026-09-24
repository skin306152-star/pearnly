# -*- coding: utf-8 -*-
"""审批流的流程层：提审 → 选审批人 → 批 / 拒 → **以批准人凭据执行** → 通知销售。

照搬老产品 `services/line_dms/approval_flow.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。

## 这条链的价值在"凭据"两个字

销售改不动老客户资料时提审；**执行用的是批准人自己的 DMS 账号**（不是销售的、也不是
平台账号）—— DMS 侧的审计因此显示**真实批准人**。所以执行那一步要先解析**批准人**的
DMS 端点，批准人没配端点就明说、并把申请**退回待批**（不吞掉）。

## 三处与"卡片/存储"的分工（别在这里重复实现）

· **一次性**由 `dms_approval_store.claim` 的原子认领保证 —— 这里只处理"抢不到"的回话；
· **状态话术**来自 `dms_approval_cards`（`_status_text_for_sales` 按状态挑一句）；
· **执行失败要回炉**（`finish(..., "pending")`），不是判失败 —— 那样销售只能重提。

新平台的管道映射：`_thr` 去掉（同步栈）；`cloud_dispatch.spawn` → `dms_out.enqueue`；
多 OA 的 `binding_guard` 去掉（一个站点一条 OA）；台账写到 `Pearnly Push Log`（与
建档/订车同一条记录页），`trigger` 位取本平台台账的选项、来源标记留在 `request_body`。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import frappe

from pearnly_core import line_binding
from pearnly_core import line_session as store
from pearnly_dms.approval import approval_cards as approval_cards
from pearnly_dms.approval import approval_store as approval_store
from pearnly_dms.line import cards as cards
from pearnly_dms.intake import service as _dms_intake
from pearnly_dms.operators import roster as roster_store
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.line.out import enqueue as _enqueue
from pearnly_dms.line.out import push_text as _push
from pearnly_dms.line.out import reply_text as _reply
from pearnly_dms.line.out import send as _send
from pearnly_dms.line.out import start_loading as _start_loading

logger = logging.getLogger(__name__)

APPROVAL_ACTIONS = frozenset(
    {
        cards.ACT_SUBMIT_APPROVAL,
        cards.ACT_APPROVAL_TARGET,
        cards.ACT_APPROVAL_APPROVE,
        cards.ACT_APPROVAL_REJECT,
        cards.ACT_APPROVAL_RETARGET,
    }
)

LOG = "Pearnly Push Log"


def handle_postback(
    binding: dict, line_user_id: str, reply_token: str, action: str, pb: dict, sess: Optional[dict]
) -> None:
    if action == cards.ACT_SUBMIT_APPROVAL:
        _submit(binding, line_user_id, reply_token, pb)
    elif action == cards.ACT_APPROVAL_TARGET:
        _target(binding, line_user_id, reply_token, pb)
    elif action == cards.ACT_APPROVAL_RETARGET:
        _retarget(binding, line_user_id, reply_token, pb)
    else:  # approve / reject
        _decide(binding, line_user_id, reply_token, pb, approve=(action == cards.ACT_APPROVAL_APPROVE))


def _is_active_admin(profile: Optional[dict]) -> bool:
    """「谁算管理员」的**单一判据**（收审批卡与能批准共用，防两处口径漂移）。"""
    return (
        bool(profile)
        and (profile.get("dms_role") or "") == "admin"
        and (profile.get("status") or "active") == "active"
    )


# ── 审批策略（2026-07-19 泰方拍板） ──────────────────────────────────────────
# 客户档案改写：销售直写不审 —— 写路径借老板管理员凭据落地。
# 信用授权（未来功能）上线时在 `APPROVAL_POLICY` 里登记 True，即接回
# "提审 → 选人 → 批准 → 执行"这条链（链路本身已经在这儿了）。
APPROVAL_POLICY = {"customer_profile": False, "credit_auth": True}


def requires_approval(kind: str) -> bool:
    """未登记的改动类型**按需要审批处理**（fail-closed：新高危动作忘登记 = 默认要审）。"""
    return APPROVAL_POLICY.get(kind, True)


def exact_diff_card(
    user_id: str, display: list, has_admin: bool, nonce: str, summary: dict
):
    """差异卡（有差异才进来）按花名册角色 + 审批策略解出主按钮。

    返回 `(卡片, 是否走审批)`：销售 + 该类改动要审 → 提审卡；其余（有写权）→ 直写；
    都没有 → 只给设置提示。渲染交给 `dms_cards`（单一 primary 选择）。
    """
    prof = roster_store.get_profile(str(user_id))
    role = (prof or {}).get("dms_role") or ""
    if role == "sales" and requires_approval("customer_profile"):
        return cards.diff_card(display, nonce, primary="approval", summary=summary), True
    primary = "update" if (has_admin or role == "admin") else "none"
    return cards.diff_card(display, nonce, primary=primary, summary=summary), False


# ── 审批人名录 ──────────────────────────────────────────────────────────────
def _bound_approvers() -> List[Dict[str, str]]:
    """本租户**能收审批卡**的管理员：`dms_role='admin'` + 启用 + 已绑 LINE。"""
    return [
        {
            "user_id": str(p["user_id"]),
            "display_name": p.get("display_name") or "",
            "line_user_id": p.get("line_user_id") or "",
        }
        for p in roster_store.list_profiles()
        if _is_active_admin(p) and p.get("line_user_id")
    ]


def _approver_eligible(user_id: str) -> bool:
    """能批 = 有管理员档案（启用）；**或**没有档案但本身是租户管理员（老板本人绑了 LINE 也能批）。"""
    prof = roster_store.get_profile(str(user_id))
    if prof:
        return _is_active_admin(prof)
    if not user_id or user_id == "Guest":
        return False
    return bool(set(roster_store.ADMIN_ROLES) & set(frappe.get_roles(user_id)))


# ── 销售侧：提审 / 选人 / 改派 ──────────────────────────────────────────────
def _submit(binding: dict, line_user_id: str, reply_token: str, pb: dict) -> None:
    """提审：核 nonce（只认"有差异且该走审批"的那张草稿）→ 建申请 → 发选人卡。"""
    payload = store.consume_nonce(line_user_id, "reviewing", pb.get("nonce"))
    if payload is None or payload.get("scenario") != "exact_diff" or not payload.get("approval"):
        _reply(reply_token, cards.TXT_EXPIRED)
        return

    approvers = _bound_approvers()
    if not approvers:
        # 一个能收卡的管理员都没有 → 明说并指路（不让销售以为已经提上去了）
        _reply(reply_token, approval_cards.TXT_NO_APPROVERS)
        return

    draft = payload.get("draft") or {}
    request_id = approval_store.create_request(
        str(binding["user_id"]),
        endpoint_id=str(payload.get("endpoint_id") or ""),
        customer_id=str(payload.get("customer_id") or ""),
        customer_name=str(draft.get("name") or ""),
        field_diffs=payload.get("field_diffs") or [],
        draft={"fields": draft, "display_diffs": payload.get("display_diffs") or [],
               "baseline": payload.get("customer_baseline"),
               "write_fields": payload.get("write_fields"),
               "write_addresses": payload.get("write_addresses")},
    )
    if not request_id:
        _reply(reply_token, cards.TXT_SYSTEM_ERROR)
        return
    store.set_session(line_user_id, "approval_pending", {
        "approval_request": request_id, "source": payload, "binding": binding,
    })
    _send(line_user_id, approval_cards.picker_card(request_id, approvers), reply_token)


def _own_pending_or_reply(reply_token: str, user_id: str, pb: dict) -> Optional[dict]:
    """销售侧动作（选人/改派）的共同守卫：是自己的单、且还没被处理。

    取不到 → 过期话术；已被处理 → 按状态回话（不让销售干等）。
    """
    req = approval_store.get_request(str(pb.get("req") or ""))
    if not req or str(req.get("operator_user_id")) != str(user_id):
        _reply(reply_token, cards.TXT_EXPIRED)
        return None
    if req.get("status") != "pending":
        _reply(reply_token, _status_text_for_sales(req))
        return None
    return req


def _target(binding: dict, line_user_id: str, reply_token: str, pb: dict) -> None:
    """选好审批人：定人 → **推审批卡给目标** → 回销售一张等待卡。"""
    req = _own_pending_or_reply(reply_token, binding["user_id"], pb)
    if not req:
        return

    approvers = _bound_approvers()
    aid = str(pb.get("aid") or "all")
    if aid == "all":
        targets, label = approvers, None
    else:
        targets = [a for a in approvers if a["user_id"] == aid]
        label = targets[0]["display_name"] if targets else None
    if not targets:
        # 选的人已停用/解绑，或全员都解绑了 → 让销售重选或找老板
        _reply(reply_token, approval_cards.TXT_NO_APPROVERS)
        return

    if not approval_store.set_target(str(req["id"]), None if aid == "all" else aid):
        _reply(reply_token, cards.TXT_EXPIRED)
        return

    card = approval_cards.request_card(
        str(req["id"]),
        _operator_display_name(str(req["operator_user_id"])),
        req.get("customer_name") or "",
        str(req.get("customer_id") or ""),
        _display_diffs(req),
    )
    # 逐个推（同步栈；每次 push 都是一次 HTTP，串行即可，不占 webhook）
    for target in targets:
        _send(target["line_user_id"], card)
    _send(line_user_id, approval_cards.waiting_card(str(req["id"]), label), reply_token)


def _retarget(binding: dict, line_user_id: str, reply_token: str, pb: dict) -> None:
    """改派：重新发一张选人卡（指定的审批人不在时，销售不至于卡死）。"""
    req = _own_pending_or_reply(reply_token, binding["user_id"], pb)
    if not req:
        return
    approvers = _bound_approvers()
    if not approvers:
        _reply(reply_token, approval_cards.TXT_NO_APPROVERS)
        return
    _send(line_user_id, approval_cards.picker_card(str(req["id"]), approvers), reply_token)


# ── 管理员侧：批 / 拒 ───────────────────────────────────────────────────────
def _decide(
    binding: dict, line_user_id: str, reply_token: str, pb: dict, *, approve: bool
) -> None:
    user_id = str(binding["user_id"])
    if not _approver_eligible(user_id):
        _reply(reply_token, approval_cards.TXT_NOT_APPROVER)
        return

    # 原子认领：谁先点谁拿到；抢不到就说清现状（被别人批了 / 已过期 / 已被拒）
    req = approval_store.claim(str(pb.get("req") or ""), user_id)
    if not req:
        current = approval_store.get_request(str(pb.get("req") or ""))
        if current and current.get("status") == "expired":
            _reply(reply_token, approval_cards.TXT_REQ_EXPIRED)
        else:
            _reply(reply_token, approval_cards.TXT_REQ_STALE)
        return

    if not approve:
        approval_store.finish(str(req["id"]), "rejected")
        _reply(reply_token, approval_cards.TXT_REQ_REJECTED_ADMIN)
        _notify_operator(req, approval_cards.TXT_REQ_REJECTED_SALES)
        return

    # 批准：先说"正在处理"，执行丢后台（要重新登录 DMS、还要写库）
    _reply(reply_token, approval_cards.TXT_PROCESSING)
    _enqueue("approve", _execute_approved, binding, line_user_id, req)


def _execute_approved(binding: dict, admin_line_user_id: str, req: dict) -> None:
    """**以批准人自己的凭据**执行覆盖写：成功 → 已批；失败 → 回炉（可再批）。"""
    tenant_user = str(binding["user_id"])
    profile = roster_store.get_profile(tenant_user)
    if not _is_active_admin(profile):
        # 认领之后权限被收回：退回待批，别让这张单卡在"执行中"
        approval_store.finish(str(req["id"]), "pending")
        return
    _start_loading(admin_line_user_id)

    endpoint = resolve_dms_endpoint(tenant_user, None)
    if not endpoint:
        approval_store.finish(str(req["id"]), "pending")
        _push(admin_line_user_id, approval_cards.TXT_APPROVER_NO_ENDPOINT)
        return

    draft_wrap = req.get("draft") or {}
    draft = draft_wrap.get("fields") or {}
    diffs = req.get("field_diffs") or []
    # 必填两项 + 逐条新值（只写选了的字段，其余保留 DMS 现值）—— 与老产品逐字段一致
    fields = {"people_id": draft.get("people_id", ""), "name": draft.get("name", "")}
    for diff in diffs:
        fields[diff.get("field")] = diff.get("new", "")
    fields = draft_wrap.get("write_fields") if draft_wrap.get("write_fields") is not None else fields

    result = _dms_intake.push_idcard_fields(
        endpoint,
        fields=fields,
        mode="overwrite",
        customer_id=str(req.get("customer_id") or ""),
        addresses=draft_wrap.get("write_addresses"),
        baseline=draft_wrap.get("baseline"),
        request_id="approval:" + str(req["id"]),
        actor=tenant_user,
    )
    success = bool(result.get("success"))
    _write_push_log(
        approver_id=tenant_user,
        endpoint=str(endpoint.get("id") or ""),
        req=req,
        result=result,
        success=success,
        diffs=diffs,
    )

    conflict = (result.get("response_body") or {}).get("customer_conflict")
    if conflict:
        # The old approval authorizes only its original baseline/diff, never the
        # newly discovered change. Terminalize it before issuing another review.
        approval_store.finish(str(req["id"]), "rejected")
        operator_id = str(req.get("operator_user_id") or "")
        luid = roster_store.line_user_id_of(operator_id)
        current = store.get_session(luid) if luid else None
        pending = (current or {}).get("payload") or {}
        from pearnly_dms.line.identity import matches
        if (current and current.get("state") == "approval_pending"
                and pending.get("approval_request") == req["id"]
                and matches(pending.get("binding") or {}, luid)):
            from pearnly_dms.intake.review import build
            new, card = build(operator_id, pending["source"], conflict,
                              has_admin=bool(pending["source"].get("has_admin")))
            if store.replace_session_snapshot(luid, current, "reviewing", new):
                _send(luid, card)
        _push(admin_line_user_id, (result.get("error_friendly") or {}).get("th") or cards.TXT_SAVE_FAIL)
    elif success:
        approval_store.finish(str(req["id"]), "approved")
        _push(admin_line_user_id, approval_cards.TXT_REQ_APPROVED_ADMIN)
        _notify_operator(req, approval_cards.TXT_REQ_APPROVED_SALES)
    else:
        # A durable unknown dispatch cannot be authorized for replay by another approver.
        approval_store.finish(str(req["id"]), "rejected" if
                              (result.get("response_body") or {}).get("submitted") else "pending")
        friendly = (result.get("error_friendly") or {}).get("th") or ""
        _push(
            admin_line_user_id,
            (friendly + "\n" if friendly else "") + approval_cards.TXT_EXEC_FAIL_ADMIN,
        )


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _status_text_for_sales(req: dict) -> str:
    """销售侧看到的"这单已经不是待批了"话术（等待卡/选人卡点晚了）。"""
    status = req.get("status")
    if status == "expired":
        return approval_cards.TXT_REQ_EXPIRED
    if status == "approved":
        return approval_cards.TXT_REQ_APPROVED_SALES
    if status == "rejected":
        return approval_cards.TXT_REQ_REJECTED_SALES
    return approval_cards.TXT_REQ_STALE


def _display_diffs(req: dict) -> List[Dict[str, str]]:
    """申请卡上逐条显示什么：优先用当时存下的展示层，缺了就用原始差异兜底。"""
    wrap = req.get("draft") or {}
    shown = wrap.get("display_diffs") or []
    if shown:
        return shown
    return [
        {
            "label": cards.FIELD_LABELS_TH.get(d.get("field"), d.get("field", "")),
            "old": str(d.get("old") or ""),
            "new": str(d.get("new") or ""),
        }
        for d in (req.get("field_diffs") or [])
    ]


def _operator_display_name(user_id: str) -> str:
    prof = roster_store.get_profile(str(user_id))
    return (prof or {}).get("display_name") or approval_cards.TXT_SALES_FALLBACK


def _notify_operator(req: dict, text: str) -> None:
    """把结果告诉申请人（他绑了 LINE 才发得出去；没绑就静默 —— 他下次上线会看到）。"""
    operator_id = str(req.get("operator_user_id") or "")
    if not operator_id:
        return
    line_user_id = roster_store.line_user_id_of(operator_id)
    if not line_user_id:
        logger.info("[approval] 申请人没绑 LINE，结果不推送：%s", operator_id)
        return
    _push(line_user_id, text)


def _write_push_log(
    *,
    approver_id: str,
    endpoint: str,
    req: dict,
    result: dict,
    success: bool,
    diffs: List[dict],
) -> None:
    """落一条推送台账（与建档/订车同一条记录页）。**台账失败不影响业务结果。**

    `trigger` 位取本平台台账的选项（manual/line/auto）——老产品那格写的是 "id_card"，
    本平台表里没有这个选项；**审批来源**留在 `request_body.trigger` 里（与订车台账同一口径）。
    """
    try:
        doc = frappe.new_doc(LOG)
        doc.status = "success" if success else "failed"
        doc.endpoint = endpoint or None
        doc.trigger = "line"
        doc.attempt = 1
        doc.business_key = str(result.get("customer_id") or req.get("customer_id") or "")
        doc.http_status = 200 if success else 0
        doc.elapsed_ms = int(result.get("elapsed_ms") or 0)
        doc.request_body = json.dumps(
            {
                "adapter": "mrerp_dms",
                "trigger": "line_dms_approval",
                "mode": "overwrite",
                "field_diffs": diffs,
                "request_id": str(req.get("id") or ""),
                "operator_user_id": str(req.get("operator_user_id") or ""),
            },
            ensure_ascii=False,
            default=str,
        )
        doc.response_body = json.dumps(result.get("response_body") or {}, ensure_ascii=False, default=str)
        doc.error_msg = (result.get("error_code") or "")[:2000]
        doc.actor = approver_id
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("[approval] 台账写入失败（不影响业务结果）", exc_info=True)


__all__ = [
    "APPROVAL_ACTIONS",
    "APPROVAL_POLICY",
    "exact_diff_card",
    "handle_postback",
    "requires_approval",
]
