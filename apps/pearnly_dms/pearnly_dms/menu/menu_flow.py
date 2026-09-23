# -*- coding: utf-8 -*-
"""DMS LINE 菜单层：เมนู/问候语弹菜单 → 选建档或订车 → 走既有采集流。

照搬老产品 `services/line_dms/menu_flow.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。

给用户一层**入口**：不用记"先拍卡"这个隐式流程，发问候语就见
「1 建档 / 2 订车」。选完落到 `collecting`，只给会话打一个 `mode`：

· `customer` —— 建档为终点，写档后收尾**不提订车**（泰方 2026-07-19 拍板）；
· `booking` / 缺省（老用户直接拍卡的行为逐字节不变）—— 建档后自动串联订车。

`mode` 存在会话 payload，落档后的分叉在 `after_customer_saved` 里读它。

⚠️ 三类动作（菜单 / 继续订车 / 重拍）**都不消费 nonce**：菜单是无状态入口；
继续订车用客户号对齐防串档；重拍照"编辑"范式**只校验不消费** nonce ——
真正的写档仍由 `dms_flow` 的 `consume_nonce` 守卫，旧卡的确认会因态已变而被拒。
"""

from __future__ import annotations

import logging
from typing import Optional

from pearnly_core import line_session as store
from pearnly_dms.booking.conversation import booking_qa as booking_qa
from pearnly_dms.line import cards as cards
from pearnly_dms.menu import menu_cards as menu_cards
from pearnly_dms.query import query_access as query_access
from pearnly_dms.line.out import send as _send
from pearnly_dms.line.out import push_text as _push
from pearnly_dms.line.out import reply_text as _reply

logger = logging.getLogger(__name__)

MENU_ACTIONS = frozenset(
    {
        cards.ACT_MENU_CUSTOMER,
        cards.ACT_MENU_BOOKING,
        cards.ACT_CONTINUE_BOOKING,
        cards.ACT_RETAKE,
    }
)

# `mode` 语义的**单一落点**：customer = 只建档收尾；其余（booking/缺省直拍）串联订车。
MODE_CUSTOMER = "customer"

# 查询那一族（`dms_query_flow` / `dms_query_cards` / `dms_sales_readback`）**已落地**，
# 所以第 5 项与单字 "5" 都开了。真判据是花名册那一栏（`dms_query_access.can_query`）——
# 这里的开关只管"这一版有没有这个功能"，不再当权限门用。
QUERY_FLOW_LANDED = True

_MENU_CHOICES = {
    "1": cards.ACT_MENU_CUSTOMER,
    "2": cards.ACT_MENU_BOOKING,
    "5": cards.ACT_MENU_QUERY,
}
# 弹菜单/切模式时保留的已收料：重复进菜单不丢已拍的卡 / 已输的号。
# `id_card_mid` 是订车逐问的身份证附件源，同样保留（落档后 `booking_qa.start` 要挂它）。
_KEEP_KEYS = ("id_card", "id_card_mid", "phone", "endpoint_id", "mode")


# ── text：弹菜单 / menu 态下的单字 1|2 ───────────────────────────────────────
def open_menu(
    binding: dict,
    line_user_id: str,
    reply_token: str,
    sess: Optional[dict],
    *,
    greet: bool = False,
) -> None:
    """置 `menu` 态（保留已收料）→ 发菜单卡。

    `greet=True` 是问候语进场（照 mockup 先来一句欢迎）；`เมนู` 是回访召唤，
    只发卡不寒暄。词形判定在 `commands.classify`，本函数只管落态出卡。
    """
    old = (sess or {}).get("payload") or {}
    payload = {key: old.get(key) for key in _KEEP_KEYS if old.get(key)}
    store.set_session(line_user_id, "menu", payload)
    allowed = can_query(binding)
    msgs: list = [
        menu_cards.menu_card(can_query=bool(allowed)),
    ]
    if greet:
        msgs.insert(0, {"type": "text", "text": cards.TXT_MENU_GREETING})
    for msg in msgs:
        _send(line_user_id, msg, reply_token)


def can_query(binding: dict) -> bool:
    """第 5 项要不要画：功能已落地 → 看花名册那一栏（真判据在后端）。"""
    return bool(QUERY_FLOW_LANDED and query_access.can_query(binding))


def handle_choice(
    binding: dict, line_user_id: str, reply_token: str, sess: Optional[dict], text: str
) -> bool:
    """`menu` 态下的单字 '1'/'2' = 点对应菜单项，返回是否已接管。

    只认这两个单字符：其余数字文本（如手机号）交回调用方走号码路。
    """
    stripped = text.strip()
    if (sess or {}).get("state") != "menu" or stripped not in _MENU_CHOICES:
        return False
    action = _MENU_CHOICES[stripped]
    if action == cards.ACT_MENU_QUERY:
        if not QUERY_FLOW_LANDED:
            return False  # 查询那族没搬：不认这个字，别假装接管了
        from pearnly_dms.query import query_flow as query_flow

        query_flow.open_query(binding, line_user_id, reply_token)
        return True
    _choose(binding, line_user_id, reply_token, sess, action)
    return True


