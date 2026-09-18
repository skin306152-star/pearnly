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

# 与产品无关的通用指令
CMD_UNBIND = "解绑"

MSG_GREETING = (
    "你好，这里是 DMS 助手。\n"
    "请把网页上生成的绑定码发给我，完成账号绑定。"
)
MSG_BOUND = "绑定成功，账号：{0}\n现在可以直接把工作交给我。"
MSG_BAD_CODE = "这个绑定码无效或已过期，请回到网页重新生成。"
MSG_UNBOUND = "已解除绑定。需要时请用新的绑定码重新绑定。"
MSG_NEED_BIND = "还没有绑定账号。请把网页上生成的绑定码发给我。"
MSG_HELP = "已绑定账号：{0}\n发「{1}」可以解除绑定。"


def handle(event: dict) -> bool:
    """DMS 处理入口。返回 True 表示这个事件由 DMS 处理了。"""
    etype = event.get("type")
    source = event.get("source") or {}
    user_id = source.get("userId") or ""
    reply_token = event.get("replyToken") or ""

    if not user_id:
        return False

    if etype == "follow":
        line.reply(reply_token, [{"type": "text", "text": MSG_GREETING}])
        return True

    if etype == "unfollow":
        line_binding.unbind(user_id)
        return True

    if etype == "postback":
        bound = _bound_or_prompt(user_id, reply_token)
        if not bound:
            return True
        from pearnly_dms import dms_flow

        dms_flow.handle_postback(
            _binding(user_id, bound), user_id, reply_token, event.get("postback") or {}
        )
        return True

    if etype == "message":
        message = event.get("message") or {}
        mtype = message.get("type")
        if mtype == "image":
            # 拍身份证 → 后台识别（识别要几秒到十几秒，不能占着回复令牌等）
            bound = _bound_or_prompt(user_id, reply_token)
            if not bound:
                return True
            from pearnly_dms import dms_flow

            dms_flow.handle_image(_binding(user_id, bound), user_id, message.get("id") or "")
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
        line.reply(reply_token, [{"type": "text", "text": MSG_NEED_BIND}])
        return None
    return bound


def _handle_text(user_id: str, text: str, reply_token: str) -> None:
    bound = line_binding.user_of(user_id)

    if text.upper() == CMD_UNBIND.upper():
        if bound:
            line_binding.unbind(user_id)
            line.reply(reply_token, [{"type": "text", "text": MSG_UNBOUND}])
        else:
            line.reply(reply_token, [{"type": "text", "text": MSG_NEED_BIND}])
        return

    # 未绑定：把这条文本当绑定码试一次
    if not bound:
        consumed = line_binding.consume_bind_code(text)
        if not consumed:
            line.reply(reply_token, [{"type": "text", "text": MSG_BAD_CODE}])
            return
        profile = line.get_profile(user_id) or {}
        line_binding.bind(user_id, consumed["user"], profile.get("displayName") or "")
        line.reply(reply_token, [
            {"type": "text", "text": MSG_BOUND.format(consumed["user"])}
        ])
        return

    # 已绑定 → 交给对话流（建档：拍身份证 + 输手机号 → 确认 → 写客户档）。
    # 命令词、状态分支、号码透传的顺序都在 text_router 里，**这里不许就地加判断**
    # （老产品的教训：判据散在两处，用户改姓名时打 เมนู 被当成新姓名）。
    from pearnly_dms import dms_text_router

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
    text = MSG_HELP.format(bound, CMD_UNBIND) if bound else MSG_NEED_BIND
    line.reply(reply_token, [{"type": "text", "text": text}])
