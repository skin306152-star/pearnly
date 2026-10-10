"""对话会话：归属判定与经引擎适配器的读写（ADR-019）。

- 会话归谁，以**本站数据库**的 `Pearnly Assistant Session` 为准（站点 = 租户）。浏览器给的编号先过
  `owned()`：属于当前用户、未归档、且是当前引擎建的，才去碰厂商服务。会话列表也只读本站表。
- 聊天记录本身存在引擎那边的会话里：网页、桌面、手机打开的是同一个会话，所以天然同步。
  后台换了引擎，旧引擎的会话不再列出（编号在新引擎里没有意义）。
- 新事件经实时通道推给会话主人（`relay`）：每次用户一侧发出前先确保本会话的转发已接上。
- 工具调用由服务端执行（`toolrun`）：推送线程看到就直接执行，页面提醒只作兜底；同一次调用靠
  `Pearnly Assistant Tool Run` 的唯一编号只执行一遍。

上下文接续（计价分档）：一个对话在引擎那边可以由几段会话接成（`remote_ids`，旧 → 新），浏览器始终只认第一段的
编号。所选模型有计价分档（`engines.context_limit`）时，记下最近一次模型请求的输入量（`context_tokens`）；
一到界线，下一条消息就在新的一段里开始，并用系统消息带上前文要点（`contracts.carry_over`）。
历史、文件按全部分段合起来给浏览器，所以看起来仍是同一个对话。
"""
import json

import frappe
from frappe import _

from pearnly_core.assistant import config, context, contracts, engine, engines, relay, toolrun

SESSION = context.SESSION
LIST_LIMIT = 100
FIELDS = ["name", "session_id", "title", "starred", "archived", "creation", "modified",
          "remote_ids", "context_tokens", "pending_carry"]


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except contracts.ContractError as err:
        frappe.throw(str(err))


def _user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return user


def owned(session_id, allow_archived=False) -> dict:
    sid = _guard(contracts.session_id, session_id)
    row = frappe.db.get_value(SESSION, {"session_id": sid, "user": _user(), "engine_code": config.engine_code()},
                              FIELDS, as_dict=True)
    if not row or (row.archived and not allow_archived):
        frappe.throw(_("Conversation not found"), frappe.DoesNotExistError)
    return row


def segments(row) -> list[str]:
    """组成这个对话的各段远端会话，旧 → 新；最后一段是当前在用的。"""
    return json.loads(row.get("remote_ids") or "[]") or [row.session_id]


def current(row) -> str:
    return segments(row)[-1]


def _summary(row, status="idle") -> dict:
    return {"id": row.session_id, "title": row.title or "", "status": status, "starred": bool(row.get("starred")),
            "created_at": str(row.creation), "updated_at": str(row.modified),
            "archived_at": str(row.modified) if row.archived else None}


def listing(archived=False) -> dict:
    rows = frappe.get_all(SESSION, filters={"user": _user(), "engine_code": config.engine_code(),
                                            "archived": 1 if archived else 0},
                          fields=["name", "session_id", "title", "starred", "archived", "creation", "modified"],
                          order_by="modified desc", limit=LIST_LIMIT)
    return {"sessions": [_summary(row) for row in rows], "nextCursor": None}


def _metadata(user, product) -> dict:
    return {"pearnly_site": frappe.local.site, "pearnly_product": product, "pearnly_user": user}


def create() -> dict:
    user, product = _user(), config.product()
    api = engine.adapter()
    sid, status = api.open(engine.prepared(api), _metadata(user, product))
    engine.tools_current(sid)
    doc = frappe.get_doc({"doctype": SESSION, "session_id": sid, "user": user, "product": product,
                          "engine_code": config.engine_code(), "title": ""}).insert(ignore_permissions=True)
    return _summary(doc, status)


def get(session_id) -> dict:
    row = owned(session_id, allow_archived=True)
    return _summary(row, engine.adapter().status(current(row)))


def rename(session_id, title) -> None:
    row = owned(session_id)
    title = contracts.title_from(title)
    frappe.db.set_value(SESSION, row.name, "title", title)
    engine.adapter().rename(current(row), title)


def star(session_id, starred) -> None:
    """加星标 / 取消：只是本站的整理标记，不动引擎那边的会话。"""
    row = owned(session_id)
    frappe.db.set_value(SESSION, row.name, "starred", 1 if starred else 0, update_modified=False)