# ── postback：菜单项 / 继续订车 / 重拍 ───────────────────────────────────────
def handle_postback(
    binding: dict,
    line_user_id: str,
    reply_token: str,
    action: str,
    pb: dict,
    sess: Optional[dict],
) -> None:
    if action in (cards.ACT_MENU_CUSTOMER, cards.ACT_MENU_BOOKING):
        _choose(binding, line_user_id, reply_token, sess, action)
    elif action == cards.ACT_CONTINUE_BOOKING:
        _continue_booking(binding, line_user_id, reply_token, pb, sess)
    else:  # ACT_RETAKE
        _retake(binding, line_user_id, reply_token, pb, sess)


def _choose(
    binding: dict, line_user_id: str, reply_token: str, sess: Optional[dict], action: str
) -> None:
    """选中菜单项：给会话打 `mode` 回 `collecting`；齐料直接查重，缺料按缺项提示补料。

    有进行中的逐问 / 待确认订车也照常进菜单（会话被覆写 = 放弃，与 `เริ่มใหม่` 同语义）。
    """
    mode = MODE_CUSTOMER if action == cards.ACT_MENU_CUSTOMER else "booking"
    old = (sess or {}).get("payload") or {}
    # 保留清单派生自 `_KEEP_KEYS`（`mode` 由本函数按所选菜单重写，不从旧会话带）
    payload = {key: old.get(key) for key in _KEEP_KEYS if key != "mode" and old.get(key)}
    payload["mode"] = mode
    store.set_session(line_user_id, "collecting", payload)

    id_card, phone = payload.get("id_card"), payload.get("phone")
    if id_card and phone:
        from pearnly_dms.intake import flow as flow  # 延迟导入避免环依赖

        flow._enqueue(
            "dedup",
            flow.run_dedup,
            binding,
            line_user_id,
            id_card,
            phone,
            payload.get("endpoint_id"),
            1,
            reply_token,
        )
        return
    if id_card:
        tail = cards.TXT_ASK_PHONE
    elif phone:
        tail = cards.TXT_ASK_CARD
    else:
        tail = cards.TXT_MENU_SEND_CARD
    _reply(reply_token, tail)


def _continue_booking(
    binding: dict, line_user_id: str, reply_token: str, pb: dict, sess: Optional[dict]
) -> None:
    """续订卡上的 [ทำใบจองต่อ]：postback 里的客户号要对得上会话（防串到别的档）→ 开逐问。"""
    payload = (sess or {}).get("payload") or {}
    cid = pb.get("cid") or ""
    if not cid or cid != str(payload.get("customer_id") or ""):
        _reply(reply_token, cards.TXT_EXPIRED)
        return
    booking_qa.start(
        binding["tenant_id"],
        line_user_id,
        endpoint_id=str(payload.get("endpoint_id") or ""),
        customer_id=cid,
        customer_name=str(payload.get("name") or ""),
        draft=payload.get("draft") or {},
        summary=payload.get("summary"),
        user_id=str(binding.get("user_id") or ""),
        id_card_mid=payload.get("id_card_mid") or None,
    )


def _retake(
    binding: dict, line_user_id: str, reply_token: str, pb: dict, sess: Optional[dict]
) -> None:
    """待确认卡上的 [ถ่ายบัตรใหม่]：清掉 id_card，留 phone/mode/endpoint 回 collecting 等重拍。

    nonce **只校验不消费**：重拍是读操作，写档仍由 `dms_flow` 的 `consume_nonce` 守卫；
    旧卡的确认会因态已回 `collecting` 而被拒。nonce 不符 → 过期话术、会话不动。
    """
    payload = (sess or {}).get("payload") or {}
    nonce = pb.get("nonce")
    if not store.verify_nonce(sess, nonce):
        _reply(reply_token, cards.TXT_EXPIRED)
        return
    kept = {key: payload.get(key) for key in ("phone", "mode", "endpoint_id") if payload.get(key)}
    store.set_session(line_user_id, "collecting", kept)
    _reply(reply_token, cards.TXT_RETAKE)


# ── 客户档落定后的分叉（`mode` 门控订车串联） ────────────────────────────────
def after_customer_saved(
    binding: dict,
    line_user_id: str,
    *,
    endpoint_id: str,
    customer_id: str,
    draft: dict,
    name: str = "",
    mode: str = "",
    same_data: bool = False,
    summary: Optional[dict] = None,
) -> None:
    """客户档落定后：`booking`/缺省 → 开订车逐问；`customer` → 收尾**不提订车**。

    菜单 1 = 只建档（泰方 2026-07-19 拍板）：不再推"要不要接着订车"的续订卡；
    续订卡的 postback 处理保留，聊天历史里已发出的旧卡仍能点。

    `same_data=True` 表示本次**零写入**（数据已存 / 选择保留），文案不谎称"已保存"。
    `summary` 透传给逐问开局 —— 预览卡与客户确认卡共用同一份五要素。
    """
    if mode != MODE_CUSTOMER:
        sess = store.get_session(line_user_id)
        payload = (sess or {}).get("payload") or {}
        booking_qa.start(
            binding["tenant_id"],
            line_user_id,
            endpoint_id=endpoint_id,
            customer_id=customer_id,
            # 零写入路径（同资料点保持）不传 name → 回退建档 draft 里的姓名
            customer_name=name or (draft or {}).get("name", ""),
            draft=draft,
            summary=summary,
            user_id=str(binding.get("user_id") or ""),
            id_card_mid=payload.get("id_card_mid") or None,
        )
        return

    store.clear_session(line_user_id)
    _push(line_user_id, cards.TXT_DONE_SAME if same_data else cards.TXT_DONE_SAVED)
