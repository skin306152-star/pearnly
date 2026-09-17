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

    if etype == "message":
        message = event.get("message") or {}
        if message.get("type") != "text":
            # 非文本（图片等）属于后续的业务录入，现在先明确回一句，
            # 不要静默丢弃让用户以为发丢了。
            _reply_need_bind_or_help(user_id, reply_token)
            return True
        _handle_text(user_id, (message.get("text") or "").strip(), reply_token)
        return True

    return False


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

    # 已绑定：业务动作的入口。DMS 的具体动作（身份证录入、客户查询）后续在这里接。
    line.reply(reply_token, [
        {"type": "text", "text": MSG_HELP.format(bound, CMD_UNBIND)}
    ])


def _reply_need_bind_or_help(user_id: str, reply_token: str) -> None:
    bound = line_binding.user_of(user_id)
    text = MSG_HELP.format(bound, CMD_UNBIND) if bound else MSG_NEED_BIND
    line.reply(reply_token, [{"type": "text", "text": text}])
