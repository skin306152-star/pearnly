"""LINE 识别后按行追问「存货对照」（11-06 按行选物料与库存，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 第 15 节 C7）。

上传前不再问整批「服务 / 存货」。识别完、出卡之前（11-19 起卡片最后才发，`line.ask` 编排：先问这里，再问写账前置判定剩下的题），
这张票推往的 Express 账套里有定不了的行（共用写账规则 `choices.resolve` 的
``needed`` 里 ``kind = item`` 且 ``expense``：没声明过账类型、没记过、名字也对不上目录），就先发一条带快捷回复的提问：

  供应商 · 单号 / 新物料：1. … 2. …   [全部新建存货] [全部记费用] [逐项选择] [取消]

「逐项选择」一项一问：[新建存货] [记费用] [选已有存货] [取消]；「选已有存货」请打名称或编码，在这份账套的存货（库存品与非库存品）里找，
列序号按钮。多仓账套（在用的仓 ≥ 2）还没记过默认仓时，这些行问完（或点了「全部…」）接着问一次仓：快捷回复列在用的仓（主仓在前），
答案记成这份账套的默认仓（11-08，评估 docs/work/2026-11-07-cowork-express-warehouse-评估.md；一张票一个仓，不逐行问，要分仓去网页复核页改）。
没有要问的行就不单为仓问。答案经推送服务 `save_choices` 记进账套记忆（同复核页、CC 卡片；推送侧经钩子 ``cowork_line_items``），之后推送照它走。
这几题与写账前置判定的存货对照、仓是同一份 ``needed``（`assistant.precheck_source.choice_items`）；留在这里先问，是因为它有「全部新建 /
全部记费用」一次答完整张、「记费用」在大存货表里也有按钮，判定那边逐行一题一问。答完记进账套记忆，判定重算时这些行就是已定、不再问。

一张一张问、答完怎么接（写账前置判定、出卡）、取消（= 卡片「取消」）都在 `line.ask`；这里只出题、认按钮，状态存在交进来的 LINE 会话载荷
``payload[ASKING]`` 里（与上传前的选择同一个会话、同样 30 分钟），由 `line.ask` 存回。按钮只认当前在问的这一项。
"""
from urllib.parse import parse_qs, urlencode

import frappe
from frappe import _

ASKING = "items"
ACTION = "cowork_item"
MAX_RESULTS = 12
ANSWERS_MAX = 15  # 卡上列出的答案最多几行（一张气泡有大小上限）
# :func:`answer` 回的状态：还在问、这张问完了、点了取消、不是当前的按钮
PENDING, DONE, CANCELLED, STALE = "asking", "done", "cancelled", "stale"


def is_step(data) -> bool:
    return (parse_qs(data or "").get("a") or [""])[0] == ACTION


def _button(label, **params):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "displayText": label,
                                         "data": urlencode({"a": "cowork_item", **params})}}  # = ACTION（字面量：产品地图按它认入口）


def _message(text, items):
    return {"type": "text", "text": text, "quickReply": {"items": items[:12] + [_button(_("Cancel"), v="cancel")]}}


# ── 识别完：算这张票要问的行（推送侧经钩子 ``cowork_line_items``，LINE 不导入推送 / 单据域）────────────────


def _side(name):
    paths = frappe.get_hooks("cowork_line_items") or []
    return frappe.get_attr(f"{paths[-1]}.{name}") if paths else None


def rows_of(history) -> list:
    """这张票推往的 Express 账套里定不了的行 ``[{kind, subject, label}]``，多仓账套末尾可跟一项仓库（``kind = location``，带
    ``options``）（推送侧 `push.application.line_items.rows`）。"""
    rows = _side("rows")
    return rows(history) if rows else []


def _title(history) -> str:
    title = _side("title")
    return title(history) if title else ""


def _items(rows) -> list:
    return [row for row in rows if row.get("kind", "item") == "item"]


def first_question(history, rows) -> dict:
    """整张的提问：新物料列全（文字没有按钮个数的限制），按钮固定四个。"""
    lines = [_title(history), _("cowork.line.items.new")] + [f"{index}. {row['label']}"
                                                             for index, row in enumerate(_items(rows), 1)]
    return _message("\n".join(part for part in lines if part), [
        _button(_("cowork.line.items.allNew"), v="all_new"),
        _button(_("cowork.line.items.allExpense"), v="all_expense"),
        _button(_("cowork.line.items.each"), v="each")])


def _row_question(asking) -> dict:
    row = asking["rows"][asking["i"]]
    if row.get("kind") == "location":
        return _location_question(asking)
    head = f"{asking['i'] + 1}/{len(_items(asking['rows']))} · {row['label']}"
    return _message(head, [_button(_("cowork.line.items.newOne"), v="new", i=asking["i"]),
                           _button(_("cowork.choice.expenseLine"), v="expense", i=asking["i"]),
                           _button(_("cowork.line.items.pick"), v="pick", i=asking["i"])])


def _option_label(option) -> str:
    return " ".join(part for part in (option.get("code"), option.get("name")) if part)


def _location_question(asking) -> dict:
    """问仓：列在用的仓（主仓在前），点一个记成这份账套的默认仓。"""
    options = asking["rows"][asking["i"]].get("options") or []
    lines = [_("Warehouse")] + [f"{index}. {_option_label(row)}" for index, row in enumerate(options, 1)]
    return _message("\n".join(lines), [_button(_option_label(row), v="loc", i=asking["i"], k=index)
                                       for index, row in enumerate(options[:MAX_RESULTS])])


