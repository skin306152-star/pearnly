"""代录事件源（多产品共用，统一对话框架，评估 2026-11-04 第 14 节，主会话 11-04 审定）：代录任务就是一段标准对话会话，
任务记录由这里投影成统一对话事件（Managed Agents 格式 + ``pearnly.progress`` / ``pearnly.tool_run``），``sessions.events``
按时间并进同一段会话，实时推送走对话同一个通道（``contracts.REALTIME_EVENT``，只推会话主人）。

- 不另存事件表：每次读都由任务记录现投影（任务状态、单据子表、上传子表、产品此刻的识别状态），编号由记录推出、固定不变
  （``pop_<任务号>_…``），同一份记录两次投影逐条相同，跨设备、刷新回放一致，页面按编号去重。事件清单见评估 14.10。
- 只读、只转调：不新增写账、推送、扣费、判单的路径。唯一写的是单据子表的两个时间（``stamp``：交进队列 ``queued_at``、
  有结论 ``done_at``，任务保存前按与库里的差别记），事件按它排序。
- 推送三个时刻：任务保存后（``tasks._announce`` → ``publish``，推这次新出现的事件）；产品回执写回后（两个产品登记的只读入口
  ``receipt``：这张单属于哪个还在跑的任务，就提交后排一次以任务主人身份的 ``settle``，它保存时照常推）；上传的识别状态变了
  （共用识别队列已推 ``pearnly_ocr_work``，页面收到即补拉，这里不另推）。推不出去不影响业务，页面下一次补拉照样拿到。
- 报告（``report``）：任务的每张单一行，列同原来页面上的导出报告；服务端按任务记录现生成，只给任务主人。
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta

import frappe
from frappe import _

from pearnly_core.assistant import contracts as chat
from pearnly_core.express_operator import contracts, products, shapes, tasks

PREFIX = "pop_"
SESSION_PREFIX = "op_"  # 补丁补建、新建的代录会话编号：op_<任务号>（远端会话第一次发话时才开）
OPEN = ("pending", "queued")
CONCLUDED = ("saved", "not_done", "cancelled", "skipped")
GROUPS = ("purchase", "sales", "expense", "payment", "skip")
RECEIPT_STATES = ("queued", "running", "attention")  # 回执可能还改得动的任务
RECEIPT_JOB = "pearnly_core.express_operator.feed.refresh"
PUSH_WINDOW = timedelta(minutes=2)  # 取不到保存前的样子时，推这么久以内的事件（页面按编号去重）
REPORT_URL = "/api/method/pearnly_core.express_operator.feed.report?name="

UPLOAD = "operator_upload"
SOURCES = "operator_sources"
START = "operator_start"
DOCUMENT = "operator_document"
CHOOSE = "operator_choose"
RESUME = "operator_resume"
TOOL_NAMES = (UPLOAD, SOURCES, START, DOCUMENT, CHOOSE, RESUME)


def session_of(task_name) -> str:
    return SESSION_PREFIX + str(task_name)


def is_local(value) -> bool:
    """事件编号是不是代录事件源出的（页面不拿它当补拉锚点，``confirm`` 不转给引擎）。"""
    return str(value or "").startswith(PREFIX)


# ── 时间 ────────────────────────────────────────────────────────────────────
def _moment(value):
    if not value:
        return None
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())


def _iso(moment, tz: str, bump: int = 0) -> str:
    """站点时区的裸时间（+ bump 微秒）→ RFC3339 UTC，固定带六位小数（字符串先后即时间先后）。同一步的调用、进度、结果靠 bump
    排先后。"""
    utc = chat.instant(chat.utc_iso(moment + timedelta(microseconds=bump), tz))
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


EPOCH = datetime(1970, 1, 1)


def _ms(moment) -> int:
    """编号里的时刻：站点时区的裸时间按毫秒数写（不经本机时区，各台服务器算出同一个数）。"""
    return int((moment.replace(tzinfo=None) - EPOCH).total_seconds() * 1000) if moment else 0


def _get(row, key):
    return row.get(key) if hasattr(row, "get") else getattr(row, key, None)


# ── 原因 ────────────────────────────────────────────────────────────────────
def reason_code(raw) -> str:
    """单据上记的原因 → 代码：CC 回执的 ``[OPERATOR_X] 原文`` 拆成 ``x``，其余（产品的码、结果不明）原样。"""
    text = str(raw or "").strip()
    found = tasks._ACK_REASON.match(text)
    return found.group(1).lower() if found else text[:80]


# 1.12.15 以前的 CC 说明以转人工截图的本机路径开头（``C:\…\need-human.png · ``）：本机路径不给页面
_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:\\|\\\\)[^·]*?\.png\s*·\s*")
DETAIL_MAX = 2000


def reason_detail(raw) -> str:
    """CC 回执 ``[OPERATOR_X] 说明`` 里的说明原文（AI 接手的结论在最前，1.12.15 起整段带来）：卡片照原文显示；
    去掉旧版开头的本机截图路径；不是代录回执的（产品的码、结果不明）没有说明。"""
    text = str(raw or "").strip()
    found = tasks._ACK_REASON.match(text)
    if not found:
        return ""
    return _LOCAL_PATH.sub("", text[found.end():].strip(), count=1).strip()[:DETAIL_MAX]


def _resumable(raw) -> bool:
    return tasks._batch_stopped(raw)


# ── 投影 ────────────────────────────────────────────────────────────────────
def _payload(value) -> list:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)}]


class _Out:
    def __init__(self, tz):
        self.tz, self.items = tz, []

    def add(self, event, moment, bump=0):
        event["processed_at"] = _iso(moment, self.tz, bump)
        self.items.append(event)
        return event

    def tool(self, eid, name, args, moment, custom=False, bump=0):
        kind = "agent.custom_tool_use" if custom else "agent.tool_use"
        return self.add({"type": kind, "id": eid, "name": name, "input": args}, moment, bump)

    def result(self, eid, use_id, value, moment, is_error=False, bump=0):
        return self.add({"type": "agent.tool_result", "id": eid, "tool_use_id": use_id, "content": _payload(value),
                         "is_error": bool(is_error)}, moment, bump)

    def answer(self, use_id, value, moment, bump=0):
        """卡片答完：自定义工具的结果用 user.custom_tool_result（页面把自定义工具的 agent.tool_result 当成停止时的中断）。"""
        self.add({"type": "user.custom_tool_result", "id": use_id + "_r", "custom_tool_use_id": use_id,
                  "content": _payload(value), "is_error": False}, moment, bump)
        self.run(use_id, "done", moment, bump)

    def run(self, use_id, status, moment, bump=0):
        event = chat.tool_run_event(use_id, status, None)
        return self.add(event, moment, bump)

    def progress(self, eid, use_id, done, total, label, moment, detail="", bump=0):
        return self.add(chat.progress_event(eid, None, use_id, done, total, label, detail), moment, bump)


def _docs(task) -> list:
    """单据子表各行，附上投影要用的时间（R5：旧任务没有时间的按开始时刻加序号排）。"""
    base = _moment(_get(task, "started_at")) or _moment(_get(task, "creation"))
    finished = _moment(_get(task, "finished_at"))
    out = []
    for index, row in enumerate(_get(task, "documents") or [], start=1):
        queued = _moment(_get(row, "queued_at")) or base + timedelta(microseconds=index * 10)
        done = _moment(_get(row, "done_at"))
        if not done and _get(row, "state") in CONCLUDED:
            done = max(finished or queued, queued) + timedelta(microseconds=5)
        out.append({"row": row, "n": index, "key": str(_get(row, "name") or index), "queued": queued, "done": done,
                    "state": str(_get(row, "state") or "pending"), "raw": str(_get(row, "reason") or "")})
    return out


def _uploads(task) -> list:
    """进了对话的上传（没有上传时刻的是对话输入框里的一份，带着它的消息还没发出：``uploads.take``，不出卡片）。"""
    return [{"row": row, "key": str(_get(row, "name") or index), "at": _moment(_get(row, "uploaded_at"))}
            for index, row in enumerate(_get(task, "uploads") or [], start=1) if _moment(_get(row, "uploaded_at"))]


def _doc_view(item) -> dict:
    row = item["row"]
    return {"doc_id": str(_get(row, "doc_id") or ""), "ref": str(_get(row, "ref") or ""), "party": str(_get(row, "party") or "")}


def last_time(task):
    """任务记录里最晚的时刻（会话状态、整段进度的时间）。"""
    times = [_moment(_get(task, key)) for key in ("creation", "started_at", "finished_at")]
    for item in _docs(task):
        times += [item["queued"], item["done"]]
    times += [item["at"] for item in _uploads(task)]
    return max(t for t in times if t)


def project(task, upload_states=None, tz: str = "UTC", floor=None) -> list[dict]:
    """一个任务记录 → 统一对话事件（旧 → 新）。``upload_states``：``{上传行名: shapes.upload_view(...)}``，产品此刻的识别状态；
    不给（实时推送）就只出上传这一步本身。``floor``：资料与开始前卡片不早于这一刻（站点时区；整段重放时取引擎记下的第一句）。
    纯函数：同一份记录、同一份识别状态两次投影逐条相同。"""
    name = str(_get(task, "name"))
    p = f"{PREFIX}{name}_"
    out = _Out(tz)
    status = str(_get(task, "status") or "preparing")
    created = _moment(_get(task, "creation"))
    started = _moment(_get(task, "started_at")) or created
    company = str(_get(task, "company") or "")
    base = {"task": name, "company": company}
    head = {**base, "company_title": str(_get(task, "company_title") or ""), "period": str(_get(task, "period") or "")}
    docs = _docs(task)
    uploads = _uploads(task)
    # 资料与开始前卡片排在用户这次的话与文件之后（11-08 生产：锚在建任务那一刻，卡片永远排在用户第一句前面；11-09 发布前：单多时
    # 卡片很高，把用户的话和回复挤出视口）：取建任务、第一句发出（``message_at``，发出成功后记）与最后一份上传（同样发出后记）里
    # 晚的那个。不往后挪成未来的时刻：页面按服务端时间取舍会话状态，先收到未来的「等你点开始」，后到的「本轮结束」就被当成旧的
    # 丢掉、状态行停在「正在执行」。整段重放时 ``floor``（引擎记下的第一句时刻）再垫一次，引擎的时刻比本站记的晚几百毫秒。
    # 建了任务、第一句还没发出：先不出这两张卡（发出时记上时刻、推送出来）
    said = _moment(_get(task, "message_at"))
    moments = [created, said, *[item["at"] for item in uploads if item["at"]], floor]
    anchor = max(moment for moment in moments if moment) + timedelta(microseconds=10)
    cards = bool(said or uploads) or status != "preparing"

    # E4 资料清单
    if cards:
        out.tool(p + "sources", SOURCES, head, anchor)
    if status != "preparing":
        groups = {}
        for item in docs:
            group = str(_get(item["row"], "doc_group") or "")
            groups[group] = groups.get(group, 0) + 1
        out.result(p + "sources_r", p + "sources", {"total": len(docs), "groups": groups}, started)

    # E5 / E6 开始前确认
    start = p + "start"
    if cards:
        out.tool(start, START, head, anchor, custom=True, bump=1)
    if status == "preparing":
        if cards:
            out.run(start, "awaiting_approval", anchor, bump=1)
    else:
        not_sent = [{**_doc_view(item), "reason": reason_code(item["raw"])} for item in docs
                    if item["state"] == "not_done" and _get(item["row"], "queued_at")
                    and _moment(_get(item["row"], "queued_at")) == _moment(_get(item["row"], "done_at"))]
        out.answer(start, {"sent": len(docs) - len(not_sent), "not_sent": not_sent,
                           "computer": str(_get(task, "computer") or ""), "start_at": str(_get(task, "start_at") or "")},
                   max(started, anchor), bump=2)

    # E3 上传
    for item in uploads:
        row, use = item["row"], f"{p}u{item['key']}"
        out.tool(use, UPLOAD, {**base, **{k: _get(row, k) or ("" if k != "size" else 0)
                                          for k in ("request_key", "filename", "size", "batch", "source")}}, item["at"])
        found = (upload_states or {}).get(item["key"])
        if not found or not found.get("state"):
            continue
        state = found["state"]
        if state in ("queued", "reading"):
            label = "queued" if state == "queued" else "reading_pages"
            out.progress(f"{use}_p{state}_{found.get('pages_done') or 0}", use, found.get("pages_done"),
                         found.get("page_count"), label, item["at"], bump=1)
        elif state == "done":
            out.result(f"{use}_rdone", use, {"state": "done", "page_count": found.get("page_count") or 0,
                                             "documents": found.get("documents") or []}, item["at"], bump=2)
        else:
            out.result(f"{use}_rfailed", use, {"state": "failed", "code": found.get("code") or ""}, item["at"],
                       is_error=True, bump=2)

    # E8 / E9 每张单；E11 要人处理的单；中途交回的卡片收口
    awaiting, requeued, stopped = [], [], []
    for item in docs:
        row, use = item["row"], f"{p}d{item['key']}"
        view = _doc_view(item)
        out.tool(use, DOCUMENT, {**base, "n": item["n"], **view, "group": str(_get(row, "doc_group") or "")}, item["queued"])
        if item["state"] in OPEN:
            blocked = str(_get(row, "blocked_by") or "")
            out.progress(f"{use}_q{_ms(item['queued'])}", use, 0, 0, "blocked" if blocked else "queued", item["queued"],
                         detail=blocked, bump=1)
            if item["done"] and item["done"] < item["queued"]:
                requeued.append(item)
                out.answer(f"{p}c{item['key']}_{_ms(item['done'])}", {"answered": True}, item["queued"], bump=2)
            continue
        code = reason_code(item["raw"])
        unknown = item["raw"] == tasks.RESULT_UNKNOWN
        detail = reason_detail(item["raw"])
        value = {"state": item["state"], "docnum": str(_get(row, "docnum") or ""),
                 "voided_docnum": str(_get(row, "voided_docnum") or ""), "reason": code, "detail": detail,
                 "level": contracts.level(code) if code else "", "result_unknown": unknown, **view}
        out.result(f"{use}_r{item['state']}_{_ms(item['done'])}", use, value, item["done"],
                   is_error=item["state"] == "not_done", bump=0)
        if item["state"] != "not_done":
            continue
        if _resumable(item["raw"]):
            stopped.append(item)
            continue
        card = f"{p}c{item['key']}_{_ms(item['done'])}"
        out.tool(card, CHOOSE, {**base, **view, "reason": code, "detail": detail, "level": contracts.level(code),
                                "result_unknown": unknown}, item["done"], custom=True, bump=1)
        out.run(card, "awaiting_approval", item["done"], bump=1)
        awaiting.append(card)

    # E12 整批停 → 继续
    if requeued:
        key = max(item["done"] for item in requeued)
        out.answer(f"{p}resume_{_ms(key)}", {"resumed": True}, max(item["queued"] for item in requeued), bump=3)
    if stopped and status in ("attention", "stopped"):
        when = max(item["done"] for item in stopped)
        stopper = next((item for item in sorted(stopped, key=lambda i: i["done"])
                        if reason_code(item["raw"]) in contracts.BATCH_STOPPERS), stopped[0])
        card = f"{p}resume_{_ms(when)}"
        out.tool(card, RESUME, {**base, **_doc_view(stopper), "reason": reason_code(stopper["raw"]),
                                "detail": reason_detail(stopper["raw"]),
                                "doc_ids": [_doc_view(item)["doc_id"] for item in stopped]}, when, custom=True, bump=2)
        out.run(card, "awaiting_approval", when, bump=2)
        awaiting.append(card)

    # E7 / E14 / E17 整个会话的状态
    last = max(last_time(task), anchor)  # 排在开始前卡片之后：它的时刻取过用户这次的话
    open_docs = [item for item in docs if item["state"] in OPEN]
    concluded = len(docs) - len(open_docs)
    if status == "preparing":
        if cards:
            _status(out, p, "wait", {"type": "requires_action", "event_ids": [start]}, last)
    elif status in ("queued", "running"):
        out.add({"type": "session.status_running", "id": f"{p}st_run_{_ms(last)}"}, last, 5)
        label = "waiting_computer" if status == "queued" else "entering"
        out.progress(f"{p}pg_{label}_{concluded}_{_ms(last)}", "", concluded, len(docs), label, last,
                     bump=6 if status == "queued" else 7)
    else:
        if not open_docs:
            counts = {state: sum(1 for item in docs if item["state"] == state) for state in CONCLUDED}
            out.add({"type": "agent.message", "id": f"{p}end_{status}_{_ms(last)}", "content": [{"type": "text", "text": ""}],
                     "operator": {"kind": status if status in ("finished", "stopped", "attention") else "finished",
                                  "task": name, "counts": counts,
                                  "report": {"filename": report_name(task), "url": REPORT_URL + name}}}, last, 4)
        if awaiting:
            _status(out, p, "act", {"type": "requires_action", "event_ids": awaiting}, last)
        else:
            _status(out, p, "end", {"type": "end_turn"}, last)
    return sorted(out.items, key=lambda event: event["processed_at"])


def _status(out, p, kind, stop, last) -> None:
    out.add({"type": "session.status_idle", "id": f"{p}st_{kind}_{_ms(last)}", "stop_reason": stop}, last, 5)


def awaiting(events) -> list[str]:
    """投影里此刻停着等人的卡片编号（最后一条会话状态是 requires_action 时）。"""
    for event in reversed(events):
        if str(event.get("type") or "").startswith(chat.STATUS_EVENT):
            return chat.parked_ids(event)
    return []


def run_state(events, as_of=None) -> dict:
    """代录会话按任务的运行状态（同 ``contracts.run_state`` 的形状）：在排在录 → running，等着人点的卡片 → requires_action。"""
    for event in reversed(events):
        kind = str(event.get("type") or "")
        if kind == "session.status_running":
            return {"status": "running", "parked": [], "awaiting_approval": [], "executing": [], "as_of": as_of}
        if kind == "session.status_idle":
            parked = chat.parked_ids(event)
            return {"status": "requires_action" if parked else "idle", "parked": parked, "awaiting_approval": parked,
                    "executing": [], "as_of": as_of}
    return {"status": "idle", "parked": [], "awaiting_approval": [], "executing": [], "as_of": as_of}


def always_sent(event, prefix: str) -> bool:
    """增量补拉不按时间筛的：会话状态、整段进度、卡片执行状态（少，且时间可能早于锚点），以及上传各步（识别状态没有时间）。"""
    kind = str(event.get("type") or "")
    if kind.startswith(chat.STATUS_EVENT) or kind == chat.TOOL_RUN_EVENT:
        return True
    if kind == chat.PROGRESS_EVENT and not event.get("tool_use_id"):
        return True
    ids = (event.get("id"), event.get("tool_use_id"))
    return any(str(value or "").startswith(prefix + "u") for value in ids)


def since(events, moment, prefix: str, slack=timedelta(minutes=5)) -> list[dict]:
    """增量：锚点时间（带时区，前推 ``slack``）之后的，加上不按时间筛的那几类。"""
    floor = moment - slack
    return [event for event in events
            if always_sent(event, prefix) or (chat.instant(event.get("processed_at")) or floor) >= floor]


# ── 任务保存前：记时间 ───────────────────────────────────────────────────────
def stamp(doc, before, now) -> None:
    """单据子表的 ``queued_at`` / ``done_at``：与库里的那一份（``before``：``{行名: {state, reason, docnum}}``）比，交进队列
    （新行、或有结论后又交回）记 ``queued_at``；有了结论或结论变了记 ``done_at``（交回队列时留着上一轮的，页面据此收口上一轮的卡片）。
    只加时间，不改任何判断。"""
    for row in doc.get("documents") or []:
        old = before.get(_get(row, "name")) if _get(row, "name") else None
        state = str(_get(row, "state") or "pending")
        if old is None:
            row.queued_at = row.queued_at or now
            if state in CONCLUDED:
                row.done_at = row.done_at or now
            continue
        was = str(old.get("state") or "pending")
        if state in OPEN and was in CONCLUDED:
            row.queued_at = now
        elif state in CONCLUDED and (was in OPEN or any(str(old.get(k) or "") != str(_get(row, k) or "")
                                                        for k in ("state", "reason", "docnum"))):
            row.done_at = now


def stored_rows(name) -> dict:
    if not name:
        return {}
    rows = frappe.get_all(tasks.DOCUMENT, filters={"parent": name, "parenttype": tasks.DOCTYPE},  # bounded: 一个任务 ≤ 200 张
                          fields=["name", "state", "reason", "docnum"], limit_page_length=0)
    return {row.name: row for row in rows}


# ── 站点：读、推 ─────────────────────────────────────────────────────────────
def _tz() -> str:
    return frappe.utils.get_system_timezone()


def upload_states(doc) -> dict:
    """任务里每份上传此刻的识别状态（产品现读，同 ``uploads.read``），按上传行名。读不到的不给。"""
    rows = [row for row in doc.get("uploads") or [] if row.batch]
    if not rows:
        return {}
    try:
        found = products.uploads(doc.company, doc.period or "", [row.batch for row in rows])
    except Exception:
        frappe.log_error(title="Operator upload states read failed")
        return {}
    return {row.name: shapes.upload_view({"filename": row.filename, "batch": row.batch}, found.get(row.batch),
                                         doc.period or "") for row in rows}


def task_of(row):
    """代录会话的任务记录（本人的；读不到回 None）。"""
    name = row.get("operator_task") if row else None
    if not name:
        return None
    found = frappe.db.get_value(tasks.DOCTYPE, {"name": name, "owner": row.get("user") or frappe.session.user}, "name")
    return frappe.get_doc(tasks.DOCTYPE, found) if found else None


def events(row, moment=None, said=None) -> list[dict]:
    """``sessions.events`` 并进来的那一路：整段（``moment`` 为空）或锚点时间之后的。``said``：整段重放时引擎记下的第一句用户
    消息时刻（RFC3339），卡片垫在它之后。"""
    doc = task_of(row)
    if doc is None:
        return []
    tz = _tz()
    floor = chat.local_time(chat.instant(said), tz) if said and chat.instant(said) else None
    found = project(doc, upload_states(doc), tz, floor)
    return found if moment is None else since(found, moment, f"{PREFIX}{doc.name}_u")


def publish(doc) -> None:
    """任务保存后（``tasks._announce``）：这次新出现的事件推给会话主人（同提醒，``{session_id, event}``，提交后）。上传的识别
    状态不在这里推。推不出去只记日志。"""
    if not doc.get("session_id"):
        return
    try:
        before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
        tz = _tz()
        now = project(doc, None, tz)
        if before is not None:
            seen = {event["id"] for event in project(before, None, tz)}
            fresh = [event for event in now if event["id"] not in seen]
        else:
            floor = frappe.utils.now_datetime() - PUSH_WINDOW
            fresh = now if doc.get("status") == "preparing" and not doc.get("documents") else [
                event for event in now if (chat.instant(event["processed_at"]) and chat.local_time(
                    chat.instant(event["processed_at"]), tz) >= floor) or always_sent(event, f"{PREFIX}{doc.name}_u")]
        for event in fresh:
            frappe.publish_realtime(chat.REALTIME_EVENT, {"session_id": doc.session_id, "event": event}, user=doc.owner,
                                    after_commit=True)
    except Exception:
        frappe.log_error(title="Operator feed publish failed")


# ── 产品回执写回后的只读入口 ─────────────────────────────────────────────────
def receipt(doc_ids, *_args, **_kwargs) -> None:
    """产品回执写回后（Cowork ``cowork_push_state_changed`` 登记、ERP ``express.pending.notify`` 末尾调用；都在写回的事务里）：
    这几张单属于哪个还可能变的代录任务，就排一次提交后的刷新（``refresh``）。只读、不锁；出错只记日志，不影响回执。"""
    try:
        ids = [doc_ids] if isinstance(doc_ids, str) else [str(item) for item in doc_ids or () if item]
        ids = [item for item in ids if item]
        if not ids:
            return
        parents = list(dict.fromkeys(frappe.get_all(tasks.DOCUMENT, filters={"parenttype": tasks.DOCTYPE, "doc_id": ["in", ids]},
                                                    pluck="parent", limit_page_length=50)))
        if not parents:
            return
        names = frappe.get_all(tasks.DOCTYPE, filters={"name": ["in", parents], "status": ["in", list(RECEIPT_STATES)]},
                               pluck="name", limit_page_length=50)
        for name in names:
            frappe.enqueue(RECEIPT_JOB, queue="short", enqueue_after_commit=True, job_id=f"pearnly-operator-feed:{name}",
                           deduplicate=True, task=name)
    except Exception:
        frappe.log_error(title="Operator receipt hook failed")


def refresh(task) -> None:
    """后台任务：以任务主人身份做一次 ``settle``（同页面的「以站点为准收尾」：没定下来的单问产品，写回子表），保存时照常推事件。"""
    owner = frappe.db.get_value(tasks.DOCTYPE, task, "owner")
    if not owner:
        return
    frappe.set_user(owner)
    tasks.settle(task)
    frappe.db.commit()


# ── 报告（E15）────────────────────────────────────────────────────────────────
REPORT_COLUMNS = ("ref", "party", "total", "status", "number", "reason", "time")
STATE_KEY = {"saved": "saved", "not_done": "notDone", "cancelled": "skipped", "skipped": "skipped", "pending": "waiting",
             "queued": "waiting"}


def report_name(task) -> str:
    title = " ".join(part for part in (str(_get(task, "company_title") or _get(task, "company") or ""),
                                       str(_get(task, "period") or "")) if part)
    safe = "".join(ch for ch in title if ch not in '\\/:*?"<>|').strip() or str(_get(task, "name"))
    return f"{safe}.csv"


def _reason_text(code, translate) -> str:
    if not code:
        return ""
    if code == tasks.RESULT_UNKNOWN:
        return translate("operator.report.reason.result_unknown")
    if code in contracts.REASONS:
        return translate(f"operator.report.reason.{code}")
    return translate("operator.report.reason.unknown")


def report_rows(task, translate) -> list[list[str]]:
    """每张单一行：单号、对方、金额、状态、Express 单号、原因、用时（同原来页面上的 ``reportCsv``）。金额与每张用时只在执行
    电脑上，服务端不估、不补，留空。"""
    head = [translate(f"operator.report.column.{key}") for key in REPORT_COLUMNS]
    rows = [head]
    for row in _get(task, "documents") or []:
        state = str(_get(row, "state") or "pending")
        code = reason_code(_get(row, "reason"))
        if state == "saved":
            reason = translate("operator.report.mismatch" if code == "readback_mismatch" else "operator.report.match")
        elif state in OPEN:
            reason = str(_get(row, "blocked_by") or "")
        else:
            parts = [_reason_text(code or ("voided" if state == "cancelled" else ""), translate)]
            if _get(row, "voided_docnum"):
                parts.append(translate("operator.report.voided").format(_get(row, "voided_docnum")))
            reason = " · ".join(part for part in parts if part)
        rows.append([str(_get(row, "ref") or _get(row, "doc_id") or ""), str(_get(row, "party") or ""), "",
                     translate(f"operator.report.state.{STATE_KEY.get(state, 'waiting')}"),
                     str(_get(row, "docnum") or ""), reason, ""])
    return rows


def report_csv(task, translate) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerows(report_rows(task, translate))
    return "﻿" + buffer.getvalue()  # BOM：Excel 按 UTF-8 认中文、泰文


@frappe.whitelist(methods=["GET"])
def report(name):
    """任务报告 CSV（只给任务主人，同 ``tasks.read``）。"""
    doc = tasks._own(name)
    frappe.response["filename"] = report_name(doc)
    frappe.response["filecontent"] = report_csv(doc, _).encode("utf-8")
    frappe.response["type"] = "download"


__all__ = ["PREFIX", "SESSION_PREFIX", "TOOL_NAMES", "awaiting", "events", "is_local", "last_time", "project", "publish",
           "reason_detail", "receipt", "reason_code", "refresh", "report", "report_csv", "report_rows", "run_state", "session_of", "since",
           "stamp", "stored_rows"]
