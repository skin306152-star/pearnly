"""写入计划：CC 写账由用户一句话下令开始（用户 11-10，总表二；主会话 11-10 批复，A 线 11-11）。直写与代录、Cowork 与 ERP 同一套。

卡片只做预选、看清、就地改，不放「开始」。写之前 CC 先调 ``pearnly_write_plan``：服务端按完整清单算出这次写哪几张、跳过哪几张及原因、
写进哪家公司哪本账套，记一份计划（`Pearnly Assistant Write Plan`，随对话删），CC 照它一句话复述、问用户可不可以。用户回了话
（录吧 / 可以 / 开始，或带修改），CC 再调 ``pearnly_write_start``，三道保险都在服务端：

1. 复述之后用户确实又说了一句：本对话发出的用户消息数（`sessions.user_turn`）比建计划时多，CC 不能自己替用户确认；
2. 只写看过之后没再改的票：逐张重算写账前置判定（`write_precheck`，与写入同一套判断）的摘要，和建计划时不同的这次不写；
3. 停：建计划之后点过停止 / 说过停止词（`sessions.last_stop`）不放行；直写已开始的计划在停之后不再推剩下的（`permit`）。

代录：计划的单由服务端按这家公司这个月的完整待录清单定（`express_operator.api.waiting`），开始时内部走代录交单同一条路
（`express_operator.api.hand_in`），页面不能直接开始。直写：开始只把计划标成已开始，CC 接着用本产品的推送工具推这些单，推送工具
先问 `permit`（不在已开始计划里的不推）。计划状态作为本站事件 ``pearnly.write_plan`` 并进对话事件（`feed`）：页面据此在
输入框上方出「录吧」（最新一份是 awaiting 时）。
"""
import hashlib
import json

import frappe
from frappe import _

DOCTYPE = "Pearnly Assistant Write Plan"
SESSION = "Pearnly Assistant Session"
TYPE = "pearnly.write_plan"
AWAITING, STARTED, REPLACED = "awaiting", "started", "replaced"
READY = "ready"  # 工具结果：有要写的（页面据此在输入框上方出「录吧」）
STATUSES = (AWAITING, STARTED, REPLACED)
CHECK_MAX = 200  # 一次交给写账前置判定几张（同 `write_precheck.api.BATCH_MAX`）
DIRECT_MAX = 200  # 直写一份计划最多几张
SKIPPED_SHOWN = 50  # 回给 CC 的跳过清单最多列几张（其余只给张数）

# 跳过的原因（给模型，不经 _()）
SKIP_GROUP = "moved_to_not_entered"       # 人在卡片上移到了「不入账」
SKIP_ENTERED = "already_entered"          # 同一张票已写进账本（代录预检 written）
SKIP_UNKNOWN = "last_write_result_unknown"  # 上次写入结果不明：先让人核对已入账 / 没入账
SKIP_UNDECIDED = "undecided_items"        # 还有没定的项（ask_user 问完、pearnly_precheck_answer 存下后再建计划）
SKIP_WAITING = "waiting_for_computer"     # 等电脑读账套
SKIP_NOT_FOUND = "not_found"              # 不是本人能写的单 / 找不到

PLAN_NOTE = ("Before writing, say back to the user in one sentence: the company, the account set(s), how many documents "
             "will be written, and which are skipped with the reason for each (skipped). Then ask whether to go ahead and "
             "wait for the user's reply. Only after the user answers in a new message call pearnly_write_start with this "
             "plan. If the user changes something (e.g. drop one), make a new plan and say it back again. Never start on "
             "your own, never say anything was written before pearnly_write_start reports it.")
NO_COMPANY_NOTE = ("This conversation is not filed under a company, so nothing can be written. Ask the user with ask_user "
                   "which company to enter it into, file this conversation under it (pearnly_file_conversation), then "
                   "make the plan again.")
NOTHING_NOTE = ("Nothing can be written now: tell the user in plain words why (skipped, with each reason), without "
                "offering to start.")
NOT_CONFIRMED_NOTE = ("The user has not answered since you said the plan back. Say it back (if you have not) and wait for "
                      "the user's reply; do not call pearnly_write_start again before the user sends a new message.")
STOPPED_NOTE = ("The user stopped after this plan was made. Do not write. If they want to go on, make a new plan and say it "
                "back again.")
CHANGED_NOTE = ("These documents changed after the plan was said back (changed), so they were not written this time: tell "
                "the user, make a new plan for them and say it back again.")
DIRECT_START_NOTE = ("Started: push exactly these documents (documents) now with the push tool, at most 5 per call, in this "
                     "order; report what was written only from the push results. Stop at once if a push says stopped.")
