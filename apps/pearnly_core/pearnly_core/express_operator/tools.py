"""代录 Express 的对话工具（多产品共用，对话框甲，主会话 10-29 审定）：实操任务页底部的对话里，智能体用这两个工具查进度、
交代跳过 / 重做。产品在自己的 ``hooks.py`` 的 ``pearnly_assistant_tools`` 里列上 ``pearnly_core.express_operator.tools.TOOLS``。

- 只认本人、本站产品里的任务（``tasks._own``），公司还要过产品的权限；别人的、别的公司的一律「找不到」。
- 不新增写账能力：跳过、重做只记在任务上，由执行的那台电脑的 CC 页面照桥上已有的控制去做（``tasks.push_control``）；
  要人选的项不经模型，人在任务页点选项卡片（``api.task_answer``）。
- 单据按任务里的顺序编号（第 1 张起），人说「第 2 张」时智能体照 ``express_operator_status`` 回的编号对上。
"""
import json

import frappe
from frappe import _

from pearnly_core.express_operator import tasks


def _numbered(view: dict) -> list:
    return [{"n": index, **row} for index, row in enumerate(view.get("documents") or [], start=1)]


def _worded(row: dict) -> dict:
    """没录成的原因给模型的是产品给的那句话（``products.reason_text``，按当前语言），码放 ``reason_code``（测试机1 11-12：模型把
    choice_needed:account_set 原样说给用户）；产品给不出话、或话就是原文的照原样。"""
    from pearnly_core.express_operator import products

    raw = str(row.get("reason") or "")
    text = products.reason_text(raw) if raw else ""
    return {**row, "reason": text, "reason_code": raw} if text and text != raw else row


def status(tool_input) -> str:
    """任务的真实进度（以站点为准：先收尾一次）：各张的编号、状态、Express 单号、没录的原因。"""
    name = str((tool_input or {}).get("task") or "")
    view = tasks.read(name)
    if view["status"] not in ("preparing",) + tasks.FINAL:
        view = tasks.settle(name)
    docs = [_worded(row) for row in _numbered(view)]
    counts: dict = {}
    for row in docs:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    out = {"task": view["name"], "company": view["company_title"] or view["company"], "period": view["period"],
           "status": view["status"], "computer": view["computer"], "counts": counts, "documents": docs}
    if view["status"] == "preparing":
        out["next"] = NEXT_PREPARING  # 11-08 生产：CC 只说了「准备中、没有单据」，没说要人做什么
        out.update(_preparing(view))
    elif view["status"] == "attention":
        out["next"] = NEXT_ATTENTION
    if view["status"] in ("preparing", "queued"):
        # 任务主人此刻没有能写的电脑（未连接 / 够不到账套 / 只有同事的在线 / 关着）：给模型单据页同一句，照话告诉用户（11-16 H 组）
        from pearnly_core.express_operator import products

        missing = products.computer_words(view["company"], tasks._user())
        if missing:
            out["no_computer"] = missing
    return json.dumps(out, ensure_ascii=False, default=str)


WAITING_SHOWN = 20  # 准备中给模型的待录单最多这么多张（总数另给）
DOC_KEYS = ("doc_id", "kind", "date", "party", "ref", "total", "other_month")


def _preparing(view: dict) -> dict:
    """还没开始的任务：这次代录待录的单（产品 ``sources``，同写入计划取的那份清单的前几张）与对话里放进来的每一份此刻识别到哪
    （``uploads.read``，同页面上传卡片）。11-16 H3：CC 只看得到「还没录」，不知道票已放进来、已识别，叫用户去别的页面上传。"""
    from pearnly_core.express_operator import listed, products, uploads

    found = products.sources(view["company"], view.get("period") or "", 0, WAITING_SHOWN)
    rows = [row for row in found.get("documents") or [] if isinstance(row, dict) and row.get("doc_id")]
    waiting = [{key: row[key] for key in DOC_KEYS if row.get(key) not in (None, "")} for row in rows if row.get("group") != "skip"]
    # 这次列出过、之后被直写写进账本的单：照实给出（已入账 · 单号），开始时跳过；不说成「没有了」（主会话 11-24）
    snapshot = listed.remember(view["name"], view["company"], rows)
    gone = listed.gone(view["company"], snapshot, [row["doc_id"] for row in rows], period=view.get("period") or "")
    entered = [{key: row[key] for key in ("doc_id", "ref", "party", "docnum") if row.get(key)} for row in gone if row["state"] == "saved"]
    left = [{key: row[key] for key in ("doc_id", "ref", "party", "state") if row.get(key)} for row in gone if row["state"] != "saved"]
    sent = [{"filename": row.get("filename") or "", "state": row.get("state") or "",
             "documents": [{key: doc[key] for key in ("doc_id", "ref", "party", "date") if doc.get(key)}
                           for doc in row.get("documents") or [] if isinstance(doc, dict)]}
            for row in uploads.read(view["name"]).get("uploads") or []]
    out = {"waiting": waiting, "waiting_total": int(found.get("total") or len(waiting)), "uploads": sent}
    if entered:
        out["entered"] = entered
    if left:
        out["left"] = left
    return out


