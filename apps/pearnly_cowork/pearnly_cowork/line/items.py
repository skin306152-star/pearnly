"""LINE 识别后按行追问「存货对照」（11-06 按行选物料与库存，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 第 15 节 C7）。

上传前不再问整批「服务 / 存货」。识别完发卡片时，这张票推往的 Express 账套里有定不了的行（共用写账规则 `choices.resolve` 的
``needed`` 里 ``kind = item`` 且 ``expense``：没声明过账类型、没记过、名字也对不上目录），就在同一次发送里卡片后面跟一条带快捷回复的提问：

  供应商 · 单号 / 新物料：1. … 2. …   [全部新建存货] [全部记费用] [逐项选择] [取消]

「逐项选择」一项一问：[新建存货] [记费用] [选已有存货] [取消]；「选已有存货」请打名称或编码，在这份账套的存货（库存品与非库存品）里找，
列序号按钮。答案经推送服务 `save_choices` 记进账套记忆（同复核页、CC 卡片；推送侧经钩子 ``cowork_line_items``），之后推送照它走。一批几张票一次只问一张，问完一张接下一张。
取消只停这张的提问，不改上传前的选择；没答的行照推送时的规则（停在待处理里等人选，不硬拦）。

追问的进度存在本人的 LINE 会话里（与上传前的选择同一个会话、同样 30 分钟，键 ``ASKING``）；按钮只认当前在问的这一项。
"""
from urllib.parse import parse_qs, urlencode

import frappe
from frappe import _

from pearnly_core import line

ASKING = "items"
ACTION = "cowork_item"
MAX_RESULTS = 12


def is_step(data) -> bool:
    return (parse_qs(data or "").get("a") or [""])[0] == ACTION


def _button(label, **params):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "displayText": label,
                                         "data": urlencode({"a": ACTION, **params})}}


def _message(text, items):
    return {"type": "text", "text": text, "quickReply": {"items": items[:12] + [_button(_("Cancel"), v="cancel")]}}


def _reply(reply_token, messages):
    line.reply(reply_token, messages if isinstance(messages, list) else [messages])


# ── 识别完：算这张票要问的行（推送侧经钩子 ``cowork_line_items``，LINE 不导入推送 / 单据域）────────────────


def _side(name):
    paths = frappe.get_hooks("cowork_line_items") or []
    return frappe.get_attr(f"{paths[-1]}.{name}") if paths else None


def rows_of(history) -> list:
    """这张票推往的 Express 账套里定不了的行 ``[{subject, label}]``（推送侧 `push.application.line_items.rows`）。"""
    rows = _side("rows")
    return rows(history) if rows else []


def _title(history) -> str:
    title = _side("title")
    return title(history) if title else ""


def first_question(history, rows) -> dict:
    """整张的提问：新物料列全（文字没有按钮个数的限制），按钮固定四个。"""
    lines = [_title(history), _("cowork.line.items.new")] + [f"{index}. {row['label']}" for index, row in enumerate(rows, 1)]
    return _message("\n".join(part for part in lines if part), [
        _button(_("cowork.line.items.allNew"), v="all_new"),
        _button(_("cowork.line.items.allExpense"), v="all_expense"),
        _button(_("cowork.line.items.each"), v="each")])


def _row_question(asking) -> dict:
    row = asking["rows"][asking["i"]]
    head = f"{asking['i'] + 1}/{len(asking['rows'])} · {row['label']}"
    return _message(head, [_button(_("cowork.line.items.newOne"), v="new", i=asking["i"]),
                           _button(_("cowork.choice.expenseLine"), v="expense", i=asking["i"]),
                           _button(_("cowork.line.items.pick"), v="pick", i=asking["i"])])


def queue(line_user_id, history) -> dict | None:
    """识别完一张：这张有定不了的行就排进本人的追问；现在没在问别的就回这张的提问（随卡片一起发），否则排队等前一张问完。"""
    from pearnly_cowork.line import flow

    rows = rows_of(history)
    if not rows:
        return None
    payload = flow.choices(line_user_id)
    asking = payload.get(ASKING)
    if asking and (asking.get("rows") or asking.get("queue")):
        asking.setdefault("queue", []).append(history)
        flow.keep(line_user_id, payload)
        return None
    payload[ASKING] = {"history": history, "rows": rows, "i": None, "queue": []}
    flow.keep(line_user_id, payload)
    return first_question(history, rows)


# ── 作答 ─────────────────────────────────────────────────────────────────────


def _save(history, answers):
    """记进这张票推往的账套的记忆（同复核页「要选的项」）：``answers`` = ``[(row, value)]``。"""
    _side("save")(history, [{"kind": "item", "subject": row["subject"], "value": value, "label": row["label"]}
                            for row, value in answers])


