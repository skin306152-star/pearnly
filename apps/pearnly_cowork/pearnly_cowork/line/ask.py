"""LINE 识别完：先把这张票要人定的题问完，最后才出卡（用户 11-19「所有问题问完 … 然后保存编辑或者取消，一气呵成」；评估
``docs/work/2026-11-19-line-ask-all-before-card-评估.md`` 第 1、3、4、7、10 节）。

一张票 = 一条识别记录。推往第三方账本（Express / MR.ERP）的票依次：

1. 按行追问（`line.items`：新物料怎么处理、多仓选仓；有「全部新建 / 全部记费用」一次答完整张）；
2. 写账前置判定剩下的题（账套、存货科目组、Express 对应项、现 / 赊……）：共用出题器 `pearnly_core.write_precheck.line_ask`，判定与记答同
   CC 卡片（产品钩子 ``pearnly_write_precheck`` → `assistant.precheck_source`）；票归哪家公司、每题开头那行经
   `assistant.precheck_source.line_ticket`（line → assistant 只经这一个函数）。推荐项排第一个按钮，点了才算；
3. 出卡（`line.cards`，保存 / 编辑 / 取消），卡上列答过的结果。

「仅识别」的票、没有题的票直接出卡。一批几张逐张问完、逐张出卡；答题中又来新票排在后面，答案只记给当前这一张，不串单。
提问里点「取消」= 卡片「取消」（`line.save.drop` → `card_save.cancel`），回「已取消」，接着问排着的下一张。
进度存在本人的 LINE 会话里（与上传前的选择同一个会话、30 分钟，键 :data:`TICKETS` 与两个出题器各自的键）；过期后旧按钮无效、不出卡
（票照常在网页列表里）。判定出错、归不上公司的票不问，直接出卡（卡上保存照现有兜底停下回原因）。
LINE 一次回复最多 5 条：前 5 条用回复令牌，其余推送。
"""
import frappe
from frappe import _

from pearnly_core import line
from pearnly_core.write_precheck import line_ask

from pearnly_cowork.line import flow, items

TICKETS = "tickets"
STEP = "a=precheck"  # 共用出题器的按钮（= ``line_ask.ACTION``；字面量：产品地图按它认入口）
ITEMS, PRECHECK = "items", "precheck"  # 当前这一张问到哪一段
REPLY_MAX = 5


def is_step(data) -> bool:
    """出卡前提问的按钮：共用出题器的（a=precheck）与按行追问的（a=cowork_item）。"""
    return line_ask.is_step(data) or items.is_step(data)


def _state(payload) -> dict:
    state = payload.get(TICKETS)
    if not isinstance(state, dict):
        state = payload[TICKETS] = {"current": None, "queue": []}
    state.setdefault("current", None)
    state.setdefault("queue", [])
    return state


def send(line_user_id, messages, reply_token=""):
    if not messages:
        return
    line.reply_or_push(line_user_id, messages[:REPLY_MAX], reply_token)
    for start in range(REPLY_MAX, len(messages), REPLY_MAX):
        line.push(line_user_id, messages[start:start + REPLY_MAX])


def _card(ticket) -> list:
    from pearnly_cowork.line import cards

    return cards.previews([ticket["name"]], ticket["card"], {ticket["name"]: ticket.get("answers") or []})


def _cancel(ticket) -> list:
    from pearnly_cowork.line import save

    save.drop(ticket["name"])
    return [{"type": "text", "text": _("Cancelled")}]


# ── 一张票的几段 ──────────────────────────────────────────────────────────────


def _precheck(payload, ticket, messages) -> bool:
    """写账前置判定剩下的题：有题回 True（这一张成了当前在问的）；没题、判不了、归不上公司直接出卡，回 False。"""
    state = _state(payload)
    state["current"] = ticket
    ticket["stage"] = PRECHECK
    outcome = None
    try:
        from pearnly_cowork.assistant import precheck_source

        found = precheck_source.line_ticket(ticket["name"])
        if found.get("company"):
            outcome = line_ask.enqueue(payload, found["company"], [ticket["name"]], ref=ticket["name"],
                                       heading=found.get("heading") or "")
    except Exception:
        # 判定出错不能吞掉卡片：这一张不问、照出卡（保存时照现有兜底）
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Cowork LINE precheck question failed", message=frappe.get_traceback()[-1500:])
        line_ask.stop(payload)
    if outcome is None:
        state["current"] = None
        messages.extend(_card(ticket))
        return False
    return _settle(payload, outcome, messages)


