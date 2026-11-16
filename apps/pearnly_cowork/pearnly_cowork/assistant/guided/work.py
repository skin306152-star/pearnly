"""Cowork 任务看板的引导式（CC 对话里点「任务看板」，总表 CW-D-WK；评估 docs/work/2026-11-16-cc-guided-settings-评估.md）。

``GUIDES`` 是纯字面量（闸门按源码读）。做什么（建卡 / 改卡 / 移动 / 完成或取消完成 / 评论）→ 看板（默认看板预先定下，可改）→ 列表（建卡、
移动时）→ 卡片（服务端搜索）→ 各字段（改卡：标题、说明、开始、到期日与时间、提醒、重复，都可选，推荐现值）/ 评论正文 → 复核 → 确定。
只转调看板页同一组接口（`work.api`：``boards_list / board_lists / board / card / create / update / move / toggle_done / comment``），
CC 工具 ``cowork_work_*`` 也是这一组。看板不按公司隔开（用户 11-16）。改卡带上复核时卡片的 ``modified``：别处改过就不覆盖，退回卡片那一步。

本轮先做这几样；清单、标签、成员、附件是这一格的「缺什么」，排在本轮主体之后（主会话 11-16）。
"""
from __future__ import annotations

import hashlib
import json

import frappe
from frappe import _

CREATE, UPDATE, MOVE, DONE, COMMENT = "create", "update", "move", "done", "comment"
ACTIONS = ((CREATE, "cowork.work.card.add"), (UPDATE, "guide.option.work.update"), (MOVE, "guide.option.work.move"),
           (DONE, "guide.option.work.done"), (COMMENT, "cowork.work.comment.label"))
FIELDS = ("rename", "description", "start_date", "due_date", "due_time", "reminder", "recurrence")   # rename = 改卡的标题
REMINDERS = ("none", "at_due", "5m", "10m", "15m", "1h", "2h", "1d", "2d")
REPEATS = ("never", "daily", "weekly", "monthly", "yearly")
DRAFT_SEP = "@"


def _api():
    from pearnly_cowork.work import api

    return api


def allowed(user) -> bool:
    """菜单上出不出「任务看板」：有任务看板功能的人（`work.access.allowed`，看板页同一口径）。"""
    from pearnly_cowork.work import access

    return bool(user) and user != "Guest" and bool(access.allowed(user))


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


def _page(rows, txt="", start=0, page_length=20):
    text = str(txt or "").strip().casefold()
    rows = [row for row in rows if text in f"{row['name']} {row.get('note') or ''}".casefold()] if text else rows
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def _fixed(labels, codes):
    rows = [{"code": code, "name": _(label)} for code, label in labels]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed(ACTIONS, codes)


def reminder_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed([(code, f"cowork.work.reminder.{code}") for code in REMINDERS], codes)


def recurrence_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed([(code, f"cowork.work.repeat.{code}") for code in REPEATS], codes)


# ── 看板、列表、卡片 ─────────────────────────────────────────────────────────────


def _boards():
    return [{"code": row["name"], "name": row.get("title") or row["name"], "default": bool(row.get("is_default"))}
            for row in _api().boards_list() or ()]


def board_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": row["code"], "name": row["name"]} for row in _boards()]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def board_default(ctx):
    """默认看板（同看板页打开时那一块；进「已定」可改）。"""
    found = next((row for row in _boards() if row["default"]), None)
    return {"value": found["code"], "value_label": found["name"], "source": "default"} if found else None


def list_options(ctx, txt="", start=0, page_length=20, codes=None):
    board = str(_answer(ctx, "board"))
    rows = [{"code": row["name"], "name": row.get("title") or row["name"]} for row in _api().board_lists(board) or ()]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def _cards(ctx):
    data = _api().board(str(_answer(ctx, "board")) or None)
    lists = {row.get("name"): row.get("title") for row in data.get("lists") or ()}
    return [{"code": card["name"], "name": card.get("title") or card["name"],
             **({"note": lists[card["board_list"]]} if lists.get(card.get("board_list")) else {})}
            for card in data.get("cards") or () if not card.get("is_template") and not card.get("archived")]


