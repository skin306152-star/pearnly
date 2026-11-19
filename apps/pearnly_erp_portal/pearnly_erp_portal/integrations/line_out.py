"""ERP 发给 LINE 用户的统一出口（reply / reply_or_push / push，转交 `pearnly_core.line`）。

这个 LINE 用户有题在等答（识别后出卡前的物料 / 仓库题、写账前置判定题，`line_questions`）时，ERP 发给他的任何一次消息末尾都重带
那道题：LINE 只在最后一条消息上挂快捷回复，回了别的（另一张卡的「已过账」「已取消」、没提交的原因、排队……）那道题的按钮就看不见了
（11-19 真机）。这次消息末尾本身已是一道带按钮的题（就是这道题，或别的流程正在问的一步）不重带，免得挡住它的按钮。

webhook 里的回复（:func:`reply` 只有回复令牌）按这次事件的 LINE 用户（`handlers.handle` 设的 :func:`receiving`）认人；
识别任务、收款结果等按收件人认。ERP 的 LINE 代码一律经这里发，不直接调 `pearnly_core.line` 的发送函数。
"""
from contextlib import contextmanager

import frappe

from pearnly_core import line, line_session

USER = "pearnly_erp_line_user"  # frappe.local 上这次 webhook 事件的 LINE 用户
# 先只看会话有没有题在等答，有才导入出题那一套（收款等别的流程发消息时不牵连它们）：
# = `line_flow.STATE`、`line_items.ASKING`、`pearnly_core.write_precheck.line_ask.KEY`（test_line_intake 核对）
STATE, ITEMS, PRECHECK = "erp_intake", "items", "precheck_ask"


@contextmanager
def receiving(line_user_id):
    previous = getattr(frappe.local, USER, None)
    setattr(frappe.local, USER, line_user_id)
    try:
        yield
    finally:
        setattr(frappe.local, USER, previous)


def _asks(message) -> bool:
    items = ((message if isinstance(message, dict) else {}).get("quickReply") or {}).get("items") or ()
    return any((item.get("action") or {}).get("type") == "postback" for item in items if isinstance(item, dict))


def _waiting(line_user_id) -> bool:
    session = line_session.get_session(line_user_id) or {}
    payload = session.get("payload") or {} if session.get("state") == STATE else {}
    return bool((payload.get(ITEMS) or {}).get("intake") or (payload.get(PRECHECK) or {}).get("current"))


def _with_question(line_user_id, messages) -> list:
    messages = list(messages or ())
    if not line_user_id or not messages or _asks(messages[-1]) or not _waiting(line_user_id):
        return messages
    from pearnly_erp_portal.integrations import line_questions

    try:
        question = line_questions.open_question(line_user_id)
    except Exception:
        frappe.log_error(title="ERP LINE open question failed", message=frappe.get_traceback()[-1500:])
        question = None  # 重带不出来不挡这次消息
    return messages + [question] if question else messages


REPLY_MAX = 5  # LINE 一次最多 5 条


def reply(reply_token, messages) -> bool:
    """回复令牌只能用一次、最多 5 条：已满 5 条就不重带。"""
    messages = list(messages or ())
    if len(messages) < REPLY_MAX:
        messages = _with_question(getattr(frappe.local, USER, None), messages)
    return line.reply(reply_token, messages)


def reply_or_push(to, messages, reply_token="", **kwargs) -> bool:
    """先回复后推送；重带那道题后超过 5 条的，余下的推送。"""
    messages = _with_question(to, messages)
    sent = line.reply_or_push(to, messages[:REPLY_MAX], reply_token, **kwargs)
    for start in range(REPLY_MAX, len(messages), REPLY_MAX):
        line.push(to, messages[start:start + REPLY_MAX])
    return sent


def push(to, messages, **kwargs) -> bool:
    messages = _with_question(to, messages)
    sent = line.push(to, messages[:REPLY_MAX], **kwargs)
    for start in range(REPLY_MAX, len(messages), REPLY_MAX):
        line.push(to, messages[start:start + REPLY_MAX])
    return sent


__all__ = ["ITEMS", "PRECHECK", "REPLY_MAX", "STATE", "USER", "push", "receiving", "reply", "reply_or_push"]
