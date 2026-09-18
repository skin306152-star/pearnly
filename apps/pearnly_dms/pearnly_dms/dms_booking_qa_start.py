# -*- coding: utf-8 -*-
"""开一局 LINE 订车逐问：扎一份实时主档 + 定死顾问归属。

照搬老产品 `services/line_dms/booking_qa_start.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。

开局这一步**只做两件不能省的事**，两件都是"开局不查清、后面会记错"：

1. **实时抓一份完整主档**（`require_complete=True`，不走会话缓存）——这一份会冻进
   会话，后面每一步的分页/按钮都复用它；抓不到就**当场收场**（清会话 + 出"主档读不到"卡），
   不带着一份残缺主档往下走。
2. **定死顾问归属**（提成算给谁）。顾问认不出就不开单 —— 宁可让销售现在去找管理员，
   也不要开出一张月底没人认的提成单。

⚠️ 会话**按 LINE 用户**存（新平台一个站点就是一家租户），所以老产品那几个
`tenant_id` 参数在这一层用不上了：`line_session` 的键就是 `line_user_id`。
"""

from __future__ import annotations

from pearnly_core import line_session as store
from pearnly_dms import dms_qa_cards as qa_cards
from pearnly_dms.dms_master_contract import MasterSyncError, build_snapshot
from pearnly_dms.dms_out import send as _send


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
    """解出操作员归属，并冻结这一局要用的实时主档。"""
    endpoint = resolve_endpoint(user_id, endpoint_id)
    advisor, dms_username = (None, "")
    snapshot = None
    if endpoint:
        try:
            live_masters = get_masters(endpoint, require_complete=True)
            snapshot = build_snapshot(live_masters)
        except MasterSyncError as exc:
            store.clear_session(line_user_id)
            _send(line_user_id, qa_cards.master_problem(exc.code))
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
