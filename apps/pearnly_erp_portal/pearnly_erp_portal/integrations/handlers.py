"""ERP 的 LINE 事件处理器（通道侧）。

传输层（`pearnly_core.line`）负责验签、幂等与回执；这里只做 ERP 的分派：
绑定 / 解绑、菜单与录单前的选择（`line_flow`）、收单据（`intake`）。
业务口径见 docs/work/2026-09-25-erp-line-intake.md（用户 2026-09-25 定稿）。
"""
from urllib.parse import parse_qs

import frappe
from frappe import _

from pearnly_core import line, line_binding

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.integrations import intake, line_flow, line_menu

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
    # 码是本人在网页上领的，但领码之后 LINE 可能已被管理员取消（团队管理那一勾）。
    if not guard.has("integration.manage", user=consumed["user"]):
        line.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return
    profile = line.get_profile(user_id) or {}
    line_binding.bind(user_id, consumed["user"], profile.get("displayName") or "")
    # 绑上就挂按岗位的那套菜单；回执里同时给 ซื้อ / ขาย 两个原生按钮（旧线上绑定后回菜单）。
    line_menu.request_sync(user_id)
    line.reply(reply_token, [{"type": "text", "text": _("Bound to {0}").format(
        _display_name(consumed["user"]))}])


def _receive_document(user_id: str, bound: str, message: dict, reply_token: str) -> None:
    """收到图片/PDF：必须先选好方向与各项；按这组选择留底并排队识别。"""
    choices = line_flow.choices(user_id)
    if not line_flow.ready(choices):
        line.reply(reply_token, [{"type": "text", "text": _("Choose purchase or sales first")}])
        return
    if not guard.has(intake.DIRECTION_CAPABILITY[choices["direction"]], user=bound):
        line.reply(reply_token, [{"type": "text", "text": _("This account cannot send documents")}])
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
            # 同一组选择可以连发多张：每张各排一次识别、各出一张卡；选择的 30 分钟重新计时。
            line_flow.keep(user_id, choices)
            intake.receive(line_user_id=user_id, sent_by=bound, choices=choices,
                           media_type=media_type, content=content, filename=filename,
                           line_message_id=message.get("id") or "", reply_token=reply_token)
        finally:
            frappe.set_user(previous_user)
    except Exception:
        # 日志标题也走英文：`check-i18n` 的存量清单已经够长，新代码不再往里加中文。
        frappe.log_error(title="ERP LINE document storage failed",
                         message=frappe.get_traceback()[-1500:])
        line.reply(reply_token, [{"type": "text", "text": _("Could not store this document")}])


def _postback(user_id: str, bound, data: str, reply_token: str) -> bool:
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    action = params.get("a") or ""
    if not (action.startswith("mode:") or action == "erp"):
        return False
    if not bound:
        line.reply(reply_token, [{"type": "text", "text": _("Bind this account first")}])
        return True
    if not guard.has("integration.read", user=bound):
        line.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return True
    if action.startswith("mode:"):
        # 富菜单（新旧两套都是 `a=mode:purchase|sales`）与「เมนู」回的按钮。
        line_flow.start(user_id, bound, action[len("mode:"):], reply_token)
    else:
        line_flow.answer(user_id, bound, data, reply_token)
    return True


def handle(event: dict) -> bool:
    """ERP 处理入口。返回 True 表示这个事件由 ERP 处理了。

    回给这个人的话、按钮都用他手机上 LINE 的语言（用户 2026-09-26），处理完还原。"""
    user_id = (event.get("source") or {}).get("userId") or ""
    previous = getattr(frappe.local, "lang", None)
    if user_id:
        frappe.local.lang = line.user_language(user_id, default=intake.channel_language())
    try:
        return _handle(event)
    finally:
        frappe.local.lang = previous


def _handle(event: dict) -> bool:
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
        line_menu.request_sync(user_id)
        return True

    bound = line_binding.user_of(user_id)
    if etype == "postback":
        return _postback(user_id, bound, (event.get("postback") or {}).get("data") or "", reply_token)
    if etype != "message":
        return False
    message = event.get("message") or {}
    text = (message.get("text") or "").strip()

    if message.get("type") in ("image", "file"):
        if not bound:
            line.reply(reply_token, [{"type": "text", "text": _("Bind this account first")}])
        elif not guard.has("integration.read", user=bound):
            line.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        else:
            _receive_document(user_id, bound, message, reply_token)
        return True

    if message.get("type") != "text":
        line.reply(reply_token, [{"type": "text", "text": _("This channel is not open yet")}])
        return True

    if _is_unbind(text):
        if bound:
            line_binding.unbind(user_id)
            line_menu.request_sync(user_id)
            line.reply(reply_token, [{"type": "text", "text": _("Unbound")}])
        else:
            line.reply(reply_token, [{"type": "text", "text": _("Bind this account first")}])
        return True

    if not bound:
        _bind_with_code(user_id, text, reply_token)
        return True

    if not guard.has("integration.read", user=bound):
        line.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return True
    if text.casefold() in line_flow.MENU_WORDS:
        line_flow.menu(bound, reply_token)
        return True
    direction = line_flow.DIRECTION_WORDS.get(text.casefold())
    if direction:
        line_flow.start(user_id, bound, direction, reply_token)
        return True
    line.reply(reply_token, [{"type": "text", "text": _("Tap the menu to start")}])
    return True
