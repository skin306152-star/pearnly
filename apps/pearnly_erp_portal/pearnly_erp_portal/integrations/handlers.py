"""ERP 的 LINE 事件处理器（通道侧）。

传输层（`pearnly_core.line`）负责验签、幂等与回执；这里只做 ERP 的分派：
绑定 / 解绑、菜单与录单前的选择（`line_flow`）、收单据（`intake`）。
业务口径见 docs/work/2026-09-25-erp-line-intake.md（用户 2026-09-25 定稿）。
"""
from urllib.parse import parse_qs

import frappe
from frappe import _

from pearnly_core import line, line_binding
from pearnly_core.write_precheck import line_ask

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.integrations import (intake, line_cards, line_collection, line_flow, line_items, line_menu, line_out,
                                             line_questions)

# 解绑指令词是**用户手打的词**，所以按通道语言认，并容错中英泰三套写法：
# 认不出就等于这个功能对用户不存在。日文同 DMS（連携解除，11-08）。
UNBIND_WORDS = ("ยกเลิกการผูก", "ยกเลิกการเชื่อมต่อ", "解绑", "unbind", "連携解除")


def _is_unbind(text: str) -> bool:
    return text.strip().casefold() in {w.casefold() for w in UNBIND_WORDS}


def _display_name(user: str) -> str:
    """LINE 绑定回执里的人：姓名（共用显示名，用户 10-30）。"""
    from pearnly_core.account import names

    return names.display_name(user)


def _bind_with_code(user_id: str, text: str, reply_token: str) -> None:
    """把这条文本当绑定码试一次。认不出就回"码不对"。"""
    consumed = line_binding.consume_bind_code(text, line_user_id=user_id)
    if not consumed:
        line_out.reply(reply_token, [{"type": "text", "text": _("That code is not valid")}])
        return
    # 码是本人在网页上领的，但领码之后 LINE 可能已被管理员取消（团队管理那一勾）。
    if not guard.has("integration.manage", user=consumed["user"]):
        line_out.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return
    profile = line.get_profile(user_id) or {}
    line_binding.bind(user_id, consumed["user"], profile.get("displayName") or "")
    # 绑上就挂按岗位的那套菜单；回执里同时给 ซื้อ / ขาย 两个原生按钮（旧线上绑定后回菜单）。
    line_menu.request_sync(user_id)
    line_out.reply(reply_token, [{"type": "text", "text": _("Bound to {0}").format(
        _display_name(consumed["user"]))}])


def _receive_document(user_id: str, bound: str, message: dict, reply_token: str) -> None:
    """收到图片/PDF：必须先选好方向与各项；按这组选择留底并排队识别。"""
    choices = line_flow.choices(user_id)
    if not line_flow.ready(choices):
        line_out.reply(reply_token, [{"type": "text", "text": _("Choose purchase or sales first")}])
        return
    if not guard.has(intake.DIRECTION_CAPABILITY[choices["direction"]], user=bound):
        line_out.reply(reply_token, [{"type": "text", "text": _("This account cannot send documents")}])
        return
    media_type = message.get("type") or ""
    filename = message.get("fileName") or ""
    if not filename:
        filename = f"line-{message.get('id') or 'document'}.jpg" if media_type == "image" \
            else f"line-{message.get('id') or 'document'}.pdf"
    try:
        # 不在回调里下载（用户 09-25：发图片时回调里同步下载，LINE 先断开，访问日志 499）：
        # 这里只登记、排队，图片在识别任务里再从 LINE 取（`intake.run` → `_fetch`）。
        # A signed webhook is still a Guest request. Use only the verified local
        # binding, never Administrator, and restore it before another event runs.
        previous_user = frappe.session.user
        try:
            frappe.set_user(bound)
            # 同一组选择可以连发多张：每张各排一次识别、各出一张卡；选择的 30 分钟重新计时。
            line_flow.keep(user_id, choices)
            intake.receive(line_user_id=user_id, sent_by=bound, choices=choices,
                           media_type=media_type, content=None, filename=filename,
                           line_message_id=message.get("id") or "", reply_token=reply_token)
        finally:
            frappe.set_user(previous_user)
    except Exception:
        # 日志标题也走英文：`check-i18n` 的存量清单已经够长，新代码不再往里加中文。
        frappe.log_error(title="ERP LINE document storage failed",
                         message=frappe.get_traceback()[-1500:])
        line_out.reply(reply_token, [{"type": "text", "text": _("Could not store this document")}])


