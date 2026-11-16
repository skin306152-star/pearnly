"""Cowork 任务看板的引导式（CC 对话里点「任务看板」，总表 CW-D-WK；评估 docs/work/2026-11-16-cc-guided-settings-评估.md）。

``GUIDES`` 是纯字面量（闸门按源码读）。做什么（建卡 / 改卡 / 移动 / 完成或取消完成 / 评论）→ 看板（默认看板预先定下，可改）→ 列表（建卡、
移动时）→ 卡片（服务端搜索）→ 各字段（改卡：标题、说明、开始、到期日与时间、提醒、重复，都可选，推荐现值）/ 评论正文 → 复核 → 确定。
只转调看板页同一组接口（`work.api`：``boards_list / board_lists / board / card / create / update / move / toggle_done / comment``），
CC 工具 ``cowork_work_*`` 也是这一组。看板不按公司隔开（用户 11-16）。改卡带上复核时卡片的 ``modified``：别处改过就不覆盖，退回卡片那一步。

负责人、标签、清单（勾完成、加一项、加一个新清单）、附件（交对话里的文件）同看板页卡片详情：负责人 / 标签 / 清单走同一个 ``update``
（带 modified），附件走 `work.service.attach`（看板页上传同一个，存成挂在卡片上的原生私有 File）。
"""
from __future__ import annotations

import contextlib
import hashlib
import json

import frappe
from frappe import _

CREATE, UPDATE, MOVE, DONE, COMMENT = "create", "update", "move", "done", "comment"
MEMBERS, LABELS_ACTION, CHECKLIST, ATTACH = "members", "labels", "checklist", "attach"
ACTIONS = ((CREATE, "cowork.work.card.add"), (UPDATE, "guide.option.work.update"), (MOVE, "guide.option.work.move"),
           (DONE, "guide.option.work.done"), (COMMENT, "cowork.work.comment.label"), (MEMBERS, "cowork.work.field.members"),
           (LABELS_ACTION, "cowork.work.field.labels"), (CHECKLIST, "cowork.work.field.checklist"),
           (ATTACH, "cowork.work.field.attachments"))
EDITS = (UPDATE, MEMBERS, LABELS_ACTION, CHECKLIST)   # 走 ``update``、要带 modified 的
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


# ── 负责人、标签、清单 ────────────────────────────────────────────────────────────


def _board_data(ctx) -> dict:
    return _api().board(str(_answer(ctx, "board")) or None)


def member_options(ctx, txt="", start=0, page_length=20, codes=None):
    """能被分派的人（同看板页卡片「成员」的候选：这块板的成员，`work.service.members`）；能不能分派由服务再核。"""
    rows = [{"code": row["user"], "name": row.get("name") or row["user"]} for row in _board_data(ctx).get("members") or ()]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def assignees_default(ctx):
    found = _card(ctx).get("assignees") if _answer(ctx, "action") == MEMBERS and _answer(ctx, "card") else None
    return {"value": list(found), "source": "current"} if found else None


def label_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": row["name"], "name": row.get("title") or _(f"cowork.work.color.{row.get('color')}")}
            for row in _board_data(ctx).get("labels") or ()]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def labels_default(ctx):
    found = _card(ctx).get("labels") if _answer(ctx, "action") == LABELS_ACTION and _answer(ctx, "card") else None
    return {"value": list(found), "source": "current"} if found else None


def _items(ctx):
    """卡片现有的清单项：码 = ``清单序号:项序号``（同看板页的顺序）。"""
    out = []
    for g, group in enumerate(_card(ctx).get("checklists") or ()):
        for i, item in enumerate(group.get("items") or ()):
            out.append({"code": f"{g}:{i}", "name": item.get("title") or "", "note": group.get("title") or "",
                        "done": bool(item.get("done"))})
    return out


def item_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{key: row[key] for key in ("code", "name", "note") if row.get(key) not in (None, "")} for row in _items(ctx)]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def checked_default(ctx):
    """已勾完成的项推荐现状（点了才算）。"""
    found = [row["code"] for row in _items(ctx) if row["done"]] if _answer(ctx, "action") == CHECKLIST else []
    return {"value": found, "source": "current"} if found else None


