"""Cowork 的 LINE 事件处理器（通道侧）。

传输层 `pearnly_core.line` 负责验签、幂等与回执；这里只做分派：绑定 / 解绑、
「发单据进 ERP」的选择（`flow`）、收单据（`intake`）、识别后出卡前的提问（`ask`：按钮 a=precheck / a=cowork_item，答题中打的字），
菜单跟着权限与手机语言走（`menu`）。
盘点在富菜单上直接打开网页端盘点页，不经过这里；工作协调要重新设计，还没有入口（用户 2026-09-26）。
"""
import frappe
from frappe import _

from pearnly_core import line, line_binding

from pearnly_cowork.line import binding, flow, intake, menu

# 解绑词是用户手打的，按中英泰日四套写法都认（与 ERP 同一组）。
UNBIND_WORDS = ("ยกเลิกการผูก", "ยกเลิกการเชื่อมต่อ", "解绑", "unbind", "連携解除")


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
    from pearnly_core.account.names import display_name

    _reply(reply_token, _("Bound to {0}").format(display_name(consumed["user"])))  # 姓名（10-30），没填过就是用户名


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
        # 统一出口：这次反应里回给这个人的消息攒到最后一次发出；有题在等答时末尾重带当前这道题（`ask.reattach`，11-19：
        # 点另一张卡的保存 / 取消等回复成了最后一条，题的快捷回复就看不见了）
        with line.batch(user_id, event.get("replyToken") or ""):
            handled = _handle(event, user_id)
            from pearnly_cowork.line import ask

            ask.reattach(user_id)
        return handled
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
    from pearnly_cowork.line import ask, save

    if data.startswith("a=" + save.RESULT_ACTION + "&"):
        # 「录入中」后的【查看结果】（用户 11-22）：发单本人的这张此刻的状态
        if _usable(bound, reply_token):
            from urllib.parse import parse_qs

            name = (parse_qs(data).get("n") or [""])[0]
            _as(bound, lambda: save.show(name, reply_token))
        return True
    if data.startswith(("a=" + save.SAVE_ACTION + "&", "a=" + save.CANCEL_ACTION + "&")):
        # 卡片「保存」（用户 11-18）：直接推送这张票
        if _usable(bound, reply_token):
            from urllib.parse import parse_qs

            name = (parse_qs(data).get("n") or [""])[0]
            cancel = data.startswith("a=" + save.CANCEL_ACTION + "&")
            _as(bound, lambda: save.reply(name, reply_token, cancel=cancel))
        return True
    if ask.is_step(data):
        # 识别后、出卡前的提问（11-19）：按行追问存货对照（a=cowork_item）与写账前置判定的题（共用出题器 a=precheck）
        if _usable(bound, reply_token):
            _as(bound, lambda: ask.step(user_id, data, reply_token))
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
    # 正在选账套：打的字就是找账套的关键词（用户 2026-09-27）。只在重新点了菜单、停在选账套这一步时接，否则交给下面
    if text and flow.allowed(bound) and _as(bound, lambda: flow.search(user_id, text, reply_token)):
        return True
    # 出卡前的提问在等答：「选已有存货」打的是存货关键词（11-06），出题器的题打编号或名称 / 编码（11-19）；没在问就不接
    from pearnly_cowork.line import ask

    if text and flow.allowed(bound) and _as(bound, lambda: ask.text(user_id, text, reply_token)):
        return True
    _reply(reply_token, _("Tap the menu to start"))
    return True
