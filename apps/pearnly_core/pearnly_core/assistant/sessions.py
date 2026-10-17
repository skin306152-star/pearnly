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

最后活动时间（会话表的 `modified`，列表的 `updated_at`，侧栏「上次更新」按它排）只在用户发出消息（这里）、CC 回复（`relay`；
转发线程不在时由读事件顺带补记）、挂上提醒（`notices`）时更新，见 `context.active`；改名、置顶、改月份 / 分店不改它。

侧栏标题见 `titles`（首条消息先当临时标题，第一轮结束后概括）；新建、改名、加星标、归档、删除都经 `sidebar`
推给会话主人，同一账号别的设备的侧栏马上并入。

对话归在哪家（ADR-020，Cowork 按公司主档）：记在 `project`（空 = 「最近」）、`period`（处理月份）、`project_scope`
（产品自定的范围，Cowork 是分店），能挂哪些由产品的 provider 判（`projects`）。`listing(grouped=True)` 第一页给全部
加星标的 + 「最近」一页 + 每组最新几条 + 组里手排过的（`_ordered`），之后按组翻（`group`）；不带 grouped 时与以前一样。
对话归在某家时，批末系统消息里带上这家的说明（`projects.note`，哈希记在 `project_noted`，说明变了、换月、开新一段才重带；移出时带一句
「不再属于」）。
"""
import json

import frappe
from frappe import _

from pearnly_core.assistant import (config, context, contracts, engine, engines, notices, prefs, projects, relay,
                                    sidebar, titles, toolrun, tools)

SESSION = context.SESSION
LIST_LIMIT = 100  # 加星标的最多这么多条（都在第一页）
PAGE_SIZE = 30  # 其余的每页条数
SAME_TIME_SLACK = 10  # 翻页时多取几条，用来跳过与上一页最后一条同一时刻、已经给过的
GROUP_FIRST = 5  # 分组列表第一页每组给这么多条
GROUP_SCAN = 300  # 第一页一次最多读这么多条归在组里的对话，再按组各留 GROUP_FIRST 条
ORDERED_MAX = 300  # 第一页按组里手排的先后一并给的最多这么多条
FIELDS = ["name", "session_id", "title", "title_source", "starred", "archived", "creation", "modified",
          "remote_ids", "context_tokens", "pending_carry", "project", "period", "project_scope", "project_noted"]
LIST_FIELDS = ["name", "session_id", "title", "starred", "archived", "creation", "modified", "project", "period",
               "project_scope"]
UNFILED = ["is", "not set"]  # 「最近」：project 为空（新加的字段，旧行是 NULL）
SEGMENT_READERS = 4  # 整段重放时同时读几段远端会话的事件（`_segment_events`）


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


def _when(value) -> str:
    """库里的时间（站点时区的裸时间）→ RFC3339 UTC，精确到秒。页面按浏览器时区解析：给裸时间时浏览器与站点时区不同，
    公司页对话的相对时间与先后都会错（审查第 4 轮）。"""
    return contracts.utc_iso(contracts.site_time(value).replace(microsecond=0), frappe.utils.get_system_timezone())


def _summary(row, status=None, unread=False) -> dict:
    """`status` 只放真读过的（建会话、取会话、侧栏核对过的）；没读就是 None，不替它说「空闲」。
    时间都是带时区的 UTC（`_when`）。"""
    return {"id": row.session_id, "title": row.title or "", "status": status, "starred": bool(row.get("starred")),
            "created_at": _when(row.creation), "updated_at": _when(row.modified),
            "archived_at": _when(row.modified) if row.archived else None, "unread": bool(unread),
            "project": row.get("project") or "", "period": row.get("period") or "",
            "scope": row.get("project_scope") or ""}


def _tool_runs(row, ids) -> dict:
    """停着等的这几次调用在本站的执行状态 `{调用编号: awaiting_approval / running / …}`（本站库，不调引擎）。"""
    return {run.tool_use_id: run.status for run in _run_rows(row, ids)}


def _run_rows(row, ids, since=None) -> list:
    """这几次调用在本站的执行记录（编号、状态、最后改动时间），一次查询；没有编号不查。
    `since`（带时区的锚点时间）：再带上锚点之后改过的（前推 `notices.FEED_SLACK`，换算同 `notices.feed`），同一次查询，
    按调用编号去重。增量补拉用：引擎收尾之后才执行完 / 出错 / 作废的记录，引擎那边再没有提到这次调用的新事件，只按这页的
    调用查就永远补不到（推送漏收、断网恢复的设备卡片会一直停在淡色「允许」；第 11 轮 A 段审查）。"""
    ids = list(ids or ())
    if not ids and since is None:
        return []
    filters = {"session_id": row.session_id, "user": _user()}
    if since is None:
        return frappe.get_all(toolrun.TOOL_RUN, filters={**filters, "tool_use_id": ["in", ids]},
                              fields=["tool_use_id", "status", "modified"])
    or_filters = [["modified", ">=", contracts.local_time(since, frappe.utils.get_system_timezone()) - notices.FEED_SLACK]]
    if ids:
        or_filters.append(["tool_use_id", "in", ids])
    rows = frappe.get_all(toolrun.TOOL_RUN, filters=filters, or_filters=or_filters,
                          fields=["tool_use_id", "status", "modified"])
    return list({run.tool_use_id: run for run in rows}.values())


def _with_tool_runs(events, row, since=None) -> list:
    """这页事件里出现过的调用在本站的执行记录，作为本站事件（`contracts.tool_run_event`）接在这页最后：对话页重开、补拉时
    按本站记录定下卡片（执行完、出错、拒绝、作废），不靠引擎那边的结果猜（第 11 轮 A 段）。增量补拉给 `since`：锚点之后
    改过的记录一并带上（`_run_rows`）。读不出来就不带，不挡取事件。"""
    try:
        runs = _run_rows(row, contracts.tool_use_ids(events), since)
        return events + [contracts.tool_run_event(run.tool_use_id, run.status, _when(run.modified) if run.modified else None)
                         for run in runs if run.status in contracts.TOOL_RUN_STATES]
    except Exception:
        frappe.log_error(title="Assistant tool runs read failed")
        return events


def run_state(row, api, latest, as_of=None):
    """这段对话此刻的运行状态（`contracts.run_state`）：读一次引擎的会话状态；引擎空闲时由 `latest()` 给出最新一条会话
    状态事件（调用方已读过事件就从读过的里取，不再读一遍）分出「等待处理」，再看停着的调用在本站是等「允许」还是正在
    执行。读不到回 None：对话页这次不改，下一个时刻再读。"""
    try:
        status = api.status(current(row))
        event = latest() if status == "idle" else None
    except Exception:
        frappe.log_error(title="Assistant status read failed")
        return None
    return contracts.run_state(status, event, _tool_runs(row, contracts.parked_ids(event)), as_of)


def _check(user, check) -> dict:
    """侧栏以为还在回复的那几个对话（浏览器给编号，最多 `contracts.CHECK_MAX` 个）各读一次此刻的运行状态；只认本人、
    当前引擎的对话。"""
    ids = contracts.session_ids(check)
    if not ids:
        return {}
    rows = frappe.get_all(SESSION, filters={"user": user, "engine_code": config.engine_code(), "session_id": ["in", ids]},
                          fields=FIELDS)
    api = engine.adapter()
    runs = {}
    for row in rows:
        run = run_state(row, api, lambda row=row: contracts.latest_status(api.events(current(row), newest_first=True)))
        if run:
            runs[row.session_id] = run
    return runs


def _page(filters, after) -> tuple[list, str | None]:
    """`filters` 下按最后活动从新到旧的一页（`after` 之后）与下一页的游标（没有更多是 None）。"""
    order = "modified desc, name desc"
    if after:
        modified, name = _guard(contracts.list_cursor, after)
        # 同一时刻的几条按记录名接着排：上一页已给到 name 为止
        found = frappe.get_all(SESSION, filters={**filters, "modified": ["<=", modified]}, fields=LIST_FIELDS,
                               order_by=order, limit=PAGE_SIZE + 1 + SAME_TIME_SLACK)
        page = [row for row in found if not (str(row.modified) == modified and row.name >= name)][:PAGE_SIZE + 1]
    else:
        page = frappe.get_all(SESSION, filters=filters, fields=LIST_FIELDS, order_by=order, limit=PAGE_SIZE + 1)
    more = len(page) > PAGE_SIZE
    page = page[:PAGE_SIZE]
    return page, (f"{page[-1].modified}|{page[-1].name}" if more and page else None)


def _starred(base) -> list:
    return frappe.get_all(SESSION, filters={**base, "starred": 1}, fields=LIST_FIELDS,
                          order_by="modified desc, name desc", limit=LIST_LIMIT)


def _filed_counts(base) -> dict:
    """各组没加星标的对话数（一条 GROUP BY），判断第一页之后这组还有没有。"""
    found = frappe.db.sql(f"""SELECT project, COUNT(*) AS count FROM `tab{SESSION}`
        WHERE user = %(user)s AND engine_code = %(engine_code)s AND archived = %(archived)s AND starred = 0
        AND IFNULL(project, '') != '' GROUP BY project""", base, as_dict=True)
    return {row.project: int(row.count) for row in found}


def _ordered(base) -> list:
    """组里手排过的对话（当前用户个人排列 order 里 `group:<公司编号>` 的编号，`prefs`；用户 10-17 组里可拖）：分组列表第一页一并
    给。第一页每组只有最新几条，排到组顶的旧对话原来要点「显示更多」翻到它那一页才回来，刷新、换设备后组顶就不对了（审查）。
    只给仍在那一组、没加星标、没归档的；读不到排列就不给。"""
    order = (prefs.read() or {}).get("order")
    wanted = {}
    for field, value in (order.items() if isinstance(order, dict) else ()):
        if not field.startswith(prefs.GROUP_FIELD) or not isinstance(value, dict) or not isinstance(value.get("keys"), list):
            continue
        for key in value["keys"]:
            if isinstance(key, str) and key and key not in wanted and len(wanted) < ORDERED_MAX:
                wanted[key] = field[len(prefs.GROUP_FIELD):]
    if not wanted:
        return []
    found = frappe.get_all(SESSION, filters={**base, "starred": 0, "session_id": ["in", list(wanted)]}, fields=LIST_FIELDS,
                           order_by="modified desc, name desc", limit=ORDERED_MAX)
    return [row for row in found if (row.project or "") == wanted.get(row.session_id)]


def _grouped_first(base) -> tuple[list, str | None, dict, list]:
    """分组列表第一页：「最近」（不在任何组）一页，加上各组最新 `GROUP_FIRST` 条（一次读至多 `GROUP_SCAN` 条再按组留），再加上
    组里手排过、还没给的（`_ordered`，回第四项：它们的编号，对话页核对「本该在第一页」时不按它们算各组翻到哪儿）。
    `groups` 只列还有更多的组：值是这组第一页最后一条（不算手排一并给的）的游标，一条都没读到的给 ""（从头翻）；之后翻到已给过的
    由对话页去重。"""
    recent, cursor = _page({**base, "starred": 0, "project": UNFILED}, None)
    filed = frappe.get_all(SESSION, filters={**base, "starred": 0, "project": ["is", "set"]}, fields=LIST_FIELDS,
                           order_by="modified desc, name desc", limit=GROUP_SCAN)
    first = contracts.keep_per_group(filed, lambda row: row.project, GROUP_FIRST)
    given = {row.session_id for row in recent + first}
    extra = [row for row in _ordered(base) if row.session_id not in given]
    taken, last = {}, {}
    for row in first:
        taken[row.project] = taken.get(row.project, 0) + 1
        last[row.project] = f"{row.modified}|{row.name}"
    for row in extra:
        if row.project:
            taken[row.project] = taken.get(row.project, 0) + 1
    groups = {project: last.get(project, "") for project, count in _filed_counts(base).items()
              if count > taken.get(project, 0)}
    return recent + first + extra, cursor, groups, [row.session_id for row in extra]


def listing(archived=False, after=None, check=None, grouped=False, group=None) -> dict:
    """侧栏列表，按最后活动时间从新到旧一页一页给（同 Claude：先显示一页，滚到底再加载）。
    加星标的全部在第一页（侧栏顶上的「已置顶」一组要齐）；之后的页只翻其余的。有没看过的提醒的标 `unread`。
    `check`：侧栏以为还在回复的对话编号（逗号分隔）；给了就各读一次此刻的运行状态放进 `runs`（侧栏按它改，不按漏没漏事件猜）。
    不给就不读引擎：整页逐个读太贵。
    `grouped`（侧栏按公司显示，ADR-020）：第一页 = 全部加星标的 + 「最近」一页（`nextCursor`）+ 各组最新几条（`groups`：
    还有更多的组 → 游标）+ 组里手排过的（`ordered`：它们的编号）；带 `group`（"" = 「最近」，或组编号）就只翻那一组
    （`after` 是那一组的游标）。
    不带 `grouped` 时与以前一样（全部对话一起翻，不分组）。"""
    user = _user()
    base = {"user": user, "engine_code": config.engine_code(), "archived": 1 if archived else 0}
    starred, groups, ordered = [], None, None
    if grouped and (group is not None or after):
        project = _guard(contracts.project_ref, group)
        page, cursor = _page({**base, "starred": 0, "project": project or UNFILED}, after)
    elif grouped:
        starred = _starred(base)
        page, cursor, groups, ordered = _grouped_first(base)
    else:
        if not after:
            starred = _starred(base)
        page, cursor = _page({**base, "starred": 0}, after)
    rows = starred + page
    unread = notices.unread_sessions(user, [row.session_id for row in rows])
    runs = _check(user, check) if check else None
    out = {"sessions": [_summary(row, status=((runs or {}).get(row.session_id) or {}).get("status"),
                                 unread=row.session_id in unread) for row in rows], "nextCursor": cursor}
    if groups is not None:
        out["groups"] = groups
    if ordered:
        out["ordered"] = ordered
    if runs is not None:
        out["runs"] = runs
    return out


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


def create(project=None, period=None, scope=None) -> dict:
    """新对话；`project` / `period` / `scope`：建在哪家、处理哪个月、哪个范围（产品不认这家或这个人不能用，照常建在
    「最近」；产品不收月份、范围的丢掉）。"""
    user, product = _user(), config.product()
    project, period, scope = _guard(projects.placement, project, period, scope, user)
    api = engine.adapter()
    sid, status = api.open(engine.prepared(api), _metadata(user, product))
    engine.tools_current(sid)
    _route(sid)
    doc = frappe.get_doc({"doctype": SESSION, "session_id": sid, "user": user, "product": product,
                          "engine_code": config.engine_code(), "title": "", "project": project, "period": period,
                          "project_scope": scope}).insert(ignore_permissions=True)
    sidebar.changed(user, "created", sid)
    return _summary(doc, status)


def get(session_id) -> dict:
    row = owned(session_id, allow_archived=True)
    return _summary(row, engine.adapter().status(current(row)))


def rename(session_id, title) -> None:
    """手动改名：记成用户起的名字，之后的自动标题不再覆盖（`titles`）。"""
    row = owned(session_id)
    title = contracts.title_from(title)
    # 改名不算活动：不改最后活动时间（侧栏「上次更新」按它排，`context.active`）
    frappe.db.set_value(SESSION, row.name, {"title": title, "title_source": titles.BY_USER}, update_modified=False)
    engine.adapter().rename(current(row), title)
    sidebar.changed(_user(), "titled", row.session_id, title=title)


def star(session_id, starred) -> None:
    """加星标 / 取消：只是本站的整理标记，不动引擎那边的会话。"""
    row = owned(session_id)
    frappe.db.set_value(SESSION, row.name, "starred", 1 if starred else 0, update_modified=False)
    sidebar.changed(_user(), "starred", row.session_id, starred=bool(starred))


def move(session_id, project, period=None, scope=None) -> dict:
    """改处理月份 / 范围：只动本人的会话。对话不能换公司（用户 10-17：侧栏拖动只在本组排先后，「移到」已删）——请求里的公司
    要与这段对话现在的一致（"" = 「最近」），不一致（含从「最近」挂进公司、移回「最近」）一律拒绝；产品不认这家时同样拒绝。
    挂在「最近」的对话没有月份、范围（一并清空）。新对话开在哪家走 `create`，删公司时对话回「最近」走 `projects.release`，
    都不经这里。只是整理，不改最后活动时间、不动置顶。"""
    row = owned(session_id)
    user = _user()
    wanted = str(project or "").strip()
    if wanted != (row.get("project") or ""):
        frappe.throw(_("A conversation cannot be moved to another company."))
    target, period, scope = _guard(projects.placement, wanted, period, scope, user)
    if wanted and not target:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    frappe.db.set_value(SESSION, row.name, {"project": target, "period": period, "project_scope": scope},
                        update_modified=False)
    sidebar.project(user, "move", session=row.session_id, project=target, period=period, scope=scope)
    return {"id": row.session_id, "project": target, "period": period, "scope": scope}


def archive(session_id) -> None:
    row = owned(session_id)
    api = engine.adapter()
    for remote in segments(row):
        api.archive(remote)
    frappe.db.set_value(SESSION, row.name, "archived", 1)
    sidebar.changed(_user(), "archived", row.session_id)


def delete(session_id) -> None:
    row = owned(session_id, allow_archived=True)
    api = engine.adapter()
    for remote in segments(row):
        api.delete(remote)
    notices.detach(row.session_id, _user())
    frappe.delete_doc(SESSION, row.name, ignore_permissions=True)
    from pearnly_core.assistant import files

    files.forget(row.name)
    sidebar.changed(_user(), "deleted", row.session_id)


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
    # 新的一段不知道前面带过的公司说明：清空，下一批重带
    fresh_row = {"remote_ids": remote_ids, "context_tokens": 0, "pending_carry": carry, "project_noted": ""}
    frappe.db.set_value(SESSION, row.name, fresh_row)
    row.update(fresh_row)
    return fresh


def events(session_id, after=None, status=False) -> dict:
    """整段重放（after 为空，各段合起来）或只取当前段 `after` 之后的新事件。取不到锚点时退回整段，`full` 告诉浏览器。
    本对话的提醒一并给：整段时按时间并入，增量时带上锚点时间之后的（对话页按编号去重）。本站执行记录同样接在最后
    （`_with_tool_runs`）：整段带这段出现过的调用的，增量带这页出现过的加锚点之后改过的。
    `status`：对话页在整段重放、回到前台、重连这些时刻要一份此刻的运行状态（`run_state`，放进 `run`）。读完事件再读
    状态，所以状态不比这页事件旧；每次只读一次引擎状态，最新一条状态事件从这次读过的事件里取。"""
    row = owned(session_id, allow_archived=True)
    user = _user()
    api = engine.adapter()
    if after:
        anchor = _guard(contracts.event_id, after)
        try:
            tap = contracts.StatusTap(api.events(current(row), newest_first=True))
            found = contracts.since(tap, anchor)
        except Exception:
            # 引擎「列出事件」偶尔超时（工具长时间执行时更常见）：不报 500，本站的提醒照样带上；`partial` 告诉页面这次
            # 没读到引擎（不是「没有新事件」）：不拿这页收补拉缺口，压着的推送等下一次真读到引擎再交（第 11 轮合并审查）
            frappe.log_error(title="Assistant events fetch failed")
            return {"events": notices.feed(row.session_id, user, recent=True), "full": False, "partial": True}
        if found is not None:
            newer = found[1:]
            _remember_context(row, newer)
            since = contracts.instant(found[0].get("processed_at"))
            page = {"events": _with_tool_runs(newer, row, since) + notices.feed(row.session_id, user, since=since,
                                                                          recent=since is None),
                    "full": False}
            return _with_run(page, row, api, tap.latest, found[-1].get("processed_at")) if status else page
    parts = _segment_events(api, segments(row))
    history = [event for part in parts for event in part]
    part = parts[-1]
    _remember_context(row, part)
    history = _with_tool_runs(contracts.merge_by_time(history, notices.feed(row.session_id, user)), row)
    page = {"events": history, "full": True, "session": {"id": row.session_id, "title": row.title or "",
                                                         "archived_at": _when(row.modified) if row.archived else None}}
    if not status:
        return page
    return _with_run(page, row, api, lambda: contracts.latest_status(reversed(part)),
                     part[-1].get("processed_at") if part else None)


def _segment_events(api, remotes) -> list[list]:
    """各段远端会话的全部事件（旧 → 新），与 `remotes` 同序。接续过的长对话由几段组成，各段互不依赖：同时读（每段里仍按页
    顺序读），整段重放的等待从各段相加变成最慢的那一段（2026-10-15 验收：2040 条事件的对话打开约 7 秒，都在这里）。
    结果与逐段读相同；有一段读不到照旧抛出那一段的错。只有一段时就在本线程读。读事件只走引擎适配器，不碰站点库。"""
    if len(remotes) < 2:
        return [list(api.events(remote)) for remote in remotes]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(len(remotes), SEGMENT_READERS),
                            thread_name_prefix="pearnly-assistant-history") as pool:
        return list(pool.map(lambda remote: list(api.events(remote)), remotes))


def _with_run(page, row, api, latest, as_of) -> dict:
    run = run_state(row, api, latest, as_of)
    if run:
        page["run"] = run
        _follow(row, run)
    return page


def _follow(row, run) -> None:
    """读到运行状态的时刻（打开对话、回到前台、重连）顺带补两件事，都不多读引擎：
    - 这段对话在跑（例如在别的设备上发的）而本站没有推送线程（进程重启、过了时限）：接上，这台设备也实时跟上；
    - 已经停下、还挂着临时标题（推送线程没看到这一轮结束）：排概括（`titles`）。"""
    if run["status"] == "running" or run["executing"]:
        relay.ensure(row.session_id, current(row), _user())
    elif run["status"] in ("idle", "terminated") and titles.due(row):
        titles.request(row.name)


def _remember_context(row, events_of_current) -> None:
    """记下输入量（接续判断用），并补记转发线程没记上的最后活动时间（`context.caught_up`）。只是附带的：写不进
    （例如推送线程正在写同一行）不影响取事件。"""
    size = contracts.context_size(events_of_current)
    try:
        noted = size is not None and size != int(row.get("context_tokens") or 0) \
            and context.note(row.session_id, events_of_current)
        caught = context.caught_up(row, events_of_current)
        if noted or caught:
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
    company, noted = projects.note(row, frappe.session.user)
    batch = [{"type": "user.message", "content": [{"type": "text", "text": text}]}]
    # 接续的前文、公司说明、还没带过的提醒、当地日期时间合成一条系统消息：引擎规定每批最多一条、只能放在最后
    note = contracts.system_message(contracts.carry_text(row.get("pending_carry")), company,
                                    contracts.notices_note(shown),
                                    contracts.now_note(frappe.utils.now_datetime(), frappe.utils.get_system_timezone()))
    if note:
        batch.append(note)
    api.post(remote, batch)
    # 发出成功才算这段对话有了动静：最后活动时间记成现在（侧栏「上次更新」按它排；CC 回复时由 relay 再记）
    context.active(row.name)
    if row.get("pending_carry"):
        frappe.db.set_value(SESSION, row.name, "pending_carry", "")
    if noted is not None:
        # 发出成功才算带过（同提醒）
        frappe.db.set_value(SESSION, row.name, "project_noted", noted, update_modified=False)
    if shown:
        # 发出成功才算带过（只标这次真带上的几条）；发不出（抛错）下次再带
        notices.mark_carried(frappe.session.user, [item["name"] for item in shown])
    if not row.title:
        # 首条消息原文先当临时标题（只写本站，会话列表读本站表），不拖慢发送；第一轮结束后才概括（`titles`）
        titles.start(row, text, frappe.session.user)
    elif titles.due(row):
        # 上一轮结束时推送线程没排上概括（进程重启、过了时限）：这时补排
        titles.request(row.name, after_commit=True)


def notices_seen(session_id) -> None:
    """对话页打开了这段对话：提醒都算看过（侧栏圆点消失）。"""
    row = owned(session_id, allow_archived=True)
    notices.mark_seen(row.session_id, _user())


def retitle(name, conversation=None, source=None, title=None, user=None) -> None:
    """兼容入口（排队任务的点路径）：上线前已排队、按首条消息概括的旧任务。交给 `titles.summarize`：标题仍是
    排队时的原文、用户没改过名才概括。新代码不再排它。"""
    titles.summarize(name, legacy_title=title)


def confirm(session_id, confirmations) -> dict:
    """对话里点的「允许 / 拒绝」。本站拦下的工具调用（执行记录里有这次调用的，不论状态）在本站处理、一律不转给引擎：
    还在等的交 `toolrun.decide`；已执行、已拒绝、已作废的（例如别的设备上点了停止，断网的手机联网后才点「允许」）不再执行，
    把此刻的状态回给页面，页面照它当场定下卡片（第 11 轮 A4：以前转给引擎被拒，报 500）。其余照旧交给引擎。
    返回 `{"confirmed": 条数, "runs": {调用编号: 本站状态}}`。"""
    row = owned(session_id)
    batch = _guard(contracts.confirmations, confirmations)
    remote = current(row)
    relay.ensure(row.session_id, remote, frappe.session.user)
    runs = _tool_runs(row, [item["tool_use_id"] for item in batch])
    rest = [item for item in batch if item["tool_use_id"] not in runs]
    if rest:
        engine.adapter().post(remote, rest)
    for item in batch:
        use_id = item["tool_use_id"]
        if runs.get(use_id) != "awaiting_approval":
            continue
        status = toolrun.decide(row.session_id, remote, use_id, item["result"] == "allow")
        # 没抢到（同一刻别处点了、或刚被作废）：回此刻的状态
        runs[use_id] = status if status != "not_waiting" else _tool_runs(row, [use_id]).get(use_id, runs[use_id])
    return {"confirmed": len(batch), "runs": runs}


def interrupt(session_id) -> None:
    row = owned(session_id)
    engine.adapter().post(current(row), [{"type": "user.interrupt"}])
    # 停在等「允许」时点了停止：这一轮结束，那张卡片作废（推送线程可能已到时限不在了，这里当场标，`toolrun.cancel_waiting`）。
    # 打断已经交给引擎，这一轮就是停了：作废只是附带的，出错回滚、只记日志，不回 500（第 11 轮重测 A 段：点「允许」后立刻
    # 停止，作废撞上同一刻的抢占报 QueryDeadlockError，页面报「出了点问题」、停止钮消失，其实已停）。推送线程看到这一轮
    # 结束时还会再作废一次。
    try:
        toolrun.cancel_waiting(row.session_id)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Assistant cancel waiting failed")


def run_tool(session_id, tool_use_id) -> dict:
    """兜底：页面提醒执行一次自定义工具调用（通常推送线程已先执行）。调用内容从引擎读回，不信浏览器给的工具名或参数。"""
    row = owned(session_id)
    use_id = _guard(contracts.event_id, tool_use_id)
    remote = current(row)
    recent = contracts.since(engine.adapter().events(remote, newest_first=True), use_id) or []
    call = recent[0] if recent and recent[0].get("type") == "agent.custom_tool_use" else None
    if not call:
        frappe.throw(_("Action not found"), frappe.DoesNotExistError)
    if use_id in contracts.answered_tool_uses(recent):
        return {"status": "answered"}
    relay.ensure(row.session_id, remote, frappe.session.user)
    status = toolrun.execute(row.session_id, remote, call)
    if status == "awaiting_approval":
        # 卡片在点之前就要说清改什么（2026-10-10 测试：说明在点了拒绝之后才出来）。预览按这段对话现算（同工具执行时：
        # 产品工具缺省取本对话挂的公司等），所以带上对话编号；算完还原（2026-10-15 测试机：公司名空着）。
        before = getattr(frappe.local, "pearnly_assistant", None)
        frappe.local.pearnly_assistant = {**(before or {}), "session_id": row.session_id}
        try:
            view = tools.approval_view(call.get("name"), call.get("input"))
        finally:
            frappe.local.pearnly_assistant = before
        return {"status": status, **view}
    return {"status": status}
