"""Cowork 文件夹定时任务的引导式（CC 对话里点「定时任务」，总表 CW-D-FT；评估 docs/work/2026-11-16-cc-guided-folder-task-评估.md）。

``GUIDES`` 是纯字面量（闸门按源码读）。第一题「做什么」，各分支用 ``when`` 分开：

- 新建 / 修改：账套（本对话账套，没定才问；修改用任务原来的）→ 电脑 → 根目录 → 文件夹（可不选 = 根目录本身）→ 去向（方向与入账
  类型，MR.ERP 是现赊）→ 默认处理（各项可不选 = 遇到时停下问一次，同网页）→ 每月运行日 → 运行时间 → 提醒（开时问提醒时刻）
  → 名称 → 复核 → 确定。修改时各步推荐任务现值（点了才算）。
- 启用 / 停用 / 提醒：任务 →（提醒：开关与提醒时刻）→ 确定。
- 立即运行：任务（启用中的）→ 允许卡（会识别新文件、扣费）；删除：任务（多选）→ 允许卡。

确定时只转调 CC 工具同一组函数（`assistant.tools.folder_task_refusal / save_folder_task / set_folder_task_enabled /
run_folder_task / delete_folder_tasks`，它们再调任务服务 `folder_intake.rules`）：任务只能指向本对话公司的账套、别家公司的任务
不动，这些判据都在那里一份。选项来自任务表单同一个 `rules.options`；任务清单是 CC 工具 `folder_tasks` 同一份（只列本对话公司的）。
"""
from __future__ import annotations

import contextlib
import hashlib
import json

import frappe
from frappe import _

NEW, UPDATE, ENABLE, DISABLE, REMIND, RUN, DELETE = "new", "update", "enable", "disable", "remind", "run", "delete"
ACTIONS = ((NEW, "cowork.folder.new"), (UPDATE, "Edit"), (ENABLE, "cowork.folder.enable"),
           (DISABLE, "cowork.folder.disable"), (REMIND, "cowork.folder.field.remind"), (RUN, "cowork.folder.runNow"),
           (DELETE, "Delete"))
FORM = (NEW, UPDATE)
EXPRESS, MRERP = "express", "mrerp"
# 去向 = 方向 + 入账类型（Express）/ 现赊（MR.ERP），合成一个码，后面的默认处理按码出题（`when`）
DIRECTIONS = (("purchase", "intake.dir.purchase"), ("sales", "intake.dir.sales"))
POSTINGS = {EXPRESS: (("service", "Service"), ("stock", "Stock")), MRERP: (("cash", "Cash"), ("credit", "Credit"))}
ON, OFF = "on", "off"
SWITCH = ((ON, "cowork.folder.remind.on"), (OFF, "cowork.folder.remind.off"))
VAT = (("claim", "cowork.folder.vat.claim"), ("expense", "cowork.folder.vat.expense"))
PAYMENT = (("cash", "Cash"), ("credit", "Credit"))
ITEM = (("__new__", "cowork.folder.item.new"), ("ask", "cowork.folder.item.ask"))
DEFAULT_KINDS = ("payment", "vat", "account", "department", "item")
REMIND_SLOTS, REMIND_DAYS = 3, 7                           # 同任务服务 `contracts.remind_times` 的上限
RUN_STATUSES = {ENABLE: (0,), DISABLE: (1,), RUN: (1,)}   # 启用只列停用的；停用、立即运行只列启用中的
DRAFT_SEP = "@"
NEW_TASK = "__new__"   # 新建时「任务」一步的值