def _checklists(card, values):
    """改后的清单：照现有的清单与项，勾选的标完成、没勾的标未完成（答了「完成的项」才动勾选），再把新的一项加到新清单或第一个清单。"""
    groups = [{"title": group.get("title") or "", "items": [dict(item) for item in group.get("items") or ()]}
              for group in card.get("checklists") or ()]
    if "checked" in values:
        done = set(values["checked"])
        for g, group in enumerate(groups):
            for i, item in enumerate(group["items"]):
                item["done"] = 1 if f"{g}:{i}" in done else 0
    item = str(values.get("new_item") or "").strip()
    if values.get("new_group") or (item and not groups):
        groups.append({"title": str(values.get("new_group") or "").strip(), "items": []})
        target = groups[-1]
    else:
        target = groups[0] if groups else None
    if item and target is not None:
        target["items"].append({"title": item, "done": 0})
    return groups


class _Upload:
    """对话里的文件当成看板页上传的那一个（`work.service.attach` 只读 ``filename`` 与 ``stream``）。"""

    def __init__(self, filename, content):
        import io

        self.filename, self.stream = filename, io.BytesIO(content)


def files_check(ctx, value) -> str:
    """只收本对话里的文件（`assistant.files.attachment`）。"""
    from pearnly_core.assistant import files as chat_files

    with _conversation(ctx):
        for file_id in value or ():
            try:
                chat_files.attachment(str(file_id))  # loop-query: 一次交的几份（引擎上限 20）
            except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError):
                frappe.clear_messages()
                return "cowork.work.error.file"
    return ""


@contextlib.contextmanager
def _conversation(ctx):
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


# ── 草稿、复核、确定 ────────────────────────────────────────────────────────────


def draft(ctx, values) -> dict:
    """改卡：把卡片此刻的 ``modified`` 一起记进草稿号（确定时交给看板服务，别处改过就不覆盖）；建卡没有标题时退回标题。"""
    action = values["action"]
    if action == UPDATE and not any(key in values for key in FIELDS):
        return {"returned": {"rename": {"reason": "guide.fact.work.nothing"}}}
    if action == CHECKLIST and not any(key in values for key in ("checked", "new_item", "new_group")):
        return {"returned": {"checked": {"reason": "guide.fact.work.nothing"}}}
    modified = _card(ctx).get("modified") if action in EDITS else ""
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((action, str(values.get("card") or ""), digest, str(modified or "")))}


def _line(key, label_key, value, step=None):
    return {"key": key, "label_key": label_key, "value": value, **({"step": step} if step else {})}


LABELS = {"title": "cowork.work.field.title", "rename": "cowork.work.field.title", "description": "cowork.work.field.description", "start_date": "cowork.work.field.start",
          "due_date": "cowork.work.field.due", "due_time": "cowork.work.field.dueTime", "reminder": "cowork.work.field.reminder",
          "recurrence": "cowork.work.field.repeat", "content": "cowork.work.comment.label",
          "new_item": "cowork.work.field.item", "new_group": "cowork.work.field.checklist"}


