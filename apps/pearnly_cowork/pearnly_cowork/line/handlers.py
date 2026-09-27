"""Cowork 的 LINE 事件处理器（通道侧）。

传输层 `pearnly_core.line` 负责验签、幂等与回执；这里只做分派：绑定 / 解绑、
「发单据进 ERP」的选择（`flow`）、收单据（`intake`），菜单跟着权限与手机语言走（`menu`）。
盘点在富菜单上直接打开网页端盘点页，不经过这里；工作协调要重新设计，还没有入口（用户 2026-09-26）。
"""
import frappe
from frappe import _

from pearnly_core import line, line_binding

from pearnly_cowork.line import binding, flow, intake, menu

# 解绑词是用户手打的，按中英泰三套写法都认（与 ERP 同一组）。
UNBIND_WORDS = ("ยกเลิกการผูก", "ยกเลิกการเชื่อมต่อ", "解绑", "unbind")


def _reply(reply_token: str, text: str) -> None:
    line.reply(reply_token, [{"type": "text", "text": text}])


def _bind_with_code(user_id: str, text: str, reply_token: str) -> None:
    consumed = line_binding.consume_bind_code(text, line_user_id=user_id)
    if not consumed:
        _reply(reply_token, _("That code is not valid"))
        return
    # 码是本人在网页上领的，领码之后 LINE 可能已被管理员取消（团队管理那一勾）。
    if not binding.enabled(consumed["user"]):
        _reply(reply_token, _("LINE is not enabled for this account"))
        return
    profile = line.get_profile(user_id) or {}
    line_binding.bind(user_id, consumed["user"], profile.get("displayName") or "")
    menu.request_sync(user_id)
    name = frappe.db.get_value("User", consumed["user"], "username") or consumed["user"]
    _reply(reply_token, _("Bound to {0}").format(name))


def handle(event: dict) -> bool:
    """返回 True 表示这个事件由 Cowork 处理了。回话用这个人手机上 LINE 的语言。"""
    if frappe.conf.get("pearnly_product") != "cowork":
        return False
    user_id = (event.get("source") or {}).get("userId") or ""
    if not user_id:
        return False
    previous = getattr(frappe.local, "lang", None)
    frappe.local.lang = line.user_language(user_id)
    menu.ensure(user_id, frappe.local.lang)
    try:
        return _handle(event, user_id)
    finally:
        frappe.local.lang = previous


def _as(user, work):
    """签名过的 webhook 仍是 Guest 请求：业务动作按绑定的这个账号做（不用 Administrator），做完还原。"""
    previous = frappe.session.user
    try:
        frappe.set_user(user)
        return work()
    finally:
        frappe.set_user(previous)


def _usable(bound, reply_token) -> bool:
    """已绑定、LINE 开着、有录入权限；否则回一句原因。"""
    if not bound:
        _reply(reply_token, _("Bind this account first"))
    elif not binding.enabled(bound):
        _reply(reply_token, _("LINE is not enabled for this account"))
    elif not flow.allowed(bound):
        _reply(reply_token, _("This account cannot send documents"))
    else:
        return True
    return False


def _postback(user_id, bound, data, reply_token) -> bool:
    if data.startswith("a=work"):
        # 旧菜单的「ประสานงาน」：工作协调要重新设计，还没开放（用户 2026-09-26）。
        _reply(reply_token, _("This channel is not open yet"))
        return True
    if not (flow.is_start(data) or flow.is_step(data)):
        return False
    if _usable(bound, reply_token):
        if flow.is_start(data):
            _as(bound, lambda: flow.start(user_id, reply_token))
        else:
            _as(bound, lambda: flow.answer(user_id, data, reply_token))
    return True


def _document(user_id, bound, message, reply_token):
    if not _usable(bound, reply_token):
        return
    choices = flow.choices(user_id)
    if not flow.ready(choices):
        _reply(reply_token, _("Tap Send documents to ERP in the menu first"))
        return
    try:
        _as(bound, lambda: intake.receive(line_user_id=user_id, bound=bound, message=message,
                                          reply_token=reply_token, choices=choices))
    except Exception:
        frappe.log_error(title="Cowork LINE document queue failed", message=frappe.get_traceback()[-1500:])
        _reply(reply_token, _("Could not store this document"))


def _handle(event: dict, user_id: str) -> bool:
    etype = event.get("type")
    reply_token = event.get("replyToken") or ""
    if etype == "follow":
        _reply(reply_token, _("Send the code from the Cowork page to bind"))
        return True
    if etype == "unfollow":
        line_binding.unbind(user_id)
        menu.request_sync(user_id)
        return True
    bound = line_binding.user_of(user_id)
    if etype == "postback":
        return _postback(user_id, bound, (event.get("postback") or {}).get("data") or "", reply_token)
    if etype != "message":
        return False
    message = event.get("message") or {}
    if message.get("type") in ("image", "file"):
        _document(user_id, bound, message, reply_token)
        return True
    text = (message.get("text") or "").strip() if message.get("type") == "text" else ""
    if text and text.casefold() in {word.casefold() for word in UNBIND_WORDS}:
        if bound:
            line_binding.unbind(user_id)
            menu.request_sync(user_id)
            _reply(reply_token, _("LINE unbound"))
        else:
            _reply(reply_token, _("Bind this account first"))
        return True
    if not bound:
        if text:
            _bind_with_code(user_id, text, reply_token)
        else:
            _reply(reply_token, _("Bind this account first"))
        return True
    if not binding.enabled(bound):
        _reply(reply_token, _("LINE is not enabled for this account"))
        return True
    if text.casefold() in {word.casefold() for word in flow.MENU_WORDS} and flow.allowed(bound):
        flow.menu(reply_token)
        return True
    # 正在选账套：打的字就是找账套的关键词（用户 2026-09-27）。
    if text and flow.allowed(bound) and _as(bound, lambda: flow.search(user_id, text, reply_token)):
        return True
    _reply(reply_token, _("Tap the menu to start"))
    return True