@contextlib.contextmanager
def _conversation(ctx):
    """调 CC 工具 / 对话账套时带上这段对话（本对话公司、账套、提醒发回的对话都按它认），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def allowed(user) -> bool:
    """菜单上出不出「定时任务」：有录入功能的人（同任务服务 `rules._require` 的 `scope.require("intake")`）。"""
    from pearnly_cowork.access import scope

    if not user or user == "Guest":
        return False
    try:
        scope.require("intake")
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


def _page(rows, txt="", start=0, page_length=20):
    text = str(txt or "").strip().casefold()
    if text:
        rows = [row for row in rows if text in f"{row['code']} {row['name']} {row.get('note') or ''}".casefold()]
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def _fixed(pairs, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in pairs]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def _tool(name, payload, ctx):
    """调 CC 工具同一个函数（公司范围、账套范围的闸都在那里），回它的 JSON（没有回包的回 None）。"""
    from pearnly_cowork.assistant import tools

    with _conversation(ctx):
        found = getattr(tools, name)(payload)
    return json.loads(found) if found else None


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed(ACTIONS, codes)


# ── 任务 ─────────────────────────────────────────────────────────────────────


def _tasks(ctx) -> list:
    """本对话公司的定时任务（CC 工具 `folder_tasks` 同一份：公司范围在那里判）。"""
    return (_tool("folder_tasks", {}, ctx) or {}).get("tasks") or []


def _task_row(ctx, name) -> dict:
    """任务现值（任务服务 `rules.listing` 的那一行，读者按编号「电脑|连接」）；不在本对话公司的任务里回空。"""
    from pearnly_cowork.folder_intake import rules

    if not name or name not in {task.get("name") for task in _tasks(ctx)}:
        return {}
    row = next((row for row in rules.listing().get("rules") or () if row.get("name") == name), {})
    return {**row, "reader": row.get("reader_id") or row.get("reader")} if row else {}


def _task_option(task) -> dict:
    state = _("cowork.folder.on") if task.get("enabled") else _("cowork.folder.off")
    note = " · ".join(part for part in (task.get("account_label"), state) if part)
    return {"code": task["name"], "name": task.get("title") or task["name"], "note": note}


def task_options(ctx, txt="", start=0, page_length=20, codes=None):
    """要动的任务：新建只有「新建」这一项（自动定下）；启用只列停用的；停用、立即运行只列启用中的；其余列本对话公司的全部。"""
    if _answer(ctx, "action") == NEW:
        return _fixed(((NEW_TASK, "cowork.folder.new"),), codes)
    statuses = RUN_STATUSES.get(_answer(ctx, "action"))
    rows = [_task_option(task) for task in _tasks(ctx)
            if task.get("name") and (statuses is None or int(task.get("enabled") or 0) in statuses)]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def task_default(ctx):
    """新建：任务一步定成「新建」（后面各步都挂在它后面，同修改走一条路）；其余动作要人选，不给默认。"""
    if _answer(ctx, "action") != NEW:
        return None
    return {"value": NEW_TASK, "value_label": _("cowork.folder.new"), "source": "default"}


def _current(ctx) -> dict:
    """修改时这条任务的现值；新建为空。"""
    return _task_row(ctx, str(_answer(ctx, "task"))) if _answer(ctx, "action") == UPDATE else {}


def _suggest(value):
    return None if value in (None, "", [], {}) else {"value": value, "source": "current"}


# ── 账套（新建：本对话账套；修改：任务原来的，连接换不了） ──────────────────────────────────────


def _books(ctx) -> dict:
    from pearnly_core.assistant import ledger

    with _conversation(ctx):
        view = ledger.view(str(ctx.get("session") or ""))
    return {"current": view.get("current") or "",
            "options": [{"code": item["id"], "name": item["label"]} for item in view.get("options") or ()]}


def _task_book(ctx) -> dict | None:
    """修改：任务原来的账套（连接换不了，同任务表单）。"""
    from pearnly_cowork.companies import provider

    current = _current(ctx)
    if not current.get("connection"):
        return None
    return {"code": provider.ledger_id(current["connection"], current.get("account_set_key")),
            "name": current.get("account_label") or current.get("account_set_key") or ""}


def book_default(ctx):
    """新建：本对话账套定了就不问；修改：任务原来的账套。"""
    if _answer(ctx, "action") == UPDATE:
        found = _task_book(ctx)
        return {"value": found["code"], "value_label": found["name"], "source": "current"} if found else None
    found = _books(ctx)
    current = next((row for row in found["options"] if row["code"] == found["current"]), None)
    return {"value": current["code"], "value_label": current["name"], "source": "conversation"} if current else None


def book_options(ctx, txt="", start=0, page_length=20, codes=None):
    if _answer(ctx, "action") == UPDATE:
        found = _task_book(ctx)
        rows = [found] if found else []
    else:
        rows = _books(ctx)["options"]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def book_apply(ctx, value) -> None:
    """新建时选了账套：记进本对话（同右栏账套选择器）；修改不动本对话账套。"""
    from pearnly_core.assistant import ledger

    if _answer(ctx, "action") != NEW:
        return

    chosen = ledger.choose(str(ctx.get("session") or ""), value, by="page")
    if chosen.get("status") != "set":
        frappe.throw(_("Invalid value"))


def _book(ctx) -> str:
    """这次的账套：答了的，没答就是后台定下的（本对话账套；引擎给选项函数的 ``answers`` 只有用户答的）。"""
    found = _answer(ctx, "book") or (book_default(ctx) or {}).get("value")
    return str(found or "")


def _place(ctx) -> tuple:
    """任务写到哪一处 (连接, 账套键)：修改按任务原来的；新建按本对话账套（已不是卡上选的那本回空）。"""
    from pearnly_cowork.assistant import write_scope

    current = _current(ctx)
    if current:
        return str(current.get("connection") or ""), str(current.get("account_set_key") or "")
    book = _book(ctx)
    with _conversation(ctx):
        pin = write_scope.pinned()
        if not book or not pin or not pin.get("key") or pin.get("id") != book:
            return "", ""
        return tuple(write_scope.place(pin))


def _adapter(ctx) -> str:
    from pearnly_cowork.connections import service as connections

    connection = _place(ctx)[0]
    return connections.adapter_of(connection) if connection else ""


def _form(ctx) -> dict:
    """任务表单同一份选项（`rules.options`）；连接不能用、账套不在目录时回空（确定时由服务报原因）。"""
    from pearnly_cowork.folder_intake import rules

    connection, key = _place(ctx)
    if not connection:
        return {}
    try:
        found = rules.options(connection, key or None)
        frappe.db.commit()   # 科目表还没读到时请电脑读（取选项是 GET，建的请求要自己提交）
        return found
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.db.rollback()
        frappe.clear_messages()
        return {}


# ── 电脑、根目录、文件夹 ─────────────────────────────────────────────────────────────


def _reader_id(reader) -> str:
    return str(reader.get("reader") or reader.get("connection") or "")


def _readers(ctx) -> list:
    return [reader for reader in _form(ctx).get("readers") or () if _reader_id(reader)]


def reader_options(ctx, txt="", start=0, page_length=20, codes=None):
    """能读文件夹的电脑（同任务表单；不在线的照列，注明）。"""
    rows = [{"code": _reader_id(reader), "name": reader.get("label") or _reader_id(reader),
             **({} if reader.get("fresh") else {"note": _("cowork.folder.agentStale")})} for reader in _readers(ctx)]
    if codes is not None:
        return _only(rows, codes)
    found = _page(rows, txt, start, page_length)
    if not rows:
        found["facts"] = [_("cowork.folder.noRoots")]
    return found


def reader_default(ctx):
    return _suggest(_current(ctx).get("reader"))


def _roots(ctx) -> list:
    wanted = str(_answer(ctx, "reader"))
    reader = next((reader for reader in _readers(ctx) if _reader_id(reader) == wanted), {})
    return [root for root in reader.get("roots") or () if isinstance(root, dict) and root.get("id")]


def root_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": str(root["id"]), "name": root.get("label") or str(root["id"])} for root in _roots(ctx)]
    if codes is not None:
        return _only(rows, codes)
    found = _page(rows, txt, start, page_length)
    if not rows:
        found["facts"] = [_("cowork.folder.noRoots")]
    return found


def root_default(ctx):
    return _suggest(_current(ctx).get("root_id"))


def folder_options(ctx, txt="", start=0, page_length=20, codes=None):
    """根目录下的文件夹（电脑报上来的那份，有上限，内存里搜索分页）；不选 = 根目录本身。"""
    wanted = str(_answer(ctx, "root"))
    root = next((root for root in _roots(ctx) if str(root["id"]) == wanted), {})
    rows = [{"code": str(folder), "name": str(folder)} for folder in root.get("folders") or () if str(folder or "")]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def folder_default(ctx):
    return _suggest(_current(ctx).get("folder"))


# ── 去向与默认处理 ──────────────────────────────────────────────────────────────


def target_options(ctx, txt="", start=0, page_length=20, codes=None):
    """去向：Express 是「方向 · 服务 / 存货」，MR.ERP 是「方向 · 现购 / 赊购」（同任务表单）。"""
    postings = POSTINGS.get(_adapter(ctx), ())
    rows = [{"code": f"{direction}:{posting}", "name": f"{_(label)} · {_(name)}"}
            for direction, label in DIRECTIONS for posting, name in postings]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def target_default(ctx):
    current = _current(ctx)
    second = current.get("posting_kind") or current.get("payment")
    return _suggest(f"{current['direction']}:{second}" if current.get("direction") and second else None)


def _target(ctx) -> tuple:
    direction, _sep, second = str(_answer(ctx, "target")).partition(":")
    return direction, second


def _defaults(ctx) -> dict:
    return _current(ctx).get("defaults") or {}


def payment_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed(PAYMENT, codes)


def vat_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed(VAT, codes)


def item_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed(ITEM, codes)


def _masters(ctx, kind):
    """科目 / 部门（任务表单同一份；销项的科目只列收入类，同任务服务）；科目表还在读回 None。"""
    rows = _form(ctx).get(kind)
    if rows is None:
        return None
    if kind == "accounts" and _target(ctx)[0] == "sales":
        rows = [row for row in rows if row.get("income")]
    return [{"code": str(row["code"]), "name": " · ".join(part for part in (str(row["code"]), row.get("name")) if part)}
            for row in rows if row.get("code")]


def _master_options(ctx, kind, txt, start, page_length, codes):
    rows = _masters(ctx, kind)
    if rows is None:
        return {"options": [], "more": False, "wait": True, "facts": [_("Loading...")]}
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def account_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _master_options(ctx, "accounts", txt, start, page_length, codes)


def department_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _master_options(ctx, "departments", txt, start, page_length, codes)


def payment_default(ctx):
    return _suggest(_defaults(ctx).get("payment"))


def vat_default(ctx):
    return _suggest(_defaults(ctx).get("vat"))


def account_default(ctx):
    return _suggest(_defaults(ctx).get("account"))


def department_default(ctx):
    return _suggest(_defaults(ctx).get("department"))


def item_default(ctx):
    return _suggest(_defaults(ctx).get("item"))


# ── 日程、提醒、名称 ──────────────────────────────────────────────────────────────


def day_options(ctx, txt="", start=0, page_length=40, codes=None):
    """每月运行日：1–31 日、月末（0，同任务页）。"""
    rows = [{"code": str(day), "name": _("cowork.folder.day").format(day)} for day in range(1, 32)]
    rows.append({"code": "0", "name": _("cowork.folder.lastDay")})
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, max(int(page_length or 0), len(rows)))


def day_default(ctx):
    value = _current(ctx).get("run_day")
    return _suggest(None if value in (None, "") else str(value))


def time_default(ctx):
    value = str(_current(ctx).get("run_time") or "")
    return _suggest(value[:5] if value else None)


def schedule_check(ctx, value) -> str:
    from pearnly_cowork.folder_intake import contracts

    try:
        contracts.schedule(_answer(ctx, "run_day") or "1", value)
    except contracts.ContractError as error:
        return error.code
    return ""


def _remind_task(ctx) -> dict:
    return _task_row(ctx, str(_answer(ctx, "task"))) if _answer(ctx, "action") in (UPDATE, REMIND) else {}


def remind_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _fixed(SWITCH, codes)


def remind_default(ctx):
    current = _remind_task(ctx)
    return _suggest((ON if int(current.get("remind") or 0) else OFF) if current else None)


def _day_label(days) -> str:
    if days == 0:
        return _("cowork.folder.remind.sameDay")
    return _("cowork.folder.remind.dayBefore") if days == 1 else _("cowork.folder.remind.daysBefore").format(days)


def times_options(ctx, txt="", start=0, page_length=20, codes=None):
    """提醒时刻：最多 3 组「提醒日 + 提醒时间」，第一组要答；推荐现值（没选过的是默认的前一天 08:00、14:00）。"""
    from pearnly_cowork.folder_intake import contracts

    current = _remind_task(ctx)
    try:
        found = contracts.remind_times(current.get("remind_times") if current else None)
    except contracts.ContractError:
        found = contracts.remind_times(None)
    days = [{"code": str(day), "name": _day_label(day)} for day in range(REMIND_DAYS + 1)]
    out = []
    for number in range(1, REMIND_SLOTS + 1):
        item = found[number - 1] if number <= len(found) else {}
        day = {"key": f"day{number}", "input": "choice", "title": _("cowork.folder.field.remindDay"), "options": days,
               "required": number == 1}
        at = {"key": f"time{number}", "input": "time", "title": _("cowork.folder.field.remindTime"), "required": number == 1}
        if item:
            day["preselected"], at["preselected"] = str(item["days_before"]), item["time"]
        out += [day, at]
    return {"questions": out}


def _remind_times(given) -> list:
    """子题答案 → 任务服务的提醒时刻（只收两项都答了的那几组）。"""
    out = []
    for number in range(1, REMIND_SLOTS + 1):
        day, at = (given or {}).get(f"day{number}"), (given or {}).get(f"time{number}")
        if day not in (None, "") and at:
            out.append({"days_before": int(day), "time": str(at)})
    return out


def times_check(ctx, value) -> str:
    """提醒时刻按任务服务的口径再核一遍（当天的要早于运行时间）。"""
    from pearnly_cowork.folder_intake import contracts

    run_time = _answer(ctx, "run_time") or str(_remind_task(ctx).get("run_time") or "")[:5]
    try:
        contracts.remind_times(_remind_times(value), run_time or None)
    except contracts.ContractError as error:
        return error.code
    return ""


def title_default(ctx):
    return _suggest(_current(ctx).get("title"))


# ── 草稿、复核、确定 ────────────────────────────────────────────────────────────


def draft(ctx, values) -> dict:
    """答完了：不建任何东西，只记下「动作@任务@答案摘要」给复核与确定用。"""
    target = values.get("task") or ",".join(values.get("tasks") or ())
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((values["action"], str(target), digest))}


def _line(key, label_key, value, step=None):
    out = {"key": key, "label_key": label_key, "value": "" if value is None else value}
    if step:
        out["step"] = step
    return out


def _name_of(ctx, options, value) -> str:
    """复核卡上一项的显示名：照这一步的选项（码按原样核）。"""
    if value in (None, ""):
        return ""
    found = options(ctx, codes=[value])["options"]
    return found[0]["name"] if found else str(value)


def _payload(ctx, values) -> dict:
    """要存的一份任务表单（CC 工具 `save_folder_task` 的入参）：修改带任务名，工具以现值为底合并。"""
    connection, key = _place(ctx)
    direction, second = _target(ctx)
    express = _adapter(ctx) == EXPRESS
    out = {"title": values["title"], "connection": connection, "account_set_key": key, "direction": direction,
           "reader": values["reader"], "root_id": values["root"], "folder": values.get("folder") or "",
           "run_day": int(values["run_day"]), "run_time": values["run_time"], "remind": values.get("remind") == ON,
           ("posting_kind" if express else "payment"): second}
    if values.get("task") and values["task"] != NEW_TASK:
        out["name"] = values["task"]
    if express:
        out["defaults"] = {kind: values[kind] for kind in DEFAULT_KINDS if values.get(kind)}
    if values.get("remind") == ON:
        out["remind_times"] = _remind_times(values.get("times"))
    return out


DEFAULT_OPTIONS = (("payment", payment_options), ("vat", vat_options), ("account", account_options),
                   ("department", department_options), ("item", item_options))


def review(ctx, draft) -> dict:
    """复核卡：做什么 + 任务；新建 / 修改列整张表单；立即运行注明会识别（扣费）；删除列要删的任务。"""
    values = dict(ctx.get("answers") or {})
    action = values.get("action")
    key = "guide.step.folder_task."
    out = {"version": draft, "ready": True, "problem": "", "problem_text": "", "tables": [], "total": "",
           "lines": [_line("action", key + "action", _(dict(ACTIONS).get(action, "")), "action")]}
    if action in (UPDATE, ENABLE, DISABLE, REMIND, RUN):
        out["lines"].append(_line("task", key + "task", _name_of(ctx, task_options, values.get("task")), "task"))
    if action in FORM:
        out["lines"] += [
            _line("book", key + "book", _name_of(ctx, book_options, _book(ctx)), "book" if action == NEW else None),
            _line("reader", key + "reader", _name_of(ctx, reader_options, values.get("reader")), "reader"),
            _line("root", key + "root", _name_of(ctx, root_options, values.get("root")), "root"),
            _line("folder", key + "folder", values.get("folder") or _("cowork.folder.rootItself"), "folder"),
            _line("target", key + "target", _name_of(ctx, target_options, values.get("target")), "target")]
        out["lines"] += [_line(kind, key + kind, _name_of(ctx, options, values[kind]), kind)
                         for kind, options in DEFAULT_OPTIONS if values.get(kind)]
        out["lines"] += [_line("run_day", key + "run_day", _name_of(ctx, day_options, values.get("run_day")), "run_day"),
                         _line("run_time", key + "run_time", values.get("run_time"), "run_time")]
    if action in (NEW, UPDATE, REMIND):
        out["lines"].append(_line("remind", key + "remind", _(dict(SWITCH).get(values.get("remind"), "")), "remind"))
        if values.get("remind") == ON:
            shown = ", ".join(f"{_day_label(item['days_before'])} {item['time']}"
                              for item in _remind_times(values.get("times")))
            out["lines"].append(_line("times", key + "times", shown, "times"))
    if action in FORM:
        out["lines"].append(_line("title", key + "title", values.get("title"), "title"))
    if action == RUN:
        out["lines"].append(_line("charge", "cowork.folder.runNow", _("cowork.folder.runNow.charge")))
    if action == DELETE:
        names = list(values.get("tasks") or ())
        found = {row["code"]: row for row in task_options(ctx, codes=names)["options"]}
        out["tables"].append({"key": "tasks", "label_key": key + "tasks", "step": "tasks",
                              "rows": [{"code": name, "name": (found.get(name) or {}).get("name") or name, "amount": ""}
                                       for name in names]})
    return out


def _refused(step, found):
    return {"returned": {step: {"reason": "folder_task_refused",
                                "text": str(found.get("reason") or found.get("status") or "")[:300]}}}


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定 / 允许」：照 CC 工具同一个函数办（公司、账套范围由它判）；不收的退回那一步，原因照工具回的。"""
    action, task = values["action"], str(values.get("task") or "")
    if action in FORM:
        payload = _payload(ctx, values)
        if not payload["connection"]:
            return {"returned": {"book": {"reason": "not_this_conversation_book"}}}
        refused = _tool("folder_task_refusal", payload, ctx)  # 写入闸；MR.ERP 账套要先定导入菜单 / 默认值
        if refused:
            return _refused("book" if action == NEW else "task", refused)
        found = _tool("save_folder_task", payload, ctx) or {}
        if found.get("status") != "saved":
            return _refused("title" if action == NEW else "task", found)
        return {"status": "entered", "docnum": found.get("title") or found.get("task") or ""}
    if action in (ENABLE, DISABLE, REMIND):
        payload = {"task": task}
        if action == REMIND:
            payload["remind"] = values.get("remind") == ON
            if payload["remind"]:
                payload["remind_times"] = _remind_times(values.get("times"))
        else:
            payload["enabled"] = action == ENABLE
        found = _tool("set_folder_task_enabled", payload, ctx) or {}
        if found.get("status") != "saved":
            return _refused("task", found)
        return {"status": "entered", "docnum": _name_of(ctx, task_options, task)}
    if action == RUN:
        found = _tool("run_folder_task", {"task": task}, ctx) or {}
        if not found.get("requested"):
            return _refused("task", found)
        return {"status": "entered", "docnum": found.get("title") or task, "reason_text": _("cowork.folder.runRequested")}
    found = _tool("delete_folder_tasks", {"tasks": list(values.get("tasks") or ())}, ctx) or {}
    if "deleted" not in found:
        return _refused("tasks", found)
    return {"status": "entered", "deleted": found["deleted"]}