def review(ctx, draft) -> dict:
    action = _answer(ctx, "action")
    lines = [_line("action", "guide.step.work.action", _(dict(ACTIONS).get(action, "")), "action")]
    if action != CREATE:
        found = _card(ctx)
        lines.append(_line("card", "guide.step.work.card", found.get("title") or _answer(ctx, "card"), "card"))
    if action in (CREATE, MOVE):
        name = next((row["name"] for row in list_options(ctx)["options"] if row["code"] == _answer(ctx, "list")), "")
        lines.append(_line("list", "cowork.work.field.list", name, "list"))
    keys = FIELDS if action == UPDATE else ("title",) if action == CREATE else ("content",) if action == COMMENT \
        else ("new_group", "new_item") if action == CHECKLIST else ()
    for key in keys:
        if _answer(ctx, key):
            lines.append(_line(key, LABELS[key], _answer(ctx, key), key))
    tables = []
    picked = {MEMBERS: ("assignees", "cowork.work.field.members", member_options),
              LABELS_ACTION: ("label_ids", "cowork.work.field.labels", label_options),
              CHECKLIST: ("checked", "cowork.work.field.done", item_options)}.get(action)
    if picked and (action != CHECKLIST or "checked" in (ctx.get("answers") or {})):
        key, label, options = picked
        chosen = list(_answer(ctx, key, []))
        names = {row["code"]: row["name"] for row in options(ctx, codes=chosen)["options"]} if chosen else {}
        tables.append({"key": key, "label_key": label, "step": key,
                       "rows": [{"code": code, "name": names.get(code, code), "amount": ""} for code in chosen]})
    if action == ATTACH:
        from pearnly_core.assistant import files as chat_files

        with _conversation(ctx):
            rows = []
            for file_id in _answer(ctx, "files", []):
                rows.append({"code": file_id, "name": chat_files.attachment(str(file_id))[0], "amount": ""})  # loop-query: 交的几份
        tables.append({"key": "files", "label_key": "cowork.work.field.attachments", "step": "files", "rows": rows})
    return {"version": draft, "ready": True, "problem": "", "problem_text": "", "lines": lines, "tables": tables, "total": ""}


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
        elif action in (MEMBERS, LABELS_ACTION, CHECKLIST):
            modified = draft.rsplit(DRAFT_SEP, 1)[-1]
            changes = {"assignees": values.get("assignees") or []} if action == MEMBERS else \
                {"labels": values.get("label_ids") or []} if action == LABELS_ACTION else \
                {"checklists": _checklists(api.card(card), values)}
            api.update(card, modified, json.dumps(changes, ensure_ascii=False))
        elif action == ATTACH:
            from pearnly_core.assistant import files as chat_files
            from pearnly_cowork.work import service

            with _conversation(ctx):
                for file_id in values["files"]:
                    filename, content = chat_files.attachment(str(file_id))  # loop-query: 交的几份，一份一个原生 File
                    service.attach(card, _Upload(filename, content))
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
          "when": {"key": "action", "in": ["update", "move", "done", "comment", "members", "labels", "checklist", "attach"]},
          "options": "pearnly_cowork.assistant.guided.work.card_options"},
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
         {"key": "assignees", "input": "multi", "rule": "always", "search": True, "after": ["card"],
          "when": {"key": "action", "value": "members"}, "options": "pearnly_cowork.assistant.guided.work.member_options",
          "default": "pearnly_cowork.assistant.guided.work.assignees_default"},
         {"key": "label_ids", "input": "multi", "rule": "always", "after": ["card"], "when": {"key": "action", "value": "labels"},
          "options": "pearnly_cowork.assistant.guided.work.label_options", "default": "pearnly_cowork.assistant.guided.work.labels_default"},
         {"key": "checked", "input": "multi", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "checklist"}, "options": "pearnly_cowork.assistant.guided.work.item_options",
          "default": "pearnly_cowork.assistant.guided.work.checked_default"},
         {"key": "new_item", "input": "text", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "checklist"}},
         {"key": "new_group", "input": "text", "rule": "always", "required": False, "after": ["card"],
          "when": {"key": "action", "value": "checklist"}},
         {"key": "files", "input": "file", "rule": "always", "after": ["card"], "when": {"key": "action", "value": "attach"},
          "check": "pearnly_cowork.assistant.guided.work.files_check"},
         {"key": "content", "input": "text", "rule": "always", "after": ["action"], "when": {"key": "action", "value": "comment"}},
     ],
     "draft": "pearnly_cowork.assistant.guided.work.draft", "review": "pearnly_cowork.assistant.guided.work.review",
     "confirm": "pearnly_cowork.assistant.guided.work.confirm",
     "tool": "cowork_work_update_card"},
]