OPERATOR_START_NOTE = "Handed to the computer that enters them; progress shows in this conversation."


def _precheck():
    """写账前置判定（共用层）：(契约, 产品接法)。用到时才导入（会话读写导入本模块，读事件时不碰判定）。"""
    from pearnly_core.write_precheck import contracts, products

    return contracts, products


# ── 对话 ─────────────────────────────────────────────────────────────────────
def _session_id() -> str:
    return str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")


def _conversation(session_id: str | None = None) -> dict:
    """本人这段对话（工具在对话里执行时取当前对话）：``{name, session_id, operator_task, project, period}``。"""
    sid = str(session_id or _session_id())
    row = frappe.db.get_value(SESSION, {"session_id": sid, "user": frappe.session.user} if sid else {"name": ""},
                              ["name", "session_id", "operator_task", "project", "period"], as_dict=True)
    if not row:
        frappe.throw(_("Conversation not found"), frappe.DoesNotExistError)
    return row


def _turn(row) -> int:
    from pearnly_core.assistant import sessions

    return sessions.user_turn(row.session_id)


def _stopped_at(row):
    from pearnly_core.assistant import sessions

    return sessions.last_stop(row.session_id)


# ── 判定与版本 ───────────────────────────────────────────────────────────────
def _version(doc) -> str:
    """一张单此刻的写账判定摘要：票面、分组、答案、写入目标变了，判定结果就变（版本不同）。"""
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _judge(company: str, doc_ids: list) -> tuple[dict, dict]:
    """这批单的写账前置判定：(合并后的卡片, ``{doc_id: 版本}``)。产品只回本人能写的单。"""
    precheck, precheck_products = _precheck()
    documents = []
    for start in range(0, len(doc_ids), CHECK_MAX):
        documents += [doc for doc in precheck_products.check(company, doc_ids[start:start + CHECK_MAX]).get("documents") or []
                      if isinstance(doc, dict) and doc.get("doc_id")]
    versions = {str(doc["doc_id"]): _version(doc) for doc in documents}
    return precheck.merge(documents), versions


def _sorted_out(card: dict, doc_ids: list, versions: dict) -> tuple[list, list]:
    """(照写的单 ``[doc_id]``，跳过的单 ``[{doc_id, reason, detail?}]``)，顺序同 ``doc_ids``。"""
    precheck, _products = _precheck()
    writable = set(precheck.writable(card))
    reasons = {}
    for key, reason in ((precheck.UNKNOWN, SKIP_UNKNOWN), (precheck.WAITING, SKIP_WAITING)):
        for fact in card.get(key) or ():
            reasons[str(fact.get("doc_id"))] = {"reason": reason}
    for fact in card.get(precheck.NOT_WRITABLE) or ():
        reasons[str(fact.get("doc_id"))] = {"reason": str(fact.get("reason") or "not_writable")}
    for doc_id in precheck.held_back(card):
        reasons.setdefault(str(doc_id), {"reason": SKIP_UNDECIDED})
    write, skip = [], []
    for doc_id in doc_ids:
        if doc_id in writable:
            write.append(doc_id)
        else:
            skip.append({"doc_id": doc_id, **reasons.get(doc_id, {"reason": SKIP_NOT_FOUND if doc_id not in versions
                                                                   else SKIP_UNDECIDED})})
    return write, skip


# ── 计划 ─────────────────────────────────────────────────────────────────────
def _mode_and_company(row, tool_input) -> tuple[str, str, str, str]:
    """(模式, 公司, 代录任务号, 月份)。代录按任务的公司与月份；直写按本对话挂的公司（没挂公司照 P0 拒）。"""
    if row.operator_task:
        from pearnly_core.express_operator import tasks

        task = tasks.read(row.operator_task)
        return "operator", str(task["company"]), str(row.operator_task), str(task.get("period") or "")
    return "direct", str(row.project or ""), "", ""


def _targets(card: dict, write: list) -> list:
    return [{key: target.get(key) for key in ("connection_title", "account_set", "account_set_title", "count")
             if target.get(key) not in (None, "")} for target in card.get("targets") or ()] if write else []