# 状态之外给模型的下一步（只给模型读，不是界面文案）：不提内部名词
NEXT_PREPARING = ("Nothing has been entered yet. waiting lists the documents this task will enter (files the user put in "
                  "this conversation are recognized here and appear there once read; uploads shows each file's state), so "
                  "never send the user elsewhere to upload. When the user wants them entered: settle the open questions, "
                  "make the write plan (pearnly_write_plan), say it back in one sentence and start it (pearnly_write_start) "
                  "only after the user agrees in a new message. entered lists documents from this task's list that were "
                  "entered in Express another way in the meantime (docnum is the Express number): they are already entered "
                  "and the task skips them; say so with the number, never that they disappeared. left lists documents from this "
                  "task's list that are no longer waiting for another reason (state: not_done, cancelled, queued elsewhere, "
                  "or unknown): say what state each is in; the task does not enter them.")
NEXT_ATTENTION = ("Some documents need the user: the cards in this conversation ask what is missing (one question covers "
                  "every document that needs the same answer). Point the user to them; do not ask the same thing again.")


def control(tool_input) -> str:
    """跳过 / 重做这个任务里的几张（按编号或单据编号）：记下，交给执行的电脑去做。"""
    values = tool_input or {}
    name, action = str(values.get("task") or ""), str(values.get("action") or "")
    docs = _numbered(tasks.read(name))
    by_n = {row["n"]: row["doc_id"] for row in docs}
    by_id = {row["doc_id"] for row in docs}
    wanted = []
    for item in values.get("documents") or []:
        key = str(item).strip()
        if key.isdigit() and int(key) in by_n:
            wanted.append(by_n[int(key)])
        elif key in by_id:
            wanted.append(key)
        else:
            frappe.throw(_("Invalid request"))
    return json.dumps(tasks.push_control(name, action, wanted), ensure_ascii=False, default=str)


TOOLS = [
    {"name": "express_operator_status", "modes": ["operator"],
     "label": "Checked the hands-on task",
     "description": "Read the Express entry task this conversation belongs to: overall status, the computer doing it, "
                    "and every document numbered from 1 in task order with its state (queued, saved, not_done, cancelled, "
                    "skipped), the Express document number once saved, and the reason it was not entered (in the user's words; reason_code is for you only). Use it to answer "
                    "questions such as how many were entered today or why document 2 was not entered. task is the id given "
                    "in the express_task note. next, when present, is what the user has to do now: tell them in plain "
                    "words. Before the task starts it also gives waiting (the documents it will enter, waiting_total in all) "
                    "and uploads (each file put in this conversation and what it was read as). no_computer, when present, "
                    "says why no computer of the user can enter them now (not connected, offline, ...): tell the user in "
                    "those words and never say they were handed to a computer.",
     "input_schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]},
     "handler": "pearnly_core.express_operator.tools.status"},
    {"name": "express_operator_control", "modes": ["operator"],
     "label": "Updated the hands-on task",
     "description": "Skip or redo documents of the Express entry task this conversation belongs to. action skip leaves "
                    "the documents out of this run; action redo (retry) enters them again. documents are the numbers from "
                    "express_operator_status (1, 2, ...) or their doc_id. The computer doing the task carries it out when it "
                    "is online; say so if it is not. Only call it when the user asked for it.",
     "input_schema": {"type": "object", "properties": {
         "task": {"type": "string"},
         "action": {"type": "string", "enum": list(tasks.CONTROL_ACTIONS)},
         "documents": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
         "required": ["task", "action", "documents"]},
     "handler": "pearnly_core.express_operator.tools.control"},
]

STEP_NAMES = {
    "express_operator_status": "Checking the hands-on task",
    "express_operator_control": "Updating the hands-on task",
}
