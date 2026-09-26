"""Cowork 的 LINE 事件处理器（通道侧）。

传输层 `pearnly_core.line` 负责验签、幂等与回执；这里只做绑定 / 解绑。
LINE 里的业务入口（发单据、盘点等）这一版不做（用户 2026-09-26：先做配置 + 绑定），
已绑定的人发别的消息回"通道还没开放"。
"""
import frappe
from frappe import _

from pearnly_core import line, line_binding

from pearnly_cowork.line import binding

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
    try:
        return _handle(event, user_id)
    finally:
        frappe.local.lang = previous


def _handle(event: dict, user_id: str) -> bool:
    etype = event.get("type")
    reply_token = event.get("replyToken") or ""
    if etype == "follow":
        _reply(reply_token, _("Send the code from the Cowork page to bind"))
        return True
    if etype == "unfollow":
        line_binding.unbind(user_id)
        return True
    if etype != "message":
        return False
    bound = line_binding.user_of(user_id)
    message = event.get("message") or {}
    text = (message.get("text") or "").strip() if message.get("type") == "text" else ""
    if text and text.casefold() in {word.casefold() for word in UNBIND_WORDS}:
        if bound:
            line_binding.unbind(user_id)
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
    _reply(reply_token, _("This channel is not open yet"))
    return True
