"""LINE 识别卡片「保存」（用户 11-18）：按钮出不出、点了之后回什么。推送本身在 `push.application.card_save`。

保存之后（用户 11-22，`pearnly_core.line_ui.receipt`）：推出去了先显示 LINE 自带的三个点，推送回执落库（票上的推送状态变了，
``cowork_push_state_changed`` → :func:`pushed`）时用这次的回复令牌回「成功 · Express 单号」/「失败 · 原因」+【编辑】；等不到回
「录入中」+【查看结果】。科目表还在读时同样先三个点，读好了（连接读完，`connections.refresh`）接着保存并回结果；等不到回原来那句
+【编辑】【查看结果】（点了重试保存）。状态词同录入列表（录入中 / 成功 / 失败）。"""
from urllib.parse import urlencode

import frappe
from frappe import _

SAVE_ACTION = "cowork_save"  # 卡片「保存」的 postback（`cards._card` 里写字面量：产品地图按它认入口）
CANCEL_ACTION = "cowork_cancel"  # 卡片「取消」
RESULT_ACTION = "cowork_result"  # 「录入中」后的【查看结果】（a=cowork_result&n=…，用户 11-22）
HISTORY = "Cowork OCR History"


def drop(name) -> None:
    """出卡前的提问里点了「取消」（`line.ask`，用户 11-19）：这张票照卡片「取消」处理（`card_save.cancel`）；回话由调用方发。
    出错记日志、不抛（取消不能把后面排着的票卡住）。"""
    try:
        from pearnly_cowork.push.application import card_save

        card_save.cancel(name)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Cowork LINE question cancel failed", message=frappe.get_traceback()[-1500:])


def key(name) -> str:
    """LINE 上在等这张票保存结果的键（`pearnly_core.line_ui.receipt`）。"""
    return f"cowork:save:{name}"


def _edit(name) -> dict:
    from pearnly_core.account import line_login

    uri = line_login.entry_url("/desk/pearnly-intake/new/" + name)
    return {"type": "action", "action": {"type": "uri", "label": str(_("Edit"))[:20], "uri": uri}}


def _view(name) -> dict:
    label = str(_("View result"))[:20]
    return {"type": "action", "action": {"type": "postback", "label": label,
                                         "data": urlencode({"a": "cowork_result", "n": name}),  # = RESULT_ACTION（字面量：产品地图按它认入口）
                                         "displayText": label}}


def _stopped(name, text) -> dict:
    return {"type": "text", "text": str(text or "-")[:2000], "quickReply": {"items": [_edit(name)]}}


def _entering(name) -> dict:
    return {"type": "text", "text": _("Entering"), "quickReply": {"items": [_view(name)]}}  # 同列表「录入中」


def _outcome(name):
    """推出去之后此刻的结果消息：成功 · Express 单号 / 失败 · 原因 +【编辑】；还在录入回 None；没推过回 False。"""
    from pearnly_cowork.push.application import card_save

    found = card_save.outcome(name)
    if found["status"] == "success":
        return {"type": "text", "text": " · ".join(part for part in (_("Succeeded"), found["docnum"]) if part)}
    if found["status"] == "failed":
        return _stopped(name, " · ".join(part for part in (_("Failed"), found["reason"]) if part))
    if found["status"] == "pending":
        return None
    return False


def _save(name, cancel=False):
    """保存（或取消）这张票：回 ``(这次的结果, 要回的消息)``；推出去在等回执时消息是 None。出错照原样回一句、什么都不推。"""
    try:
        from pearnly_cowork.push.application import card_save

        result = card_save.cancel(name) if cancel else card_save.save(name)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {}, {"type": "text", "text": frappe.utils.strip_html(str(error)) or _("Not permitted")}
    except Exception:
        # 意外的错（11-19 真机：一处写错的表名让保存直接抛错、LINE 一句都不回）：记日志，回「失败」，什么都不推
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Cowork LINE card save failed", message=frappe.get_traceback()[-1500:])
        return {}, {"type": "text", "text": _("Failed")}
    frappe.db.commit()
    if result.get("cancelled"):
        return result, {"type": "text", "text": _("Cancelled")}
    if result.get("saved") or result.get("kept"):
        return result, {"type": "text", "text": _("Saved")}
    if result.get("wait"):
        return result, {"type": "text", "text": str(result["stop"] or "-")[:2000],
                        "quickReply": {"items": [_edit(name), _view(name)]}}
    if result.get("stop"):
        return result, _stopped(name, result["stop"])
    return result, _outcome(name) or None


def reply(name, reply_token, cancel=False):
    """postback 入口：推出去了先三个点、等回执回结果（见模块说明）；停下回原因 + 快捷回复「编辑」。``cancel``：卡片「取消」。"""
    from pearnly_core import line
    from pearnly_core.line_ui import receipt as line_receipt

    result, message = _save(name, cancel)
    if message is not None and not result.get("wait"):
        line.reply(reply_token, [message])
        return
    fallback = [message] if message is not None else [_entering(name)]
    topics = [f"cowork:refresh:{result['connection']}"] if result.get("wait") and result.get("connection") else []
    started = line_receipt.start(key(name), _line_user(), reply_token, fallback,
                                 resume="pearnly_cowork.line.save.resume", args={"name": name}, topics=topics)
    if not started:
        line.reply(reply_token, fallback)
        return
    if not result.get("wait"):
        now = _outcome(name)  # 交单到这里之间回执已经到了：当场回结果
        if now:
            line_receipt.settle(key(name), [now])


def _line_user() -> str:
    """这次 webhook 事件的 LINE 用户（`handlers.handle` 开的 ``line.batch`` 记着收件人）。"""
    return (getattr(frappe.local, "pearnly_line_batch", None) or {}).get("to") or ""


def resume(name):
    """（`line_receipt` 的短任务，按发单人的身份与语言）推送回执到了、或科目表读好了：推出去了回结果（还在录入回 None 接着等）；
    还没推（在等科目表）就接着保存，停下回原因，又在等回 None。"""
    now = _outcome(name)
    if now:
        return [now]
    if now is None:
        return None
    result, message = _save(name)
    if result.get("wait") or message is None:
        return None
    return [message]


def pushed(history) -> None:
    """推送状态变了（``cowork_push_state_changed``，推送事务里）：LINE 上有人在等这张的结果就叫醒，提交后回结果。"""
    from pearnly_core.line_ui import receipt as line_receipt

    line_receipt.wake(key(history))


def show(name, reply_token) -> None:
    """【查看结果】：发单本人的这张此刻的状态（同录入列表的状态词）；还没推出去的照「保存」重试。"""
    from pearnly_core import line

    if frappe.db.get_value(HISTORY, name, "owner") != frappe.session.user:
        line.reply(reply_token, [{"type": "text", "text": _("Not permitted")}])
        return
    now = _outcome(name)
    if now is False:
        return reply(name, reply_token)
    line.reply(reply_token, [now or _entering(name)])
