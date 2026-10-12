"""对话提醒（ADR-019）：产品发给某个用户的提醒，三条路送到。

- 对话里的卡片：提醒挂在用户自己的一段对话上（`session_id`）时，实时推给对话页，补拉与整段重放时按时间并进对话
  （`sessions.events`）；侧栏按未看过的提醒标圆点（`seen`），挂上时会话浮到侧栏顶上。
- 手机推送：提交后排一个后台任务（`push.send_notice`），按 `ttl` 留在推送服务里。
- 桌面通知：没挂对话的也推实时事件（`session_id` 为空），电脑上的窗口照样弹通知；小助手同步按记录取（`for_references`）。

提醒不写进引擎会话（写进去会开一轮、要花钱）：用户下次发话时，本对话的和没挂对话的、还没带过的提醒随系统消息
告诉智能体（`uncarried` / `mark_carried`）。去重键唯一，同一时段的提醒只记一条、只推一次；并发时撞键只退回到
本次插入之前（保存点），不动调用方同一事务里的其他提醒。产品只经这里发提醒，共用层不认识产品的提醒类型。
"""
import json
from datetime import timedelta

import frappe

from pearnly_core.assistant import config, context, contracts, relay

NOTICE = "Pearnly Assistant Notice"
SESSION = context.SESSION
FIELDS = ["name", "session_id", "kind", "title", "text", "creation"]
FEED_MAX = 500  # 整段重放最多并入这么多条（同 contracts.TAIL_MAX）
FEED_RECENT = 20  # 增量补拉、引擎出错时带回的最近几条
# 增量补拉按锚点事件的时间取提醒，前推这么多：引擎与站点的时钟可能差几分钟，重复的由对话页按编号去重
FEED_SLACK = timedelta(minutes=5)
REFERENCE_SCAN = 200  # 按记录取提醒时一次最多看这么多条
SAVEPOINT = "pearnly_assistant_notice"


def _tz() -> str:
    return frappe.utils.get_system_timezone()


def _conversation(user, conversation):
    """提醒能挂上的对话：属于该用户、未归档、当前引擎的。不是（删了、归档了、换了负责人或引擎、没配引擎）就不挂，
    这条提醒只走手机推送与电脑通知。"""
    if not conversation:
        return None
    try:
        sid = contracts.session_id(conversation)
    except contracts.ContractError:
        return None
    code = config.engine_code()
    if not code:
        return None
    return frappe.db.get_value(SESSION, {"session_id": sid, "user": user, "engine_code": code, "archived": 0},
                               ["name", "session_id"], as_dict=True)


def _result(name, session_id, created) -> dict:
    return {"name": name or "", "id": contracts.NOTICE_PREFIX + name if name else "", "session_id": session_id or "",
            "created": created}


def post(user, kind, text, dedup_key, *, title="", conversation="", reference_doctype="", reference_names=(),
         ttl=contracts.NOTICE_TTL) -> dict:
    """记一条提醒并送出。同一去重键已有就回 `created: False`，不再推送。调用方负责提交（推送都在提交后才发）。"""
    kind, title, text, key = contracts.notice_fields(kind, title, text, dedup_key)
    refs = contracts.notice_refs(reference_names)
    found = frappe.db.get_value(NOTICE, {"dedup_key": key}, ["name", "session_id"], as_dict=True)
    if found:
        return _result(found.name, found.session_id, False)
    session = _conversation(user, conversation)
    frappe.db.savepoint(SAVEPOINT)
    try:
        doc = frappe.get_doc({
            "doctype": NOTICE, "user": user, "session_id": session.session_id if session else "", "kind": kind,
            "title": title, "text": text, "dedup_key": key, "reference_doctype": str(reference_doctype or "")[:140],
            "reference_name": refs[0] if refs else "", "refs": json.dumps(refs, ensure_ascii=False),
            "ttl": contracts.notice_ttl(ttl), "seen": 0, "carried": 0}).insert(ignore_permissions=True)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        # 并发时另一边先记上了同一个键：只退回到本次插入之前；唯一冲突顺手 msgprint 的提示要清掉（同 toolrun）
        frappe.db.rollback(save_point=SAVEPOINT)
        frappe.clear_messages()
        found = frappe.db.get_value(NOTICE, {"dedup_key": key}, ["name", "session_id"], as_dict=True)
        return _result(found.name if found else "", found.session_id if found else "", False)
    if session:
        # 侧栏按最后活动时间排：挂上提醒的对话浮到顶上（不改会话的其他字段）
        frappe.db.set_value(SESSION, session.name, "modified", frappe.utils.now_datetime(), update_modified=False)
    frappe.publish_realtime(relay.EVENT, {"session_id": doc.session_id or "", "event": event(doc)}, user=user,
                            after_commit=True)
    frappe.enqueue("pearnly_core.assistant.push.send_notice", queue="short", enqueue_after_commit=True,
                   job_id=f"pearnly_notice_push:{doc.name}", deduplicate=True, notice=doc.name)
    return _result(doc.name, doc.session_id, True)


