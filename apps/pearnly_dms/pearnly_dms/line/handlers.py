# -*- coding: utf-8 -*-
"""DMS 的 LINE 事件处理器。

传输层（`pearnly_core.line`）负责验签、幂等、回执；这里只做 DMS 的业务判断。
旧系统里 DMS 这条已经跑通过，本文件先落**身份绑定**这一段 —— 它是所有业务
动作的前提：没有绑定就不知道这条消息属于哪个账号。

后续业务（身份证录入、客户查询等）在这里按事件类型继续加分支即可，
传输层不用动。
"""

import frappe

from pearnly_core import line, line_binding
from pearnly_dms.line import cards as cards

# 解绑指令词：这是**用户手打的词**，所以按通道语言认（LINE 那头是泰国销售），
# 同时容错中文与英文 —— 认不出就等于这个功能对用户不存在。
# 泰文**两个词都收**：本产品自己的话术用 `ยกเลิกการผูก`（"解除绑定"），而老 DMS 通道
# 用的是 `ยกเลิกการเชื่อมต่อ`（"断开连接"，见线上基准 501589f0 的
# `routes/line_dms_webhook_routes.py`）。老用户按旧话术打字也必须能解绑 ——
# 原先的注释写"老产品只认一个中文词"，与 DMS 通道的事实不符（2026-09-21 复核改正）。
UNBIND_WORDS = ("ยกเลิกการผูก", "ยกเลิกการเชื่อมต่อ", "解绑", "unbind")


def _is_unbind(text: str) -> bool:
    return text.strip().casefold() in {w.casefold() for w in UNBIND_WORDS}


# 回复话术全部取自语言包（键 = `dms.card.MSG_BIND_*`），代码里不留中文 ——
# 这与本产品其他卡片文案同一套机制（见 `dms_cards.py` 开头那段说明）。
# 取词走 `cards.*`，所以 `scripts/sample-verify-dms-cards.py` 的"防漏键"守卫会自动覆盖到。


def handle(event: dict) -> bool:
    """DMS 处理入口。返回 True 表示这个事件由 DMS 处理了。"""
    etype = event.get("type")
    source = event.get("source") or {}
    user_id = source.get("userId") or ""
    reply_token = event.get("replyToken") or ""

    if not user_id:
        return False

    unbinding = etype == "message" and _is_unbind((event.get("message") or {}).get("text") or "")
    if etype in {"message", "postback"} and not unbinding and line_binding.user_of(user_id):
        from pearnly_core import line_session
        from pearnly_dms.booking import booking_attempt

        marker = ((line_session.get_session(user_id) or {}).get("payload") or {}).get("booking_attempt")
        if marker:
            from pearnly_dms.booking import recovery
            recovery.request(user_id, marker)
            # The pending order may belong to the previous employee after rebind.
            # Keep it durable, but don't expose its number to the new identity.
            line.reply(reply_token, [{"type": "text", "text": booking_attempt.message({})}])
            return True

    if etype == "follow":
        line.reply(reply_token, [{"type": "text", "text": cards.MSG_BIND_GREETING}])
        return True

    if etype == "unfollow":
        line_binding.unbind(user_id)
        return True

    if etype == "postback":
        bound = _bound_or_prompt(user_id, reply_token)
        if not bound:
            return True
        from pearnly_dms.intake import flow as dms_flow

        dms_flow.handle_postback(
            _binding(user_id, bound), user_id, reply_token, event.get("postback") or {}
        )
        return True

    if etype == "message":
        message = event.get("message") or {}
        mtype = message.get("type")
        if mtype == "image":
            # 图片有两种来历：**订车逐问里的转账凭证**，和**拍身份证**（建档）。
            # 先看会话是不是在逐问态 —— 那时图片归逐问状态机（它会自己判断这一步收不收图），
            # 否则才当身份证走识别。识别要几秒到十几秒，一律丢后台，不占着回复令牌等。
            bound = _bound_or_prompt(user_id, reply_token)
            if not bound:
                return True
            from pearnly_core import line_session as store
            from pearnly_dms.booking.conversation import booking_qa as dms_booking_qa
            from pearnly_dms.intake import flow as dms_flow

            sess = store.get_session(user_id)
            if (sess or {}).get("state") == "booking_qa":
                if dms_booking_qa.handle_image(
                    frappe.local.site, user_id, message.get("id") or "", reply_token, sess=sess
                ):
                    return True
            # 令牌一起带进后台任务：识别那几秒通常还在有效期内，结果卡就能走**免费回覆**
            # 而不是计费推播（2026-09-23 省额度）。
            dms_flow.handle_image(_binding(user_id, bound), user_id, message.get("id") or "",
                                 reply_token)
            return True
        if mtype != "text":
            # 非文本（图片等）属于后续的业务录入，现在先明确回一句，
            # 不要静默丢弃让用户以为发丢了。
            _reply_need_bind_or_help(user_id, reply_token)
            return True
        _handle_text(user_id, (message.get("text") or "").strip(), reply_token)
        return True

    return False