def archive(session_id) -> None:
    row = owned(session_id)
    api = engine.adapter()
    for remote in segments(row):
        api.archive(remote)
    frappe.db.set_value(SESSION, row.name, "archived", 1)


def delete(session_id) -> None:
    row = owned(session_id, allow_archived=True)
    api = engine.adapter()
    for remote in segments(row):
        api.delete(remote)
    frappe.delete_doc(SESSION, row.name, ignore_permissions=True)
    from pearnly_core.assistant import files

    files.forget(row.name)


def segment_for_turn(row, api) -> str:
    """新一轮（发消息、传附件）用哪段远端会话：输入量到了所选模型的计价界线就接续到新的一段。"""
    s = config.settings()
    limit = engines.context_limit(s.engine.code, s.model)
    remote = current(row)
    if not limit or int(row.get("context_tokens") or 0) < limit:
        engine.keep_tools_current(api, remote)
        return remote
    carry = contracts.carry_over(api.events(remote))
    fresh, _status = api.open(engine.prepared(api), {**_metadata(_user(), config.product()),
                                                     "pearnly_conversation": row.session_id})
    engine.tools_current(fresh)
    remote_ids = json.dumps(segments(row) + [fresh])
    frappe.db.set_value(SESSION, row.name, {"remote_ids": remote_ids, "context_tokens": 0, "pending_carry": carry})
    row.update({"remote_ids": remote_ids, "context_tokens": 0, "pending_carry": carry})
    return fresh


def events(session_id, after=None) -> dict:
    """整段重放（after 为空，各段合起来）或只取当前段 `after` 之后的新事件。取不到锚点时退回整段，`full` 告诉浏览器。"""
    row = owned(session_id, allow_archived=True)
    api = engine.adapter()
    if after:
        anchor = _guard(contracts.event_id, after)
        newer = contracts.newer_than(api.events(current(row), newest_first=True), anchor)
        if newer is not None:
            _remember_context(row, newer)
            return {"events": newer, "full": False}
    history = []
    for remote in segments(row):
        part = list(api.events(remote))
        history += part
    _remember_context(row, part)
    return {"events": history, "full": True, "session": {"id": row.session_id, "title": row.title or "",
                                                         "archived_at": str(row.modified) if row.archived else None}}


def _remember_context(row, events_of_current) -> None:
    size = contracts.context_size(events_of_current)
    if size is not None and size != int(row.get("context_tokens") or 0):
        context.note(row.session_id, events_of_current)
        frappe.db.commit()  # GET 请求不自动提交


def send(session_id, text) -> None:
    row = owned(session_id)
    text = _guard(contracts.message_text, text)
    api = engine.adapter()
    remote = segment_for_turn(row, api)
    relay.ensure(row.session_id, remote, frappe.session.user)
    batch = [{"type": "user.message", "content": [{"type": "text", "text": text}]}]
    if row.get("pending_carry"):
        batch.append(contracts.carry_message(row.pending_carry))  # 系统消息只能放在一批事件的最后
    api.post(remote, batch)
    if row.get("pending_carry"):
        frappe.db.set_value(SESSION, row.name, "pending_carry", "")
    if not row.title:
        # 首条消息当标题：只写本站（会话列表读本站表）；不再多一次远端请求拖慢发送。
        frappe.db.set_value(SESSION, row.name, "title", contracts.title_from(text))


def confirm(session_id, confirmations) -> int:
    row = owned(session_id)
    batch = _guard(contracts.confirmations, confirmations)
    relay.ensure(row.session_id, current(row), frappe.session.user)
    engine.adapter().post(current(row), batch)
    return len(batch)


def interrupt(session_id) -> None:
    row = owned(session_id)
    engine.adapter().post(current(row), [{"type": "user.interrupt"}])


def run_tool(session_id, tool_use_id) -> dict:
    """兜底：页面提醒执行一次自定义工具调用（通常推送线程已先执行）。调用内容从引擎读回，不信浏览器给的工具名或参数。"""
    row = owned(session_id)
    use_id = _guard(contracts.event_id, tool_use_id)
    remote = current(row)
    recent = contracts.since(engine.adapter().events(remote, newest_first=True), use_id) or []
    call = recent[0] if recent and recent[0].get("type") == "agent.custom_tool_use" else None
    if not call:
        frappe.throw(_("Tool call not found"), frappe.DoesNotExistError)
    if use_id in contracts.answered_tool_uses(recent):
        return {"status": "answered"}
    relay.ensure(row.session_id, remote, frappe.session.user)
    return {"status": toolrun.execute(row.session_id, remote, call)}
