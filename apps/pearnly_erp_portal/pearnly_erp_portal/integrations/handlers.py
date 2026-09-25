"""ERP 的 LINE 事件处理器（通道侧）。

传输层（`pearnly_core.line`）负责验签、幂等与回执；这里只做 ERP 的判断。
绑定后的图片/PDF 按采购或销售方向留底，交 ERP 页面识别、复核和确认。
未承接的消息类型明确提示未开放，不声称已经录入。
"""

import frappe
from frappe import _

from pearnly_core import line, line_binding, line_session

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.integrations import intake

# 解绑指令词是**用户手打的词**，所以按通道语言认，并容错中英泰三套写法：
# 认不出就等于这个功能对用户不存在。
UNBIND_WORDS = ("ยกเลิกการผูก", "ยกเลิกการเชื่อมต่อ", "解绑", "unbind")

# 方向词：用户手打的词，翻了就等于这个功能不存在（与解绑词同理）。
# 旧线上是在菜单里选"买/卖"再发单据；菜单卡片还没做，先用一句话切。
DIRECTION_WORDS = {
    "ซื้อ": "purchase", "purchase": "purchase",
    "ขาย": "sales", "sell": "sales", "sales": "sales",
}

SESSION_STATE = "erp_intake"


def _direction_of(line_user_id: str) -> str:
    """这位 LINE 用户当前在录哪个方向；没说过就是进项（与旧线上默认一致）。"""
    payload = ((line_session.get_session(line_user_id) or {}).get("payload") or {})
    direction = payload.get("direction")
    return direction if direction in ("purchase", "sales") else "purchase"


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
    # 码是本人在网页上领的，但领码之后 LINE 可能已被管理员取消（团队管理那一勾）。
    if not guard.has("integration.manage", user=consumed["user"]):
        line.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return
    profile = line.get_profile(user_id) or {}
    line_binding.bind(user_id, consumed["user"], profile.get("displayName") or "")
    line.reply(reply_token, [{"type": "text", "text": _("Bound to {0}").format(
        _display_name(consumed["user"]))}])


def _receive_document(user_id: str, bound: str, message: dict, reply_token: str,
                      direction: str = "purchase") -> None:
    """收到图片/PDF：留底并落草稿。没有对应岗位的人不能往账上放单据。"""
    if not guard.has("integration.read", user=bound):
        line.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return
    capability = intake.DIRECTION_CAPABILITY.get(direction) or "purchase.write"
    if not guard.has(capability, user=bound):
        line.reply(reply_token, [{"type": "text",
                                  "text": _("This account cannot send documents")}])
        return
    media_type = message.get("type") or ""
    filename = message.get("fileName") or ""
    if not filename:
        filename = f"line-{message.get('id') or 'document'}.jpg" if media_type == "image" \
            else f"line-{message.get('id') or 'document'}.pdf"
    try:
        content = line.download_message_content(message.get("id") or "")
        # A signed webhook is still a Guest request. Use only the verified local
        # binding, never Administrator, and restore it before another event runs.
        previous_user = frappe.session.user
        try:
            frappe.set_user(bound)
            saved = intake.receive(line_user_id=user_id, sent_by=bound, media_type=media_type,
                                   content=content, filename=filename, direction=direction,
                                   line_message_id=message.get("id") or "")
        finally:
            frappe.set_user(previous_user)
    except Exception:
        # 日志标题也走英文：`check-i18n` 的存量清单已经够长，新代码不再往里加中文。
        frappe.log_error(title="ERP LINE document storage failed",
                         message=frappe.get_traceback()[-1500:])
        line.reply(reply_token, [{"type": "text",
                                  "text": _("Could not store this document")}])
        return
    if saved.get("duplicate"):
        line.reply(reply_token, [{"type": "text", "text": _("Already received")}])
        return
    line.reply(reply_token, [{"type": "text", "text": _("Received; confirm it on the ERP page")}])


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

    text = (message.get("text") or "").strip()
    bound = line_binding.user_of(user_id)

    if message.get("type") in ("image", "file"):
        if not bound:
            line.reply(reply_token, [{"type": "text",
                                      "text": _("Bind this account first")}])
            return True
        _receive_document(user_id, bound, message, reply_token, _direction_of(user_id))
        return True

    if message.get("type") != "text":
        line.reply(reply_token, [{"type": "text", "text": _("This channel is not open yet")}])
        return True

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

    direction = DIRECTION_WORDS.get(text.casefold()) or DIRECTION_WORDS.get(text.strip())
    if direction:
        line_session.set_session(user_id, SESSION_STATE, {"direction": direction})
        line.reply(reply_token, [{"type": "text", "text": _("Now sending {0} documents").format(
            _("purchase") if direction == "purchase" else _("sales"))}])
        return True

    line.reply(reply_token, [{"type": "text", "text": _("This channel is not open yet")}])
    return True
