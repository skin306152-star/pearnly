# -*- coding: utf-8 -*-
"""开一局 LINE 订车逐问：读取完整本地主档 + 定死顾问归属。

原业务来自 `services/line_dms/booking_qa_start.py`；本批读取改为五分钟完整快照，
最终写入另作实时复核。

开局这一步**只做两件不能省的事**，两件都是"开局不查清、后面会记错"：

1. **读取完整本地主档**并保留源数据时间；后续每步重新检查当前凭据/权限与完整代际。
   未建好或过期时提示待同步，保留已完成的客户录入并提供继续订车按钮，不重复 OCR/写入。
2. **定死顾问归属**（提成算给谁）。顾问认不出就不开单 —— 宁可让销售现在去找管理员，
   也不要开出一张月底没人认的提成单。

⚠️ 会话**按 LINE 用户**存（新平台一个站点就是一家租户），所以老产品那几个
`tenant_id` 参数在这一层用不上了：`line_session` 的键就是 `line_user_id`。
"""

from __future__ import annotations

from pearnly_core import line_session as store
from pearnly_dms.booking.conversation import qa_cards as qa_cards
from pearnly_dms.masters.master_contract import MasterSyncError, build_snapshot
from pearnly_dms.line.out import send as _send


def start(
    tenant_id,
    line_user_id,
    endpoint_id,
    customer_id,
    customer_name,
    id_card_mid,
    *,
    persist,
    send_step,
    resolve_endpoint,
    get_masters,
    resolve_advisor,
    reply_token=None,
    draft=None,
    user_id="",
    summary=None,
) -> None:
    """解出操作员归属，并记录本局主档代际（不触发 DMS 读取）。"""
    endpoint = resolve_endpoint(user_id, endpoint_id)
    advisor, dms_username = (None, "")
    snapshot = None
    if endpoint:
        try:
            live_masters = get_masters(endpoint, require_complete=True)
            snapshot = build_snapshot(live_masters)
        except MasterSyncError as exc:
            if str(exc.code).startswith("ERR_DMS_SNAPSHOT_"):
                # Customer saving may already be complete. A cold/stale cache
                # must never force another upload or replay of that write.
                from pearnly_dms.line import cards
                store.set_session(line_user_id, "customer_saved", {
                    "endpoint_id": str(endpoint_id or ""), "customer_id": str(customer_id or ""),
                    "name": customer_name or "", "draft": dict(draft or {}),
                    "summary": dict(summary or {}), "id_card_mid": id_card_mid,
                    "mode": "booking",
                })
                msg = qa_cards.master_problem(exc.code)
                msg["quickReply"] = {"items": [{"type": "action", "action": {
                    "type": "postback", "label": cards._t("BTN_CONTINUE_BOOKING"),
                    "data": cards._data(cards.ACT_CONTINUE_BOOKING, cid=str(customer_id or "")),
                }}]}
                _send(line_user_id, msg, reply_token)
                return
            store.clear_session(line_user_id)
            _send(line_user_id, qa_cards.master_problem(exc.code), reply_token)
            return
        advisor, dms_username = resolve_advisor(endpoint, masters=live_masters)
    if advisor is None:
        store.clear_session(line_user_id)
        msg = qa_cards.no_endpoint() if not endpoint else qa_cards.advisor_block_msg(dms_username)
        _send(line_user_id, msg)
        return

    qa = {
        "step": "place",
        "endpoint_id": str(endpoint_id or ""),
        "customer": {"id": str(customer_id or ""), "name": customer_name or ""},
        "advisor": advisor,
        "draft": dict(draft or {}),
        "summary": dict(summary or {}),
        "user_id": str(user_id or ""),
        "files": {"id_card_mid": id_card_mid or None, "slip_mid": None},
        "answers": {},
        "payments": [],
        "pending_channel": {},
        "audit": [],
        "master_snapshot": snapshot,
    }
    persist(tenant_id, line_user_id, qa)
    send_step(tenant_id, line_user_id, qa, "place", reply_token)