def make(tool_input) -> dict:
    """建一份计划（同一对话里没开始的旧计划作废），回给 CC 的内容见 `PLAN_NOTE`。"""
    tool_input = tool_input or {}
    row = _conversation()
    mode, company, task, period = _mode_and_company(row, tool_input)
    if not company:
        return {"status": "no_company", "note": NO_COMPANY_NOTE}  # P0：直写对话没挂公司不写（先问录到哪家、挂上）
    _contracts, precheck_products = _precheck()
    if not precheck_products.allowed(frappe.session.user, company):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    skipped_first, again = [], []
    if mode == "operator":
        from pearnly_core.express_operator import api as operator_api

        again = [str(doc_id) for doc_id in tool_input.get("again") or () if doc_id]
        doc_ids, _groups, held = operator_api.waiting(company, period, again)
        again = [doc_id for doc_id in again if doc_id in doc_ids]
        skipped_first = held
    else:
        doc_ids = list(dict.fromkeys(str(doc_id) for doc_id in tool_input.get("documents") or () if doc_id))
        if not doc_ids or len(doc_ids) > DIRECT_MAX:
            frappe.throw(_("Invalid request"))
    card, versions = _judge(company, doc_ids) if doc_ids else ({}, {})
    write, skipped = _sorted_out(card, doc_ids, versions)
    skipped = skipped_first + skipped
    _replace_open(row.session_id)
    if not write:
        # 做不了：不存计划（没有可开始的，页面不出「录吧」），旧的没开始的已作废
        return {"plan": "", "company": company, "company_title": str(card.get("company_title") or ""), "account_sets": [],
                "count": 0, "documents": [], "skipped_count": len(skipped), "skipped": skipped[:SKIPPED_SHOWN],
                "status": "nothing_to_write", "note": NOTHING_NOTE}
    doc = frappe.get_doc({"doctype": DOCTYPE, "session_id": row.session_id, "user": frappe.session.user, "mode": mode,
                          "company": company, "operator_task": task, "user_turn": _turn(row), "status": AWAITING,
                          "documents": json.dumps([{"doc_id": doc_id, "version": versions[doc_id], **({"again": True} if doc_id in again else {})}
                                                  for doc_id in write]),
                          "skipped": json.dumps(skipped, ensure_ascii=False)}).insert(ignore_permissions=True)
    _announce(doc)
    out = {"plan": doc.name, "company": company, "company_title": str(card.get("company_title") or ""),
           "account_sets": _targets(card, write), "count": len(write), "documents": write,
           "skipped_count": len(skipped), "skipped": skipped[:SKIPPED_SHOWN]}
    # 工具结果的状态与 B 线「录吧」的约定（主会话 11-11）：有要写的回 ready，做不了回 nothing_to_write（带原因）；计划表里自己记 awaiting
    return {**out, "status": READY, "note": PLAN_NOTE}


def _replace_open(session_id: str) -> None:
    for name in frappe.get_all(DOCTYPE, filters={"session_id": session_id, "status": AWAITING}, pluck="name",
                               limit_page_length=50):
        frappe.db.set_value(DOCTYPE, name, "status", REPLACED, update_modified=True)
        _announce(frappe.get_doc(DOCTYPE, name))


def _own_plan(name, row) -> "frappe.model.document.Document":
    doc = frappe.get_doc(DOCTYPE, str(name or "")) if name and frappe.db.exists(DOCTYPE, str(name)) else None
    if not doc or doc.session_id != row.session_id or doc.user != frappe.session.user:
        frappe.throw(_("Invalid request"))
    return doc


def start(tool_input) -> dict:
    """按计划开始（见文件头三道保险）。"""
    tool_input = tool_input or {}
    row = _conversation()
    doc = _own_plan(tool_input.get("plan"), row)
    if doc.status != AWAITING:
        return {"status": "not_awaiting", "plan": doc.name, "plan_status": doc.status,
                "note": "This plan is not waiting to start. Make a new plan and say it back again."}
    if _turn(row) <= int(doc.user_turn or 0):
        return {"status": "not_confirmed", "plan": doc.name, "note": NOT_CONFIRMED_NOTE}
    stopped = _stopped_at(row)
    if stopped and doc.creation and frappe.utils.get_datetime(stopped) >= _to_utc(doc.creation):
        return {"status": "stopped", "plan": doc.name, "note": STOPPED_NOTE}
    planned = [item for item in json.loads(doc.documents or "[]") if isinstance(item, dict) and item.get("doc_id")]
    ids = [str(item["doc_id"]) for item in planned]
    card, versions = _judge(doc.company, ids) if ids else ({}, {})
    still, _skipped = _sorted_out(card, ids, versions)
    still = set(still)
    write = [item["doc_id"] for item in planned if item["doc_id"] in still and versions.get(item["doc_id"]) == item["version"]]
    changed = [doc_id for doc_id in ids if doc_id not in write]
    doc.db_set({"status": STARTED, "started_at": frappe.utils.now_datetime(),
                "started": json.dumps(write)}, update_modified=True)
    _announce(doc)
    out = {"status": "started", "plan": doc.name, "count": len(write), "documents": write}
    if changed:
        out.update(changed=changed, changed_note=CHANGED_NOTE)
    if not write:
        return {**out, "status": "nothing_to_write", "note": CHANGED_NOTE}
    if doc.mode == "operator":
        from pearnly_core.express_operator import api as operator_api

        again = [item["doc_id"] for item in planned if item.get("again") and item["doc_id"] in write]
        handed = operator_api.hand_in(doc.company, doc.operator_task, write, again=again)
        return {**out, "handed": handed, "note": OPERATOR_START_NOTE}
    return {**out, "note": DIRECT_START_NOTE}