def _next_doc(line_user_id, payload, reply_token, done_text=True):
    """这张问完：排着的下一张有要问的就问，没有就回「已保存」。"""
    from pearnly_cowork.line import flow

    asking = payload.get(ASKING) or {}
    pending = list(asking.get("queue") or [])
    while pending:
        history = pending.pop(0)
        rows = rows_of(history)
        if rows:
            payload[ASKING] = {"history": history, "rows": rows, "i": None, "queue": pending}
            flow.keep(line_user_id, payload)
            messages = [{"type": "text", "text": _("Saved")}] if done_text else []
            return _reply(reply_token, messages + [first_question(history, rows)])
    payload.pop(ASKING, None)
    flow.keep(line_user_id, payload)
    return _reply(reply_token, {"type": "text", "text": _("Saved") if done_text else _("Cancelled")})


def answer(line_user_id, data, reply_token):
    """快捷回复点回来的一步。只认当前在问的这一张、这一项；旧按钮点了回「这个选项已经不是当前的」。"""
    from pearnly_core.express_rules.push.choices import EXPENSE, NEW
    from pearnly_cowork.line import flow

    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    value = params.get("v") or ""
    payload = flow.choices(line_user_id)
    asking = payload.get(ASKING)
    if not asking or not asking.get("rows"):
        return _reply(reply_token, {"type": "text", "text": _("This option is no longer current")})
    if value == "cancel":
        return _next_doc(line_user_id, payload, reply_token, done_text=False)
    rows, current = asking["rows"], asking.get("i")
    if value in ("all_new", "all_expense", "each"):
        if current is not None:
            return _reply(reply_token, {"type": "text", "text": _("This option is no longer current")})
        if value == "each":
            asking["i"] = 0
            flow.keep(line_user_id, payload)
            return _reply(reply_token, _row_question(asking))
        _save(asking["history"], [(row, NEW if value == "all_new" else EXPENSE) for row in rows])
        return _next_doc(line_user_id, payload, reply_token)
    if current is None or str(params.get("i", "")) != str(current):
        return _reply(reply_token, {"type": "text", "text": _("This option is no longer current")})
    if value == "pick":
        asking["search"] = True
        flow.keep(line_user_id, payload)
        return _reply(reply_token, _message(_("cowork.line.items.type"), []))
    if value in ("new", "expense"):
        return _answered(line_user_id, payload, reply_token, NEW if value == "new" else EXPENSE)
    if value == "o":
        offered = asking.get("offered") or []
        try:
            code = offered[int(params.get("k", ""))]
        except (ValueError, IndexError):
            return _reply(reply_token, {"type": "text", "text": _("This option is no longer current")})
        return _answered(line_user_id, payload, reply_token, code)
    return _reply(reply_token, {"type": "text", "text": _("This option is no longer current")})


def _answered(line_user_id, payload, reply_token, value):
    from pearnly_cowork.line import flow

    asking = payload[ASKING]
    _save(asking["history"], [(asking["rows"][asking["i"]], value)])
    asking.pop("search", None)
    asking.pop("offered", None)
    asking["i"] += 1
    if asking["i"] < len(asking["rows"]):
        flow.keep(line_user_id, payload)
        return _reply(reply_token, _row_question(asking))
    return _next_doc(line_user_id, payload, reply_token)


def search(line_user_id, text, reply_token) -> bool:
    """在「选已有存货」这一步收到的文字＝名称或编码：在这份账套的存货里找（库存品与非库存品），列序号按钮。
    不在这一步返回 False。"""
    from pearnly_cowork.line import flow

    payload = flow.choices(line_user_id)
    asking = payload.get(ASKING) or {}
    if not asking.get("search") or asking.get("i") is None or not str(text or "").strip():
        return False
    find = _side("search")
    options = [row for row in (find(asking["history"], str(text).strip()[:60], MAX_RESULTS + 1) if find else [])
               if row.get("code")]
    if not options:
        _reply(reply_token, [{"type": "text", "text": _("No match for “{0}”").format(str(text).strip()[:40])},
                             _row_question(asking)])
        return True
    if len(options) > MAX_RESULTS:
        _reply(reply_token, _message(_("Too many matches; type a few more letters to narrow it down"), []))
        return True
    asking["offered"] = [row["code"] for row in options]
    flow.keep(line_user_id, payload)
    lines = [_("Found {0}. Tap the number to choose:").format(len(options))]
    lines += [f"{index}. {' '.join(part for part in (row['code'], row.get('name')) if part)}"
              for index, row in enumerate(options, 1)]
    _reply(reply_token, _message("\n".join(lines), [_button(str(index), v="o", i=asking["i"], k=index - 1)
                                                    for index in range(1, len(options) + 1)]))
    return True


__all__ = ["ACTION", "ASKING", "answer", "first_question", "is_step", "queue", "rows_of", "search"]