def card_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这块看板上的卡片（同看板页取的那一份，按标题 / 列表名在内存里搜索、分页）；``codes`` 给了按原样核。"""
    rows = _cards(ctx)
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def _card(ctx) -> dict:
    name = str(_answer(ctx, "card"))
    return _api().card(name) if name else {}


def _current(ctx, field):
    """改卡时各字段推荐现值（点了才算）。"""
    if _answer(ctx, "action") != UPDATE or not _answer(ctx, "card"):
        return None
    value = _card(ctx).get(field)
    if value in (None, ""):
        return None
    value = str(value)
    return {"value": value[:5] if field == "due_time" else value, "source": "current"}


def rename_default(ctx):
    return _current(ctx, "title")


def description_default(ctx):
    return _current(ctx, "description")


def start_default(ctx):
    return _current(ctx, "start_date")


def due_default(ctx):
    return _current(ctx, "due_date")


def time_default(ctx):
    return _current(ctx, "due_time")


def reminder_default(ctx):
    return _current(ctx, "reminder")


def recurrence_default(ctx):
    return _current(ctx, "recurrence")


# ── 草稿、复核、确定 ────────────────────────────────────────────────────────────


def draft(ctx, values) -> dict:
    """改卡：把卡片此刻的 ``modified`` 一起记进草稿号（确定时交给看板服务，别处改过就不覆盖）；建卡没有标题时退回标题。"""
    action = values["action"]
    if action == UPDATE and not any(key in values for key in FIELDS):
        return {"returned": {"rename": {"reason": "guide.fact.work.nothing"}}}
    modified = _card(ctx).get("modified") if action == UPDATE else ""
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((action, str(values.get("card") or ""), digest, str(modified or "")))}


def _line(key, label_key, value, step=None):
    return {"key": key, "label_key": label_key, "value": value, **({"step": step} if step else {})}


LABELS = {"title": "cowork.work.field.title", "rename": "cowork.work.field.title", "description": "cowork.work.field.description", "start_date": "cowork.work.field.start",
          "due_date": "cowork.work.field.due", "due_time": "cowork.work.field.dueTime", "reminder": "cowork.work.field.reminder",
          "recurrence": "cowork.work.field.repeat", "content": "cowork.work.comment.label"}


def review(ctx, draft) -> dict:
    action = _answer(ctx, "action")
    lines = [_line("action", "guide.step.work.action", _(dict(ACTIONS).get(action, "")), "action")]
    if action != CREATE:
        found = _card(ctx)
        lines.append(_line("card", "guide.step.work.card", found.get("title") or _answer(ctx, "card"), "card"))
    if action in (CREATE, MOVE):
        name = next((row["name"] for row in list_options(ctx)["options"] if row["code"] == _answer(ctx, "list")), "")
        lines.append(_line("list", "cowork.work.field.list", name, "list"))
    keys = FIELDS if action == UPDATE else ("title",) if action == CREATE else ("content",) if action == COMMENT else ()
    for key in keys:
        if _answer(ctx, key):
            lines.append(_line(key, LABELS[key], _answer(ctx, key), key))
    return {"version": draft, "ready": True, "problem": "", "problem_text": "", "lines": lines, "tables": [], "total": ""}


def confirm(ctx, values, draft, consent) -> dict:
    """转调看板页同一个接口；看板服务拒绝（没权限、别处改过、日期先后不对）退回对应那一步。"""
    api, action = _api(), values["action"]
    card = str(values.get("card") or "")
    try:
        if action == CREATE:
            made = api.create(values["title"], values["list"])
            return {"status": "entered", "docnum": (made or {}).get("name") or ""}
        if action == UPDATE:
            modified = draft.rsplit(DRAFT_SEP, 1)[-1]
            changes = {("title" if key == "rename" else key): values[key] for key in FIELDS if key in values}
            api.update(card, modified, json.dumps(changes, ensure_ascii=False))
        elif action == MOVE:
            api.move(card, values["list"])
        elif action == DONE:
            api.toggle_done(card)
        else:
            api.comment(card, values["content"])
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"returned": {"card" if action != CREATE else "list": {"reason": "work_refused", "text": str(error)[:300]}}}
    return {"status": "entered", "docnum": card}


GUIDES = [
    {"id": "work", "label_key": "guide.business.work", "icon": "kanban", "order": 90,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_cowork.assistant.guided.work.allowed",
     "opening_key": "guide.open.work", "source": "form", "writes": True,
     "steps": [
         {"key": "action", "input": "choice", "rule": "always", "options": "pearnly_cowork.assistant.guided.work.action_options"},
         {"key": "board", "input": "choice", "rule": "auto", "after": ["action"],
          "options": "pearnly_cowork.assistant.guided.work.board_options", "default": "pearnly_cowork.assistant.guided.work.board_default"},
         {"key": "list", "input": "choice", "rule": "always", "after": ["board"], "when": {"key": "action", "in": ["create", "move"]},
          "options": "pearnly_cowork.assistant.guided.work.list_options"},
         {"key": "card", "input": "choice", "rule": "always", "search": True, "after": ["board"],
          "when": {"key": "action", "in": ["update", "move", "done", "comment"]}, "options": "pearnly_cowork.assistant.guided.work.card_options"},
         {"key": "title", "input": "text", "rule": "always", "after": ["list"], "when": {"key": "action", "value": "create"}},
         {"key": "rename", "input": "text", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "update"}, "default": "pearnly_cowork.assistant.guided.work.rename_default"},
         {"key": "description", "input": "text", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "update"}, "default": "pearnly_cowork.assistant.guided.work.description_default"},
         {"key": "start_date", "input": "date", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "update"}, "default": "pearnly_cowork.assistant.guided.work.start_default"},
         {"key": "due_date", "input": "date", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "update"}, "default": "pearnly_cowork.assistant.guided.work.due_default"},
         {"key": "due_time", "input": "time", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "update"}, "default": "pearnly_cowork.assistant.guided.work.time_default"},
         {"key": "reminder", "input": "choice", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "update"}, "options": "pearnly_cowork.assistant.guided.work.reminder_options",
          "default": "pearnly_cowork.assistant.guided.work.reminder_default"},
         {"key": "recurrence", "input": "choice", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "update"}, "options": "pearnly_cowork.assistant.guided.work.recurrence_options",
          "default": "pearnly_cowork.assistant.guided.work.recurrence_default"},
         {"key": "content", "input": "text", "rule": "always", "after": ["action"], "when": {"key": "action", "value": "comment"}},
     ],
     "draft": "pearnly_cowork.assistant.guided.work.draft", "review": "pearnly_cowork.assistant.guided.work.review",
     "confirm": "pearnly_cowork.assistant.guided.work.confirm",
     "tool": "cowork_work_update_card"},
]
