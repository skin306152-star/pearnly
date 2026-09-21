"""ERP 的 LINE 事件处理器（通道侧）。

传输层（`pearnly_core.line`）负责验签、幂等与回执；这里只做 ERP 的判断。
本批只落**身份绑定**这一段：没有绑定就不知道这条消息属于哪个账号，后面的
LINE 录单（发单据 → 识别 → 入账）才有对象。

尚未迁的部分说清楚：绑定成功之后再发消息，会收到一句"这个入口还没开放"。
**不给假动作**（例如回一句"已收到"然后什么也不做），那是谎报。
"""

import frappe
from frappe import _

from pearnly_core import line, line_binding

# 解绑指令词是**用户手打的词**，所以按通道语言认，并容错中英泰三套写法：
# 认不出就等于这个功能对用户不存在。
UNBIND_WORDS = ("ยกเลิกการผูก", "ยกเลิกการเชื่อมต่อ", "解绑", "unbind")


def _is_unbind(text: str) -> bool:
    return text.strip().casefold() in {w.casefold() for w in UNBIND_WORDS}


def _display_name(user: str) -> str:
    return (frappe.db.get_value("User", user, "full_name")
            or frappe.db.get_value("User", user, "name") or "")


def _bind_with_code(user_id: str, text: str, reply_token: str) -> None:
    """把这条文本当绑定码试一次。认不出就回"码不对"。"""
    consumed = line_binding.consume_bind_code(text, line_user_id=user_id)
    if not consumed:
        line.reply(reply_token, [{"type": "text", "text": _("That code is not valid")}])
        return
    profile = line.get_profile(user_id) or {}
    line_binding.bind(user_id, consumed["user"], profile.get("displayName") or "")
    line.reply(reply_token, [{"type": "text", "text": _("Bound to {0}").format(
        _display_name(consumed["user"]))}])


def handle(event: dict) -> bool:
    """ERP 处理入口。返回 True 表示这个事件由 ERP 处理了。"""
    etype = event.get("type")
    source = event.get("source") or {}
    user_id = source.get("userId") or ""
    reply_token = event.get("replyToken") or ""
    if not user_id:
        return False

    if etype == "follow":
        line.reply(reply_token, [{"type": "text", "text": _("Send the code from the ERP page to bind")}])
        return True

    if etype == "unfollow":
        line_binding.unbind(user_id)
        return True

    if etype != "message":
        return False
    message = event.get("message") or {}
    if message.get("type") != "text":
        # 图片/文件是录单要用的：还没做，就不能装作收下了。
        line.reply(reply_token, [{"type": "text", "text": _("This channel is not open yet")}])
        return True

    text = (message.get("text") or "").strip()
    bound = line_binding.user_of(user_id)

    if _is_unbind(text):
        if bound:
            line_binding.unbind(user_id)
            line.reply(reply_token, [{"type": "text", "text": _("Unbound")}])
        else:
            line.reply(reply_token, [{"type": "text", "text": _("Bind this account first")}])
        return True

    if not bound:
        _bind_with_code(user_id, text, reply_token)
        return True

    line.reply(reply_token, [{"type": "text", "text": _("This channel is not open yet")}])
    return True