def _to_utc(moment):
    """表里的时刻（站点时区的裸时间）→ UTC 的裸时间（同 `sessions.last_stop` 记法）。"""
    from datetime import timezone
    from zoneinfo import ZoneInfo

    value = frappe.utils.get_datetime(moment)
    return value.replace(tzinfo=ZoneInfo(frappe.utils.get_system_timezone())).astimezone(timezone.utc).replace(tzinfo=None)


# ── 直写推送工具的门 ─────────────────────────────────────────────────────────
def permit(doc_ids) -> tuple[list, dict]:
    """直写推送工具推之前问：(这次可以推的, ``{doc_id: 原因}``)。只有在 CC 对话里执行时才管（网页待处理、定时任务不经这里）。
    可以推 = 在本对话最近一份已开始的计划里，且开始之后没停过。"""
    sid = _session_id()
    ids = [str(doc_id) for doc_id in doc_ids or () if doc_id]
    if not sid:
        return ids, {}
    row = _conversation(sid)
    found = frappe.get_all(DOCTYPE, filters={"session_id": sid, "status": STARTED}, fields=["name", "started", "started_at"],
                           order_by="started_at desc", limit_page_length=1)
    if not found:
        return [], {doc_id: "not_started" for doc_id in ids}
    plan = found[0]
    stopped = _stopped_at(row)
    if stopped and plan.started_at and frappe.utils.get_datetime(stopped) >= _to_utc(plan.started_at):
        return [], {doc_id: "stopped" for doc_id in ids}
    allowed = set(json.loads(plan.started or "[]"))
    return [doc_id for doc_id in ids if doc_id in allowed], {doc_id: "not_in_plan" for doc_id in ids if doc_id not in allowed}


PERMIT_NOTES = {
    "not_started": ("Nothing was pushed: writing starts only from a plan the user agreed to. Call pearnly_write_plan, say "
                    "it back in one sentence and wait for the user's reply, then pearnly_write_start."),
    "not_in_plan": ("Not pushed: this document is not in the plan the user agreed to. Make a new plan that includes it "
                    "and say it back again."),
    "stopped": "Not pushed: the user stopped. Tell the user how many were written before the stop and which were not.",
}


# ── 事件 ─────────────────────────────────────────────────────────────────────
def event(doc) -> dict:
    """一份计划 → 本站事件（编号带状态：同一份计划状态变了是新的一条，页面按编号去重）。"""
    count = len(json.loads(doc.documents or "[]"))
    return {"id": f"pplan_{doc.name}_{doc.status}", "type": TYPE, "plan": doc.name, "status": doc.status, "count": count,
            "processed_at": _iso(doc.modified)}


def _iso(moment) -> str:
    return _to_utc(moment).isoformat(timespec="milliseconds") + "Z" if moment else ""


def _announce(doc) -> None:
    from pearnly_core.assistant import contracts

    try:
        frappe.publish_realtime(contracts.REALTIME_EVENT, {"session_id": doc.session_id, "event": event(doc)},
                                user=doc.user, after_commit=True)
    except Exception:
        pass


def feed(session_id: str, since=None) -> list:
    """这段对话的计划事件（整段时全部，增量时锚点之后改过的）；只给本人。读不出不挡取事件。"""
    try:
        filters = {"session_id": session_id, "user": frappe.session.user}
        if since is not None:
            filters["modified"] = [">=", since]
        rows = frappe.get_all(DOCTYPE, filters=filters, fields=["name", "status", "documents", "modified"],
                              order_by="modified asc", limit_page_length=50)
        return [event(row) for row in rows]
    except Exception:
        frappe.log_error(title="Assistant write plan feed failed")
        return []


def forget(session_id: str) -> None:
    """删对话时一起删。"""
    frappe.db.delete(DOCTYPE, {"session_id": session_id})


# ── 工具 ─────────────────────────────────────────────────────────────────────
def tool_plan(tool_input) -> str:
    return json.dumps(make(tool_input), ensure_ascii=False, default=str)


def tool_start(tool_input) -> str:
    return json.dumps(start(tool_input), ensure_ascii=False, default=str)