def _as(bound, function, *args):
    """识别后追问要按绑定的这个账号查物料、算卡片金额（webhook 本身是 Guest），做完还原。"""
    previous = frappe.session.user
    try:
        frappe.set_user(bound)
        return function(*args)
    finally:
        frappe.set_user(previous)


def _postback(user_id: str, bound, data: str, reply_token: str, event_id: str = "") -> bool:
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    action = params.get("a") or ""
    if not (action.startswith("mode:") or action in ("erp", line_items.ACTION, line_ask.ACTION, line_collection.ACTION,
                                                     line_cards.SAVE_ACTION, line_cards.CANCEL_ACTION)):
        return False
    if not bound:
        line_out.reply(reply_token, [{"type": "text", "text": _("Bind this account first")}])
        return True
    if not guard.has("integration.read", user=bound):
        line_out.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return True
    if action.startswith("mode:"):
        # 富菜单（新旧两套都是 `a=mode:purchase|sales`）与「เมนู」回的按钮。
        line_flow.start(user_id, bound, action[len("mode:"):], reply_token)
    elif action == line_collection.ACTION:
        # 收款（富菜单第 4 格、「เมนู」里的按钮、收款各步与确认卡）：权限与规则在 `collection.service`
        line_collection.answer(user_id, bound, data, reply_token, event_id)
    elif action == line_cards.SAVE_ACTION:
        _as(bound, _save_card, params.get("n") or "", reply_token)  # 卡片「保存」（用户 11-18）
    elif action == line_cards.CANCEL_ACTION:
        _as(bound, _save_card, params.get("n") or "", reply_token, intake.cancel_card)  # 卡片「取消」（用户 11-19）
    elif action == line_items.ACTION:
        _as(bound, line_items.answer, user_id, bound, data, reply_token)  # 识别后按行追问（11-06 按行选物料与库存）
    elif line_questions.is_step(data):
        # 出卡前问写账前置判定的题（共用出题器，用户 11-19 问完再出卡）
        _as(bound, line_questions.answer, user_id, bound, data, reply_token)
    else:
        line_flow.answer(user_id, bound, data, reply_token)
    return True


def _save_card(name, reply_token, act=None):
    """卡片「保存」：提交成功回单号；有要人定的项、或提交时报错（缺仓库、按台未登记、非本人…）就不入账，回原因 + 「编辑」。
    ``act`` = ``intake.cancel_card`` 时是卡片「取消」：回「已取消」（已入账的回原单号）。点过取消再点保存同样只回「已取消」。"""
    try:
        result = (act or intake.save_card)(name)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        text = frappe.utils.strip_html(str(error) or "") or _("Not permitted")
        frappe.clear_messages()
        doc = frappe.db.exists(intake.DOCTYPE, name) and frappe.get_doc(intake.DOCTYPE, name)
        message = line_cards.stopped(doc, text) if doc and doc.sent_by == frappe.session.user else {"type": "text", "text": text}
        line_out.reply(reply_token, [message])
        return
    except Exception:
        # 意外的错：记日志，回「失败」，什么都不入账（同 Cowork 卡片，11-19 真机）
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="ERP LINE card save failed", message=frappe.get_traceback()[-1500:])
        line_out.reply(reply_token, [{"type": "text", "text": _("Failed")}])
        return
    if result.get("cancelled"):
        line_out.reply(reply_token, [{"type": "text", "text": _("Cancelled")}])
        return
    doc = frappe.get_doc(intake.DOCTYPE, name)
    if result.get("needed"):
        line_out.reply(reply_token, [line_cards.stopped(doc, line_cards.needed_text(doc, result["needed"]))])
        return
    line_out.reply(reply_token, [line_cards.saved(doc, result)])