def exists(dedup_key) -> bool:
    return bool(frappe.db.exists(NOTICE, {"dedup_key": contracts.notice_key(dedup_key)}))


def event(row) -> dict:
    return contracts.notice_event(row.name, contracts.utc_iso(row.creation, _tz()), row.kind, row.title, row.text,
                                  row.session_id)


def feed(session_id, user, since=None, recent=False) -> list[dict]:
    """本对话的提醒事件，旧 → 新：`since`（带时区的锚点时间）给之后的（前推 `FEED_SLACK`），`recent` 给最近几条，
    都不给就是整段（最多 `FEED_MAX` 条）。"""
    if not session_id:
        return []
    filters = {"session_id": session_id, "user": user}
    if since is not None:
        filters["creation"] = [">=", contracts.local_time(since, _tz()) - FEED_SLACK]
    rows = frappe.get_all(NOTICE, filters=filters, fields=FIELDS, order_by="creation desc, name desc",
                          limit=FEED_RECENT if (recent or since is not None) else FEED_MAX)
    return [event(row) for row in reversed(rows)]


def unread_sessions(user, session_ids) -> set:
    """这些对话里哪些有还没看过的提醒（侧栏圆点）。"""
    ids = [sid for sid in session_ids or () if sid]
    if not ids:
        return set()
    return set(frappe.get_all(NOTICE, filters={"user": user, "seen": 0, "session_id": ["in", ids]},
                              pluck="session_id", distinct=True))


def mark_seen(session_id, user) -> None:
    frappe.db.set_value(NOTICE, {"session_id": session_id, "user": user, "seen": 0}, "seen", 1, update_modified=False)


def _uncarried_filters(session_id, user, until=None):
    filters = {"user": user, "carried": 0}
    if until is not None:
        filters["creation"] = ["<=", until]
    # 本对话的，加上本用户没挂对话的（负责人换了、来源对话删了时，提醒也要在他下次发话时带给智能体）
    return filters, [["session_id", "=", session_id], ["session_id", "is", "not set"]]


def uncarried(session_id, user) -> list[dict]:
    """还没随话带给智能体的提醒：最新 `NOTICES_CARRIED` 条，旧 → 新。"""
    filters, either = _uncarried_filters(session_id, user)
    rows = frappe.get_all(NOTICE, filters=filters, or_filters=either, fields=FIELDS,
                          order_by="creation desc, name desc", limit=contracts.NOTICES_CARRIED)
    return [{"name": row.name, "at": contracts.site_time(row.creation).strftime("%Y-%m-%d %H:%M"),
             "title": row.title or "", "text": row.text, "creation": row.creation} for row in reversed(rows)]


def mark_carried(session_id, user, until) -> None:
    """发话成功后：`until`（这次带上的最新一条的时间）及以前的都算带过，更早没带上的旧提醒也不再带；
    这之后才来的不受影响。"""
    filters, either = _uncarried_filters(session_id, user, until)
    names = frappe.get_all(NOTICE, filters=filters, or_filters=either, pluck="name")
    if names:
        frappe.db.set_value(NOTICE, {"name": ["in", names]}, "carried", 1, update_modified=False)


def detach(session_id, user) -> None:
    """删对话时：提醒不再挂这段对话，行留着（去重键要留着，否则同一时段会再推一次）。"""
    if session_id:
        frappe.db.set_value(NOTICE, {"session_id": session_id, "user": user}, "session_id", "", update_modified=False)


def _refs(row) -> list:
    try:
        refs = json.loads(row.refs or "[]")
    except ValueError:
        refs = []
    return [str(r) for r in refs if r] if isinstance(refs, list) else []


def for_references(doctype, names, since_epoch, limit=20) -> list[dict]:
    """某类产品记录（`doctype`）里这些记录（`names`）在 `since_epoch` 之后的提醒，旧 → 新，最多 `limit` 条。
    给小助手同步用（按记录、不按用户：读同一文件夹的成员都要弹）。"""
    wanted = contracts.notice_refs(names)
    cap = min(max(frappe.utils.cint(limit), 0), REFERENCE_SCAN)
    if not doctype or not wanted or not cap:
        return []
    tz = _tz()
    rows = frappe.get_all(
        NOTICE, filters={"reference_doctype": doctype, "creation": [">", contracts.local_time(since_epoch, tz)]},
        # 先在库里按名字粗筛（refs 是 JSON 列表），再逐条核对
        or_filters=[["refs", "like", f"%{json.dumps(name, ensure_ascii=False)}%"] for name in wanted],
        fields=FIELDS + ["refs"], order_by="creation asc, name asc", limit=REFERENCE_SCAN)
    found = []
    for row in rows:
        if not set(wanted) & set(_refs(row)):
            continue
        found.append({"id": contracts.NOTICE_PREFIX + row.name, "title": row.title or "", "text": row.text,
                      "created": contracts.epoch(row.creation, tz), "session": row.session_id or ""})
        if len(found) >= cap:
            break
    return found
