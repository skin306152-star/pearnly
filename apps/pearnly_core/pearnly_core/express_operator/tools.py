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


def status(tool_input) -> str:
    """任务的真实进度（以站点为准：先收尾一次）：各张的编号、状态、Express 单号、没录的原因。"""
    name = str((tool_input or {}).get("task") or "")
    view = tasks.read(name)
    if view["status"] not in ("preparing",) + tasks.FINAL:
        view = tasks.settle(name)
    docs = _numbered(view)
    counts: dict = {}
    for row in docs:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    out = {"task": view["name"], "company": view["company_title"] or view["company"], "period": view["period"],
           "status": view["status"], "computer": view["computer"], "counts": counts, "documents": docs}
    if view["status"] == "preparing":
        out["next"] = NEXT_PREPARING  # 11-08 生产：CC 只说了「准备中、没有单据」，没说要人做什么
    elif view["status"] == "attention":
        out["next"] = NEXT_ATTENTION
    return json.dumps(out, ensure_ascii=False, default=str)


# 状态之外给模型的下一步（只给模型读，不是界面文案）：不提内部名词
NEXT_PREPARING = ("Nothing has been entered yet: the documents in this conversation are entered when the user presses "
                  "Start on the card in this conversation, after answering its questions there. Tell the user exactly that.")
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
                    "skipped), the Express document number once saved, and the reason it was not entered. Use it to answer "
                    "questions such as how many were entered today or why document 2 was not entered. task is the id given "
                    "in the express_task note. next, when present, is what the user has to do now: tell them in plain "
                    "words.",
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
