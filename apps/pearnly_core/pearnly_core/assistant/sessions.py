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
一到界线，下一条消息就在新的一段里开始，并用系统消息带上前文要点（`contracts.carry_note`：本对话的工作清单、
本站留存的文件、最近的文字）。
历史、文件按全部分段合起来给浏览器，所以看起来仍是同一个对话。

对话提醒（`notices`）不在引擎会话里：补拉、整段重放时由这里按时间并进来；用户发话时，还没带过的提醒（本对话的、
没挂对话的、别的对话里近一天的）随批末的系统消息告诉智能体，发出成功后才把带上的那几条标成带过。
同一条系统消息每次都带上用户当地的日期时间（智能体才知道「今天」）。
"""
import json

import frappe
from frappe import _

from pearnly_core.assistant import config, context, contracts, engine, engines, notices, relay, toolrun, tools

SESSION = context.SESSION
LIST_LIMIT = 100  # 加星标的最多这么多条（都在第一页）
PAGE_SIZE = 30  # 其余的每页条数
SAME_TIME_SLACK = 10  # 翻页时多取几条，用来跳过与上一页最后一条同一时刻、已经给过的
# 短标题生成后推给会话主人（侧栏等着它，见对话页 session-list-adapter）
TITLE_EVENT = "pearnly_assistant_title"
TITLE_JOB_TIMEOUT = 60
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


def _summary(row, status="idle", unread=False) -> dict:
    return {"id": row.session_id, "title": row.title or "", "status": status, "starred": bool(row.get("starred")),
            "created_at": str(row.creation), "updated_at": str(row.modified),
            "archived_at": str(row.modified) if row.archived else None, "unread": bool(unread)}


def listing(archived=False, after=None) -> dict:
    """侧栏列表，按最后活动时间从新到旧一页一页给（同 Claude：先显示一页，滚到底再加载）。
    加星标的全部在第一页（侧栏顶上的「已加星标」一组要齐）；之后的页只翻其余的。有没看过的提醒的标 `unread`。"""
    user = _user()
    base = {"user": user, "engine_code": config.engine_code(), "archived": 1 if archived else 0}
    fields = ["name", "session_id", "title", "starred", "archived", "creation", "modified"]
    order = "modified desc, name desc"
    rows = []
    rest = {**base, "starred": 0}
    if after:
        modified, name = _guard(contracts.list_cursor, after)
        rest["modified"] = ["<=", modified]
        # 同一时刻的几条按记录名接着排：上一页已给到 name 为止
        found = frappe.get_all(SESSION, filters=rest, fields=fields, order_by=order, limit=PAGE_SIZE + 1 + SAME_TIME_SLACK)
        page = [row for row in found if not (str(row.modified) == modified and row.name >= name)][:PAGE_SIZE + 1]
    else:
        rows = frappe.get_all(SESSION, filters={**base, "starred": 1}, fields=fields, order_by=order, limit=LIST_LIMIT)
        page = frappe.get_all(SESSION, filters=rest, fields=fields, order_by=order, limit=PAGE_SIZE + 1)
    more = len(page) > PAGE_SIZE
    page = page[:PAGE_SIZE]
    cursor = f"{page[-1].modified}|{page[-1].name}" if more and page else None
    unread = notices.unread_sessions(user, [row.session_id for row in rows + page])
    return {"sessions": [_summary(row, unread=row.session_id in unread) for row in rows + page], "nextCursor": cursor}


def _metadata(user, product) -> dict:
    return {"pearnly_site": frappe.local.site, "pearnly_product": product, "pearnly_user": user}


def _route(remote: str) -> None:
    """告诉计费服务这段远端会话属于本站：生产的引擎回调只登记一个地址（入口 fatcc.com），由它按这份登记转给本站
    （`webhook`）。只为转发，登记不上不挡对话（页面开着照常执行；关着时这一段等页面再开）。"""
    from pearnly_core import billing_client

    if not billing_client.configured():
        return
    try:
        billing_client.assistant_route(remote)
    except Exception:
        frappe.log_error(title="Assistant route not recorded")


def create() -> dict:
    user, product = _user(), config.product()
    api = engine.adapter()
    sid, status = api.open(engine.prepared(api), _metadata(user, product))
    engine.tools_current(sid)
    _route(sid)
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
    notices.detach(row.session_id, _user())
    frappe.delete_doc(SESSION, row.name, ignore_permissions=True)
    from pearnly_core.assistant import files

    files.forget(row.name)


def _worked_on(row) -> tuple[str, str]:
    """接续时除了最近的文字还带上的：本对话的工作清单与留存的文件（都从本站读，不多调模型、不重传文件）。
    只是附带的：读不出来就不带，不挡这一轮。"""
    from pearnly_core.assistant import files  # files 导入 sessions，这里用时再导入

    try:
        return toolrun.work_list(row.session_id), contracts.files_note(files.kept(row))
    except Exception:
        frappe.log_error(title="Assistant carry list failed")
        return "", ""


def segment_for_turn(row, api) -> str:
    """新一轮（发消息、传附件）用哪段远端会话：输入量到了所选模型的计价界线就接续到新的一段。"""
    s = config.settings()
    limit = engines.context_limit(s.engine.code, s.model)
    remote = current(row)
    if not limit or int(row.get("context_tokens") or 0) < limit:
        engine.keep_tools_current(api, remote)
        return remote
    carry = contracts.carry_note(contracts.carry_over(api.events(remote)), *_worked_on(row))
    fresh, _status = api.open(engine.prepared(api), {**_metadata(_user(), config.product()),
                                                     "pearnly_conversation": row.session_id})
    engine.tools_current(fresh)
    _route(fresh)
    remote_ids = json.dumps(segments(row) + [fresh])
    frappe.db.set_value(SESSION, row.name, {"remote_ids": remote_ids, "context_tokens": 0, "pending_carry": carry})
    row.update({"remote_ids": remote_ids, "context_tokens": 0, "pending_carry": carry})
    return fresh


def events(session_id, after=None) -> dict:
    """整段重放（after 为空，各段合起来）或只取当前段 `after` 之后的新事件。取不到锚点时退回整段，`full` 告诉浏览器。
    本对话的提醒一并给：整段时按时间并入，增量时带上锚点时间之后的（对话页按编号去重）。"""
    row = owned(session_id, allow_archived=True)
    user = _user()
    api = engine.adapter()
    if after:
        anchor = _guard(contracts.event_id, after)
        try:
            found = contracts.since(api.events(current(row), newest_first=True), anchor)
        except Exception:
            # 引擎「列出事件」偶尔超时（工具长时间执行时更常见）：这一次当作没有新事件，页面下一轮再取，不报 500；
            # 本站的提醒照样带上
            frappe.log_error(title="Assistant events fetch failed")
            return {"events": notices.feed(row.session_id, user, recent=True), "full": False}
        if found is not None:
            newer = found[1:]
            _remember_context(row, newer)
            since = contracts.instant(found[0].get("processed_at"))
            return {"events": newer + notices.feed(row.session_id, user, since=since, recent=since is None),
                    "full": False}
    history = []
    for remote in segments(row):
        part = list(api.events(remote))
        history += part
    _remember_context(row, part)
    history = contracts.merge_by_time(history, notices.feed(row.session_id, user))
    return {"events": history, "full": True, "session": {"id": row.session_id, "title": row.title or "",
                                                         "archived_at": str(row.modified) if row.archived else None}}


def _remember_context(row, events_of_current) -> None:
    """记下输入量（接续判断用）。只是附带的：写不进（例如推送线程正在写同一行）不影响取事件。"""
    size = contracts.context_size(events_of_current)
    if size is not None and size != int(row.get("context_tokens") or 0):
        try:
            context.note(row.session_id, events_of_current)
            frappe.db.commit()  # GET 请求不自动提交
        except frappe.QueryDeadlockError:
            frappe.db.rollback()  # 推送线程正在写同一行（常见、无害）：这次不记，下一轮再记
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Assistant context note failed")


def send(session_id, text) -> None:
    row = owned(session_id)
    text = _guard(contracts.message_text, text)
    api = engine.adapter()
    remote = segment_for_turn(row, api)
    toolrun.remember_lang(row.session_id)
    relay.ensure(row.session_id, remote, frappe.session.user)
    shown = notices.uncarried(row.session_id, frappe.session.user)
    batch = [{"type": "user.message", "content": [{"type": "text", "text": text}]}]
    # 接续的前文、还没带过的提醒、当地日期时间合成一条系统消息：引擎规定每批最多一条、只能放在最后
    note = contracts.system_message(contracts.carry_text(row.get("pending_carry")), contracts.notices_note(shown),
                                    contracts.now_note(frappe.utils.now_datetime(), frappe.utils.get_system_timezone()))
    if note:
        batch.append(note)
    api.post(remote, batch)
    if row.get("pending_carry"):
        frappe.db.set_value(SESSION, row.name, "pending_carry", "")
    if shown:
        # 发出成功才算带过（只标这次真带上的几条）；发不出（抛错）下次再带
        notices.mark_carried(frappe.session.user, [item["name"] for item in shown])
    if not row.title:
        # 首条消息原文先当标题（只写本站，会话列表读本站表），不拖慢发送；长的再在后台概括成短标题（`retitle`）。
        title = contracts.title_from(text)
        frappe.db.set_value(SESSION, row.name, "title", title)
        source = contracts.summary_source(text)
        if source:
            frappe.enqueue("pearnly_core.assistant.sessions.retitle", queue="short", timeout=TITLE_JOB_TIMEOUT,
                           enqueue_after_commit=True, name=row.name, conversation=row.session_id, source=source,
                           title=title, user=frappe.session.user)
        else:
            _announce_title(row.session_id, title, frappe.session.user, after_commit=True)


def notices_seen(session_id) -> None:
    """对话页打开了这段对话：提醒都算看过（侧栏圆点消失）。"""
    row = owned(session_id, allow_archived=True)
    notices.mark_seen(row.session_id, _user())


def retitle(name, conversation, source, title, user) -> None:
    """后台任务：用引擎目录登记的便宜模型把首条消息概括成短标题（同各家对话应用的侧栏）。用户在这期间改过名就不动；
    概括不出来保留原文。无论成败都把当前标题推给会话主人，侧栏不用干等。"""
    short = ""
    try:
        short = contracts.short_title(engine.adapter().summarize(source, config.settings().engine.title_model))
    except Exception:
        frappe.log_error(title="Assistant title summary failed")
    current = frappe.db.get_value(SESSION, name, "title")
    if short and current == title:
        frappe.db.set_value(SESSION, name, "title", short, update_modified=False)
        frappe.db.commit()
        current = short
    if current is not None:
        _announce_title(conversation, current, user)


def _announce_title(conversation, title, user, after_commit=False) -> None:
    frappe.publish_realtime(TITLE_EVENT, {"session_id": conversation, "title": title}, user=user,
                            after_commit=after_commit)


def confirm(session_id, confirmations) -> int:
    """对话里点的「允许 / 拒绝」。我们自己拦下的工具调用（要授权的 Pearnly 工具，`toolrun.decide`）在本站处理；
    其余照旧交给引擎。"""
    row = owned(session_id)
    batch = _guard(contracts.confirmations, confirmations)
    remote = current(row)
    relay.ensure(row.session_id, remote, frappe.session.user)
    ours = set(frappe.get_all(toolrun.TOOL_RUN, filters={
        "session_id": row.session_id, "user": frappe.session.user, "status": "awaiting_approval",
        "tool_use_id": ["in", [item["tool_use_id"] for item in batch]]}, pluck="tool_use_id"))
    rest = [item for item in batch if item["tool_use_id"] not in ours]
    if rest:
        engine.adapter().post(remote, rest)
    for item in batch:
        if item["tool_use_id"] in ours:
            toolrun.decide(row.session_id, remote, item["tool_use_id"], item["result"] == "allow")
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
    status = toolrun.execute(row.session_id, remote, call)
    if status == "awaiting_approval":
        # 卡片在点之前就要说清改什么（2026-10-10 测试：说明在点了拒绝之后才出来）
        return {"status": status, **tools.approval_view(call.get("name"), call.get("input"))}
    return {"status": status}