def start(payload, history) -> dict | None:
    """开始问这一张：有定不了的行就记进会话载荷、回第一条提问；没有回 None（不问）。"""
    rows = rows_of(history)
    if not rows:
        return None
    payload[ASKING] = {"history": history, "rows": rows, "i": None, "answers": []}
    return first_question(history, rows)


def finish(payload) -> list:
    """这一张问完（或取消）：从会话里拿掉，回答过的结果 ``[{label, value}]``（卡上列出）。"""
    return list((payload.pop(ASKING, None) or {}).get("answers") or ())


def active(payload) -> bool:
    return bool((payload.get(ASKING) or {}).get("rows"))


# ── 作答 ─────────────────────────────────────────────────────────────────────


def _stale():
    return STALE, [{"type": "text", "text": _("This option is no longer current")}]


def _save(asking, answers):
    """记进这张票推往的账套的记忆（同复核页「要选的项」）：``answers`` = ``[(row, value, 卡上显示的值)]``。"""
    _side("save")(asking["history"], [{"kind": row.get("kind", "item"), "subject": row["subject"], "value": value,
                                       "label": row["label"]} for row, value, _shown in answers])
    shown = asking.setdefault("answers", [])
    for row, _value, text in answers:
        if len(shown) < ANSWERS_MAX:
            shown.append({"label": str(row["label"] or _("Warehouse")), "value": str(text)})


def answer(payload, data) -> tuple:
    """快捷回复点回来的一步 → ``(状态, 要回的消息)``。只认当前在问的这一张、这一项；旧按钮回「这个选项已经不是当前的」。"""
    from pearnly_core.express_rules.push.choices import EXPENSE, NEW

    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    value = params.get("v") or ""
    asking = payload.get(ASKING)
    if not asking or not asking.get("rows"):
        return _stale()
    if value == "cancel":
        return CANCELLED, []
    rows, current = asking["rows"], asking.get("i")
    if value in ("all_new", "all_expense", "each"):
        if current is not None:
            return _stale()
        if value == "each":
            asking["i"] = 0
            return PENDING, [_row_question(asking)]
        shown = _("cowork.line.items.newOne") if value == "all_new" else _("cowork.choice.expenseLine")
        _save(asking, [(row, NEW if value == "all_new" else EXPENSE, shown) for row in _items(rows)])
        place = next((index for index, row in enumerate(rows) if row.get("kind") == "location"), None)
        if place is not None:  # 多仓账套：接着问一次仓
            asking["i"] = place
            return PENDING, [_location_question(asking)]
        return DONE, []
    if current is None or str(params.get("i", "")) != str(current):
        return _stale()
    row = rows[current]
    if value == "pick" and row.get("kind", "item") == "item":
        asking["search"] = True
        return PENDING, [_message(_("cowork.line.items.type"), [])]
    if value in ("new", "expense") and row.get("kind", "item") == "item":
        new = value == "new"
        return _answered(asking, NEW if new else EXPENSE, _("cowork.line.items.newOne") if new else _("cowork.choice.expenseLine"))
    if value == "loc" and row.get("kind") == "location":
        options = row.get("options") or []
        try:
            picked = options[int(params.get("k", ""))]
        except (ValueError, IndexError, KeyError):
            return _stale()
        return _answered(asking, picked["code"], _option_label(picked))
    if value == "o":
        offered, labels = asking.get("offered") or [], asking.get("offered_labels") or []
        try:
            index = int(params.get("k", ""))
            code = offered[index]
        except (ValueError, IndexError):
            return _stale()
        return _answered(asking, code, labels[index] if index < len(labels) else code)
    return _stale()


def _answered(asking, value, shown):
    _save(asking, [(asking["rows"][asking["i"]], value, shown)])
    asking.pop("search", None)
    asking.pop("offered", None)
    asking.pop("offered_labels", None)
    asking["i"] += 1
    if asking["i"] < len(asking["rows"]):
        return PENDING, [_row_question(asking)]
    return DONE, []


def search(payload, text) -> list | None:
    """在「选已有存货」这一步收到的文字＝名称或编码：在这份账套的存货里找（库存品与非库存品），回列序号按钮的消息。
    不在这一步回 None（交给别的处理）。"""
    asking = payload.get(ASKING) or {}
    if not asking.get("search") or asking.get("i") is None or not str(text or "").strip():
        return None
    find = _side("search")
    options = [row for row in (find(asking["history"], str(text).strip()[:60], MAX_RESULTS + 1) if find else [])
               if row.get("code")]
    if not options:
        return [{"type": "text", "text": _("No match for “{0}”").format(str(text).strip()[:40])}, _row_question(asking)]
    if len(options) > MAX_RESULTS:
        return [_message(_("Too many matches; type a few more letters to narrow it down"), [])]
    asking["offered"] = [row["code"] for row in options]
    asking["offered_labels"] = [_option_label(row) for row in options]
    lines = [_("Found {0}. Tap the number to choose:").format(len(options))]
    lines += [f"{index}. {label}" for index, label in enumerate(asking["offered_labels"], 1)]
    return [_message("\n".join(lines), [_button(str(index), v="o", i=asking["i"], k=index - 1)
                                         for index in range(1, len(options) + 1)])]


__all__ = ["ACTION", "ASKING", "CANCELLED", "DONE", "PENDING", "STALE", "active", "answer", "finish", "first_question", "is_step",
           "rows_of", "search", "start"]