def _bound_or_prompt(line_user_id: str, reply_token: str):
    """已绑定就返回本地账号；没绑定就回一句提示并返回 None。

    图片和 postback 都不该在未绑定时静默丢弃 —— 用户会以为发丢了。
    """
    bound = line_binding.user_of(line_user_id)
    if not bound:
        line.reply(reply_token, [{"type": "text", "text": cards.MSG_BIND_NEED}])
        return None
    return bound


def _handle_text(user_id: str, text: str, reply_token: str) -> None:
    bound = line_binding.user_of(user_id)

    if _is_unbind(text):
        if bound:
            line_binding.unbind(user_id)
            line.reply(reply_token, [{"type": "text", "text": cards.MSG_BIND_UNBOUND}])
        else:
            line.reply(reply_token, [{"type": "text", "text": cards.MSG_BIND_NEED}])
        return

    # 未绑定：把这条文本当绑定码试一次
    if not bound:
        consumed = line_binding.consume_bind_code(text, line_user_id=user_id)
        if not consumed:
            line.reply(reply_token, [{"type": "text", "text": cards.MSG_BIND_BAD_CODE}])
            return
        profile = line.get_profile(user_id) or {}
        line_binding.bind(user_id, consumed["user"], profile.get("displayName") or "")
        display_name = frappe.db.get_value("DMS Operator", {"user": consumed["user"]}, "display_name")
        display_name = display_name or frappe.db.get_value("User", consumed["user"], "full_name") or ""
        line.reply(reply_token, [
            {"type": "text", "text": cards.MSG_BIND_OK.format(display_name)}
        ])
        return

    # 已绑定 → 交给对话流（建档：拍身份证 + 输手机号 → 确认 → 写客户档）。
    # 命令词、状态分支、号码透传的顺序都在 text_router 里，**这里不许就地加判断**
    # （老产品的教训：判据散在两处，用户改姓名时打 เมนู 被当成新姓名）。
    from pearnly_dms.line import text_router as dms_text_router

    dms_text_router.route(_binding(user_id, bound), user_id, reply_token, text)


def _binding(line_user_id: str, user: str) -> dict:
    """对话流要的绑定上下文。

    `tenant_id` = 站点名：我们"每产品一个站点"就是租户隔离，所以会话/台账都不用再带
    租户字段（见 `pearnly_core/line_session.py`）；这里保留这个键只是为了和对话流的
    签名对齐，值为站点名。
    """
    import frappe

    return {"line_user_id": line_user_id, "user_id": user, "tenant_id": frappe.local.site}


def _reply_need_bind_or_help(user_id: str, reply_token: str) -> None:
    bound = line_binding.user_of(user_id)
    name = (frappe.db.get_value("DMS Operator", {"user": bound}, "display_name")
            or frappe.db.get_value("User", bound, "full_name")) if bound else ""
    text = cards.MSG_BIND_HELP.format(name or "", UNBIND_WORDS[0]) if bound else cards.MSG_BIND_NEED
    line.reply(reply_token, [{"type": "text", "text": text}])