def _settle(payload, outcome, messages) -> bool:
    """出题器交回的结果：问完的出卡（带答过的结果）、取消的作废，再接它要发的消息（下一题、重问、旧按钮无效）。回还在不在问这一张。"""
    state = _state(payload)
    ticket = state.get("current")
    for row in outcome.get("finished") or ():
        if ticket and row.get("ref") == ticket["name"]:
            ticket.setdefault("answers", []).extend(
                {"label": " · ".join(part for part in (answer.get("title"), answer.get("label")) if part),
                 "value": answer.get("value_label") or answer.get("value")}
                for answer in row.get("answers") or ())
            messages.extend(_card(ticket))  # loop-query: 只认 ref 对上的当前这一张，每次最多一张
            state["current"] = ticket = None
    for row in outcome.get("cancelled") or ():
        if ticket and row.get("ref") == ticket["name"]:
            messages.extend(_cancel(ticket))  # loop-query: 只认 ref 对上的当前这一张，每次最多一张
            state["current"] = ticket = None
    messages.extend(outcome.get("messages") or ())
    return bool(state.get("current"))


def _start(payload, ticket, messages) -> bool:
    """开始问一张：先按行追问，没有再问写账前置判定。回这一张是不是在等答。"""
    try:
        question = items.start(payload, ticket["name"])
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
        frappe.log_error(title="Cowork LINE item question failed", message=frappe.get_traceback()[-1500:])
        question = None
    if question:
        ticket["stage"] = ITEMS
        _state(payload)["current"] = ticket
        messages.append(question)
        return True
    return _precheck(payload, ticket, messages)


def _run(payload, messages) -> list:
    """当前没有在问的：排着的票逐张开始，有题的出第一题、停在这一张；没题的直接出卡。"""
    state = _state(payload)
    if not state.get("current"):
        line_ask.stop(payload)  # 不该有残留的提问（例如上一张出错时）
        payload.pop(items.ASKING, None)
    while not state.get("current") and state["queue"]:
        if _start(payload, state["queue"].pop(0), messages):  # loop-query: 一次上传的几张票，有题即停、逐张出卡
            break
    return messages


# ── 对外入口 ──────────────────────────────────────────────────────────────────


def arrive(line_user_id, records, pending) -> list:
    """识别完一份（`intake.announce`：已切成绑定的这个账号、按本人的语言）：回这次要发的消息。``pending`` 是上传时记下的这次选择。"""
    from pearnly_cowork.line import cards

    card = {key: pending.get(key) for key in ("direction", "target", "mode")}
    if not pending.get("push"):
        return cards.previews(records, card)
    with flow.locked(line_user_id):
        payload = flow.choices(line_user_id)
        state = _state(payload)
        state["queue"].extend({"name": name, "card": card, "answers": []} for name in records)
        messages = [] if state.get("current") else _run(payload, [])
        flow.keep(line_user_id, payload)
    return messages


def touch(line_user_id):
    """又发了一张（webhook 里）：会话原样保留、30 分钟重新计时；按此刻会话里的存，不覆盖后台刚改过的题。"""
    with flow.locked(line_user_id):
        payload = flow.choices(line_user_id)
        if payload:
            flow.keep(line_user_id, payload)


def step(line_user_id, data, reply_token):
    """出卡前提问的按钮点回来。"""
    with flow.locked(line_user_id):
        payload = flow.choices(line_user_id)
        existed = bool(payload)
        state = _state(payload)
        ticket = state.get("current")
        messages = []
        if line_ask.is_step(data):
            _settle(payload, line_ask.postback(payload, data), messages)
        elif ticket and ticket.get("stage") == ITEMS:
            status, replies = items.answer(payload, data)
            messages.extend(replies)
            if status == items.DONE:
                ticket.setdefault("answers", []).extend(items.finish(payload))
                _precheck(payload, ticket, messages)
            elif status == items.CANCELLED:
                items.finish(payload)
                messages.extend(_cancel(ticket))
                state["current"] = None
        else:
            messages.append({"type": "text", "text": _("This option is no longer current")})
        if not state.get("current"):
            _run(payload, messages)
        if existed:
            flow.keep(line_user_id, payload)
    send(line_user_id, messages, reply_token)


def text(line_user_id, message, reply_token) -> bool:
    """答题中打的字：按行追问「选已有存货」那一步是找存货，出题器在等答时是打编号 / 搜索。不在这两种时回 False（交给别的处理）。"""
    with flow.locked(line_user_id):
        payload = flow.choices(line_user_id)
        state = _state(payload)
        ticket = state.get("current") or {}
        messages = []
        if ticket.get("stage") == ITEMS:
            found = items.search(payload, message)
            if found is None:
                return False
            messages.extend(found)
        elif ticket.get("stage") == PRECHECK and line_ask.asking(payload):
            outcome = line_ask.text(payload, message)
            if outcome is None:
                return False
            _settle(payload, outcome, messages)
            if not state.get("current"):
                _run(payload, messages)
        else:
            return False
        flow.keep(line_user_id, payload)
    send(line_user_id, messages, reply_token)
    return True


__all__ = ["ITEMS", "PRECHECK", "STEP", "TICKETS", "arrive", "is_step", "send", "step", "text", "touch"]
