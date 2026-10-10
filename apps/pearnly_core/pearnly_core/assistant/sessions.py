"""对话会话：归属判定与对 Managed Agents 的读写（ADR-019）。

- 会话归谁，以**本站数据库**的 `Pearnly Assistant Session` 为准（站点 = 租户）。浏览器给的编号先过
  `owned()`：属于当前用户、未归档，才去碰 Anthropic。会话列表也只读本站表，不去翻组织下全部会话。
- 聊天记录本身存在 Managed Agents 的会话里：网页、桌面、手机打开的是同一个会话，所以天然同步。
- 工具调用由服务端执行（`tools`），浏览器只负责「提醒有待办」；同一次调用靠 `Pearnly Assistant Tool Run`
  的唯一编号只执行一遍。
"""
import frappe
from frappe import _

from pearnly_core.assistant import client as anthropic_client
from pearnly_core.assistant import config, contracts, provision, tools

SESSION = "Pearnly Assistant Session"
TOOL_RUN = "Pearnly Assistant Tool Run"
LIST_LIMIT = 100


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
    row = frappe.db.get_value(SESSION, {"session_id": sid, "user": _user()},
                              ["name", "session_id", "title", "starred", "archived", "creation", "modified"], as_dict=True)
    if not row or (row.archived and not allow_archived):
        frappe.throw(_("Conversation not found"), frappe.DoesNotExistError)
    return row


def _summary(row, status="idle") -> dict:
    return {"id": row.session_id, "title": row.title or "", "status": status, "starred": bool(row.get("starred")),
            "created_at": str(row.creation), "updated_at": str(row.modified),
            "archived_at": str(row.modified) if row.archived else None}


def listing(archived=False) -> dict:
    rows = frappe.get_all(SESSION, filters={"user": _user(), "archived": 1 if archived else 0},
                          fields=["name", "session_id", "title", "starred", "archived", "creation", "modified"],
                          order_by="modified desc", limit=LIST_LIMIT)
    return {"sessions": [_summary(row) for row in rows], "nextCursor": None}


def create() -> dict:
    user, product = _user(), config.product()
    agent_id, environment_id = provision.ensure()
    session = anthropic_client.client().beta.sessions.create(
        agent=agent_id, environment_id=environment_id, title="",
        metadata={"pearnly_site": frappe.local.site, "pearnly_product": product, "pearnly_user": user})
    doc = frappe.get_doc({"doctype": SESSION, "session_id": session.id, "user": user, "product": product,
                          "title": ""}).insert(ignore_permissions=True)
    return _summary(doc, session.status)


def get(session_id) -> dict:
    row = owned(session_id, allow_archived=True)
    session = anthropic_client.client().beta.sessions.retrieve(row.session_id)
    return _summary(row, session.status)


def rename(session_id, title) -> None:
    row = owned(session_id)
    title = contracts.title_from(title)
    frappe.db.set_value(SESSION, row.name, "title", title)
    anthropic_client.client().beta.sessions.update(row.session_id, title=title)


def star(session_id, starred) -> None:
    """加星标 / 取消：只是本站的整理标记，不动 Anthropic 那边的会话。"""
    row = owned(session_id)
    frappe.db.set_value(SESSION, row.name, "starred", 1 if starred else 0, update_modified=False)


def archive(session_id) -> None:
    row = owned(session_id)
    api = anthropic_client.client()
    api.beta.sessions.events.send(row.session_id, events=[{"type": "user.interrupt"}])
    api.beta.sessions.archive(row.session_id)
    frappe.db.set_value(SESSION, row.name, "archived", 1)


def delete(session_id) -> None:
    row = owned(session_id, allow_archived=True)
    try:
        anthropic_client.client().beta.sessions.delete(row.session_id)
    except Exception as err:
        if not anthropic_client.gone(err):
            raise
    frappe.delete_doc(SESSION, row.name, ignore_permissions=True)


def events(session_id, after=None) -> dict:
    """整段重放（after 为空）或只取 `after` 之后的新事件。取不到锚点时退回整段，`full` 告诉浏览器。"""
    row = owned(session_id, allow_archived=True)
    api = anthropic_client.client()
    if after:
        anchor = _guard(contracts.event_id, after)
        newest_first = (anthropic_client.plain(e) for e in api.beta.sessions.events.list(row.session_id, order="desc"))
        newer = contracts.newer_than(newest_first, anchor)
        if newer is not None:
            return {"events": newer, "full": False}
    history = [anthropic_client.plain(e) for e in api.beta.sessions.events.list(row.session_id, order="asc")]
    return {"events": history, "full": True, "session": {"id": row.session_id, "title": row.title or "",
                                                         "archived_at": str(row.modified) if row.archived else None}}


def send(session_id, text) -> None:
    row = owned(session_id)
    text = _guard(contracts.message_text, text)
    anthropic_client.client().beta.sessions.events.send(
        row.session_id, events=[{"type": "user.message", "content": [{"type": "text", "text": text}]}])
    if not row.title:
        rename(row.session_id, text)


def confirm(session_id, confirmations) -> int:
    row = owned(session_id)
    batch = _guard(contracts.confirmations, confirmations)
    anthropic_client.client().beta.sessions.events.send(row.session_id, events=batch)
    return len(batch)


def interrupt(session_id) -> None:
    row = owned(session_id)
    anthropic_client.client().beta.sessions.events.send(row.session_id, events=[{"type": "user.interrupt"}])


def run_tool(session_id, tool_use_id) -> dict:
    """执行一次自定义工具调用。调用内容从 Anthropic 读回，不信浏览器传来的工具名或参数。"""
    row = owned(session_id)
    use_id = _guard(contracts.event_id, tool_use_id)
    api = anthropic_client.client()
    history = [anthropic_client.plain(e) for e in api.beta.sessions.events.list(row.session_id, order="asc")]
    call = next((e for e in history if e.get("id") == use_id and e.get("type") == "agent.custom_tool_use"), None)
    if not call:
        frappe.throw(_("Tool call not found"), frappe.DoesNotExistError)
    if use_id in contracts.answered_tool_uses(history):
        return {"status": "answered"}
    try:
        run = frappe.get_doc({"doctype": TOOL_RUN, "tool_use_id": use_id, "session_id": row.session_id,
                              "user": frappe.session.user, "tool": call.get("name") or "", "status": "running"})
        run.insert(ignore_permissions=True)
        frappe.db.commit()
    except frappe.DuplicateEntryError:
        frappe.db.rollback()
        return {"status": "running"}
    text, is_error = tools.run(call.get("name"), call.get("input"))
    api.beta.sessions.events.send(row.session_id, events=[contracts.tool_result(use_id, text, is_error)])
    frappe.db.set_value(TOOL_RUN, run.name, "status", "error" if is_error else "done")
    return {"status": "error" if is_error else "done"}
