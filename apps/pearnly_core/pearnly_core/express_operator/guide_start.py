"""代录开始前那张卡并进引导（A4，多产品共用；约定「A4-代录开始前题卡-约定-11-16」）。

代录任务还在准备中时，开始前只出一张题卡：开一次 documents 引导运行（``guide.run.start(..., doc_ids=…)``），单据是这家公司这个月
的完整待录清单（资料区、网页 / LINE 进来的、上传过的 ``I:``，同写入计划 `api.waiting` 那份，不看页面加载了几页）。
题由写账前置判定出（开始卡、预选卡、引导卡三张合一，`guide.documents`）；原开始卡上的三样非题内容在这里变成卡上的「做不做」题：

- 同一张票已写进 Express（``written``，默认跳过）：跳过 / 再录一次（答再录一次的进 ``again``，交 `hand_in`）；
- 上次写入结果不明：已入账 / 没有入账（答了走 `api.resolve`，同开始卡上那两个按钮）；
- 这个月一张可录的都没有、别的月份有：各月几张（服务端数，`products.elsewhere`）+ 留在这个月；选了月份改任务月份
  （`sessions.move`，同对话上方的月份），按新月份重开这张卡。

都不预选（「做不做」类）。月份与张数是服务端数的，卡上的字照它出。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

import frappe
from frappe import _

AGAIN_PREFIX = "operator-again|"
RESOLVE_PREFIX = "operator-resolve|"
MONTH_KEY = "operator-month"
AGAIN, SKIP = "again", "skip"
ENTERED, NOT_ENTERED = "entered", "not_entered"
STAY = "stay"
MONTHS_SHOWN = 3
PAGE = 50


def _candidates(company: str, period: str) -> List[Dict[str, Any]]:
    """这个月等着录的单（资料分页取全，去掉人移到「不入账」的），按产品的资料顺序。"""
    from pearnly_core.express_operator import api, products

    rows, offset = [], 0
    while True:
        found = products.sources(company, str(period or ""), offset, PAGE)  # loop-query: 资料按页取全，最多 SOURCES_MAX 张
        page = [row for row in found.get("documents") or [] if isinstance(row, dict) and row.get("doc_id")]
        rows += page
        following = found.get("next_offset")
        if not page or following is None or int(following) <= offset or len(rows) > api.SOURCES_MAX:
            break
        offset = int(following)
    rows = list({str(row["doc_id"]): row for row in rows}.values())
    return [row for row in rows if row.get("group") != "skip"]


def _business(rows) -> str:
    """开这次引导用哪份声明（两份产品函数相同）：有采购票按采购，否则销售。只决定卡头的业务名。"""
    return "sales" if rows and all(str(row.get("kind") or row.get("group") or "") == "sales" for row in rows) else "purchase"


def start(task_name: str) -> Dict[str, Any]:
    """代录任务准备中：开（或重开）这次开始前的那张卡。本人、任务还在准备中才开。回引导运行的样子。"""
    from pearnly_core.assistant.guide import run
    from pearnly_core.express_operator import tasks

    task = tasks.read(task_name)
    if task.get("status") != "preparing":
        frappe.throw(_("Invalid request"))
    rows = _candidates(task["company"], task.get("period") or "")
    return run.start(str(task.get("session_id") or ""), _business(rows), doc_ids=[str(row["doc_id"]) for row in rows],
                     at="asking", operator={"task": task_name, "period": str(task.get("period") or "")})


def _precheck(company: str, doc_ids: List[str]) -> List[Dict[str, Any]]:
    from pearnly_core.express_operator import api, products

    out = []
    for start_at in range(0, len(doc_ids), api.BATCH_MAX):
        out += [doc for doc in products.precheck(company, doc_ids[start_at:start_at + api.BATCH_MAX]).get("documents") or []
                if isinstance(doc, dict)]  # loop-query: 按批预检（同写入计划 `api.waiting`），最多 SOURCES_MAX 张
    return out


def _whether(key: str, kind: str, docs: List[str], options: List[Dict[str, Any]], params: Mapping[str, Any]) -> Dict[str, Any]:
    return {"key": key, "kind": kind, "group": "book", "tier": "whether", "input": "choice", "title_key": f"guide.q.{kind}",
            "title_params": dict(params), "docs": list(docs), "count": len(docs), "options": options, "preselected": "",
            "remember": False, "free": False}


def questions(ctx: Mapping[str, Any], doc_ids: List[str], operator: Mapping[str, Any], again: List[str]) -> List[Dict[str, Any]]:
    """卡上这次代录自己的题（放在写账判定的题前面）：没有单时问月份；写过的问再录不录；结果不明的问入没入账。"""
    from pearnly_core.express_operator import products, tasks

    company = str(ctx.get("company") or "")
    if not doc_ids:
        months = products.elsewhere(company, operator.get("period") or "")[:MONTHS_SHOWN]
        if not months:
            return []
        options = [{"code": item["month"], "name": f"{item['month']} · {item['count']}"} for item in months]
        options.append({"code": STAY, "label_key": "guide.option.operator_month.stay"})
        return [_whether(MONTH_KEY, "operator_month", [], options, {"count": sum(item["count"] for item in months)})]
    out = []
    for doc in _precheck(company, doc_ids):
        doc_id = str(doc.get("doc_id") or "")
        label = str(doc.get("ref") or doc_id)
        if doc.get("state") == "written" and doc_id not in again:
            out.append(_whether(AGAIN_PREFIX + doc_id, "operator_again", [doc_id],
                                [{"code": SKIP, "label_key": "guide.option.operator_again.skip"},
                                 {"code": AGAIN, "label_key": "guide.option.operator_again.again"}],
                                {"label": label, "docnum": str(doc.get("docnum") or "")}))
        elif doc.get("state") == "blocked" and doc.get("reason") == tasks.RESULT_UNKNOWN:
            out.append(_whether(RESOLVE_PREFIX + doc_id, "operator_resolve", [doc_id],
                                [{"code": ENTERED, "label_key": "guide.option.operator_resolve.entered"},
                                 {"code": NOT_ENTERED, "label_key": "guide.option.operator_resolve.not_entered"}],
                                {"label": label}))
    return out


def own(key: str) -> bool:
    return key == MONTH_KEY or key.startswith((AGAIN_PREFIX, RESOLVE_PREFIX))


def apply(ctx: Mapping[str, Any], rows: List[Dict[str, str]], operator: Mapping[str, Any]) -> Dict[str, Any]:
    """落下卡上这几题的答案（不交给写账判定）：``{again: [加进再录的单], held: [这次不录的单], month?: 新月份}``。
    已入账 / 没有入账走 `api.resolve`（同开始卡上的按钮）；月份走 `sessions.move`（同对话上方的月份，任务跟着改）。"""
    from pearnly_core.assistant import sessions
    from pearnly_core.express_operator import api, products

    company = str(ctx.get("company") or "")
    out: Dict[str, Any] = {"again": [], "held": []}
    month = next((row["value"] for row in rows if row["key"] == MONTH_KEY), "")  # 一张卡至多一道月份题
    if month and month != STAY and month in {item["month"] for item in products.elsewhere(company, operator.get("period") or "")}:
        found = frappe.db.get_value(sessions.SESSION, {"session_id": ctx["session"], "user": frappe.session.user},
                                    ["project", "project_scope"], as_dict=True) or {}
        out["month"] = sessions.move(ctx["session"], found.get("project") or "", period=month,
                                     scope=found.get("project_scope") or "")["period"]
        return out
    for row in rows:
        key, value = row["key"], row["value"]
        if key.startswith(AGAIN_PREFIX):
            doc_id = key[len(AGAIN_PREFIX):]
            out["again" if value == AGAIN else "held"].append(doc_id)
        elif key.startswith(RESOLVE_PREFIX):
            doc_id = key[len(RESOLVE_PREFIX):]
            if value in (ENTERED, NOT_ENTERED):
                api.resolve(company, doc_id, value == ENTERED)  # loop-query: 卡上答了几张就标几张（结果不明的单很少）
            if value == ENTERED:
                out["held"].append(doc_id)
    return out


def card_head(ctx: Mapping[str, Any], doc_ids: List[str], operator: Mapping[str, Any]) -> Dict[str, Any]:
    """卡头（不是题）：写进哪家 · 哪本账套 · 哪台电脑（同开始卡 `writeLine` 的口径，按任务会话的本对话账套）、没电脑能写时那句。"""
    from pearnly_core.express_operator import api, products

    company = str(ctx.get("company") or "")
    found = api.precheck(company, doc_ids[:api.BATCH_MAX], task=str(operator.get("task") or "")) if doc_ids else {"targets": []}
    words = products.computer_words(company, frappe.session.user)
    return {"targets": list(found.get("targets") or ()), **({"computer": words} if words else {})}


__all__ = ["AGAIN", "AGAIN_PREFIX", "ENTERED", "MONTH_KEY", "NOT_ENTERED", "RESOLVE_PREFIX", "SKIP", "STAY", "apply", "card_head",
           "own", "questions", "start"]