def handle(event: dict) -> bool:
    """ERP 处理入口。返回 True 表示这个事件由 ERP 处理了。

    回给这个人的话、按钮都用他手机上 LINE 的语言（用户 2026-09-26），处理完还原。"""
    user_id = (event.get("source") or {}).get("userId") or ""
    previous = getattr(frappe.local, "lang", None)
    if user_id:
        frappe.local.lang = line.user_language(user_id, default=intake.channel_language())
        line_menu.ensure(user_id, frappe.local.lang)
    try:
        with line_out.receiving(user_id):  # 这次事件的回复末尾重带此人在等答的题（`line_out`）
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
        line_out.reply(reply_token, [{"type": "text", "text": _("Send the code from the ERP page to bind")}])
        return True

    if etype == "unfollow":
        line_binding.unbind(user_id)
        line_menu.request_sync(user_id)
        return True

    bound = line_binding.user_of(user_id)
    if etype == "postback":
        return _postback(user_id, bound, (event.get("postback") or {}).get("data") or "", reply_token,
                         event.get("webhookEventId") or "")
    if etype != "message":
        return False
    message = event.get("message") or {}
    text = (message.get("text") or "").strip()

    if message.get("type") in ("image", "file"):
        if not bound:
            line_out.reply(reply_token, [{"type": "text", "text": _("Bind this account first")}])
        elif not guard.has("integration.read", user=bound):
            line_out.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        elif line_collection.wants_voucher(user_id):
            line_collection.receive_voucher(user_id, bound, message, reply_token)  # 收款里等的转账凭证
        else:
            _receive_document(user_id, bound, message, reply_token)
        return True

    if message.get("type") != "text":
        line_out.reply(reply_token, [{"type": "text", "text": _("This channel is not open yet")}])
        return True

    if _is_unbind(text):
        if bound:
            line_binding.unbind(user_id)
            line_menu.request_sync(user_id)
            line_out.reply(reply_token, [{"type": "text", "text": _("Unbound")}])
        else:
            line_out.reply(reply_token, [{"type": "text", "text": _("Bind this account first")}])
        return True

    if not bound:
        _bind_with_code(user_id, text, reply_token)
        return True

    if not guard.has("integration.read", user=bound):
        line_out.reply(reply_token, [{"type": "text", "text": _("LINE is not enabled for this account")}])
        return True
    if text.casefold() in line_flow.MENU_WORDS:
        line_flow.menu(bound, reply_token)
        return True
    if line_collection.text(user_id, bound, text, reply_token, event.get("webhookEventId") or ""):
        return True  # 正在录收款：打的字是客户关键字、金额或确认词（在「1 / 2」选方向之前认）
    if _as(bound, line_items.search, user_id, bound, text, reply_token):
        return True  # 正停在识别后追问的「选已有物料 / 仓库」：打的字是名称或编码
    if _as(bound, line_questions.text, user_id, bound, text, reply_token):
        return True  # 正在答出卡前的写前判定题：打的字是编号、名称或编码（菜单词、「ซื้อ / ขาย」不在这里认）
    if line_flow.search(user_id, bound, text, reply_token):
        return True  # 正停在选公司 / 供应商 / 客户 / 仓库：打的字是名称或编号
    direction = line_flow.DIRECTION_WORDS.get(text.casefold())
    if direction:
        line_flow.start(user_id, bound, direction, reply_token)
        return True
    line_out.reply(reply_token, [{"type": "text", "text": _("Tap the menu to start")}])
    return True