# ── 声明 ─────────────────────────────────────────────────────────────────────

GUIDES = [
    {"id": "folder_task", "label_key": "guide.business.folder_task", "icon": "folder-clock", "order": 70,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_cowork.assistant.guided.folder_task.allowed",
     "opening_key": "guide.open.folder_task", "source": "form", "writes": True,
     "consent": {"key": "action", "in": ["run", "delete"]},
     "steps": [
         {"key": "action", "input": "choice", "rule": "always",
          "options": "pearnly_cowork.assistant.guided.folder_task.action_options"},
         {"key": "task", "input": "choice", "rule": "auto", "search": True, "after": ["action"],
          "when": {"key": "action", "in": ["new", "update", "enable", "disable", "remind", "run"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.task_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.task_default"},
         {"key": "tasks", "input": "multi", "rule": "always", "search": True, "after": ["action"],
          "when": {"key": "action", "value": "delete"}, "options": "pearnly_cowork.assistant.guided.folder_task.task_options"},
         {"key": "book", "input": "choice", "rule": "auto", "search": True, "after": ["task"],
          "when": {"key": "action", "in": ["new", "update"]}, "options": "pearnly_cowork.assistant.guided.folder_task.book_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.book_default",
          "apply": "pearnly_cowork.assistant.guided.folder_task.book_apply"},
         {"key": "reader", "input": "choice", "rule": "always", "after": ["book"],
          "when": {"key": "action", "in": ["new", "update"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.reader_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.reader_default"},
         {"key": "root", "input": "choice", "rule": "always", "after": ["reader"],
          "when": {"key": "action", "in": ["new", "update"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.root_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.root_default"},
         {"key": "folder", "input": "choice", "rule": "always", "search": True, "required": False, "after": ["root"],
          "when": {"key": "action", "in": ["new", "update"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.folder_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.folder_default"},
         {"key": "target", "input": "choice", "rule": "always", "after": ["book"],
          "when": {"key": "action", "in": ["new", "update"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.target_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.target_default"},
         {"key": "payment", "input": "choice", "rule": "always", "required": False, "after": ["target"],
          "when": {"key": "target", "in": ["purchase:service", "purchase:stock"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.payment_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.payment_default"},
         {"key": "vat", "input": "choice", "rule": "always", "required": False, "after": ["target"],
          "when": {"key": "target", "in": ["purchase:service", "purchase:stock"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.vat_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.vat_default"},
         {"key": "account", "input": "choice", "rule": "always", "search": True, "required": False, "after": ["target"],
          "when": {"key": "target", "in": ["purchase:service", "sales:service", "sales:stock"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.account_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.account_default"},
         {"key": "department", "input": "choice", "rule": "always", "search": True, "required": False, "after": ["target"],
          "when": {"key": "target", "in": ["purchase:service", "purchase:stock", "sales:service", "sales:stock"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.department_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.department_default"},
         {"key": "item", "input": "choice", "rule": "always", "required": False, "after": ["target"],
          "when": {"key": "target", "in": ["purchase:stock", "sales:stock"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.item_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.item_default"},
         {"key": "run_day", "input": "choice", "rule": "always", "after": ["book"],
          "when": {"key": "action", "in": ["new", "update"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.day_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.day_default"},
         {"key": "run_time", "input": "time", "rule": "always", "after": ["run_day"],
          "when": {"key": "action", "in": ["new", "update"]},
          "default": "pearnly_cowork.assistant.guided.folder_task.time_default",
          "check": "pearnly_cowork.assistant.guided.folder_task.schedule_check"},
         {"key": "remind", "input": "choice", "rule": "always", "after": ["task"],
          "when": {"key": "action", "in": ["new", "update", "remind"]},
          "options": "pearnly_cowork.assistant.guided.folder_task.remind_options",
          "default": "pearnly_cowork.assistant.guided.folder_task.remind_default"},
         {"key": "times", "input": "questions", "rule": "always", "after": ["remind"],
          "when": {"key": "remind", "value": "on"},
          "options": "pearnly_cowork.assistant.guided.folder_task.times_options",
          "check": "pearnly_cowork.assistant.guided.folder_task.times_check"},
         {"key": "title", "input": "text", "rule": "always", "after": ["book"],
          "when": {"key": "action", "in": ["new", "update"]},
          "default": "pearnly_cowork.assistant.guided.folder_task.title_default"},
     ],
     "draft": "pearnly_cowork.assistant.guided.folder_task.draft",
     "review": "pearnly_cowork.assistant.guided.folder_task.review",
     "confirm": "pearnly_cowork.assistant.guided.folder_task.confirm",
     "tool": "cowork_save_folder_task", "expires": ["ledger", "company"]},
]
