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

from pearnly_core.assistant import (ask, config, context, contracts, engine, engines, ledger, notices, prefs, projects,
                                    relay, sidebar, stopped, timing, titles, toolrun, tools, waits, write_plan)

SESSION = context.SESSION
TURN_LOG = "pearnly.assistant.turn"  # 建对话、发送的分段计时（`timing.Steps`，批 A ②）
LIST_LIMIT = 100  # 加星标的最多这么多条（都在第一页）
PAGE_SIZE = 30  # 其余的每页条数
SAME_TIME_SLACK = 10  # 翻页时多取几条，用来跳过与上一页最后一条同一时刻、已经给过的
GROUP_FIRST = 5  # 分组列表第一页每组给这么多条
GROUP_SCAN = 300  # 第一页一次最多读这么多条归在组里的对话，再按组各留 GROUP_FIRST 条
ORDERED_MAX = 300  # 第一页按组里手排的先后一并给的最多这么多条
FIELDS = ["name", "session_id", "title", "title_source", "starred", "archived", "creation", "modified",
          "remote_ids", "context_tokens", "pending_carry", "project", "period", "project_scope", "project_noted",
          "operator_task", "project_ledger"]
LIST_FIELDS = ["name", "session_id", "title", "starred", "archived", "creation", "modified", "project", "period",
               "project_scope", "operator_task", "project_ledger"]
UNFILED = ["is", "not set"]  # 「最近」：project 为空（新加的字段，旧行是 NULL）
FILED = ["is", "set"]
# 两种模式（统一对话框架 14.8）：代录会话 = operator_task 不为空；列表按 mode 筛（chat / operator，不带 = 全部）
CHAT, OPERATOR = "chat", "operator"
MODES = (CHAT, OPERATOR)
LOCAL_PREFIX = "op_"  # 代录会话在本站先建、远端会话第一次发话时才开（`_remote_ready`）：编号 op_<任务号>
SEGMENT_READERS = 4  # 整段重放时同时读几段远端会话的事件（`_segment_events`）
# 哪把密钥都读不到的那段（远端删了、过期，或建它的密钥没配进「以前用过的密钥」，`engine.reader`）：整段重放时在它的位置说这一句，
# 不让整段对话打不开（11-11 erpcowork 换密钥后旧对话 388 次 404）
HISTORY_UNAVAILABLE = "Earlier messages in this conversation are unavailable."


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except contracts.ContractError as err:
        # 上传拒收（不收的类型、空文件、太大）按请求语言回词条；CC 桌面版文件夹、直接调接口也看得到这句
        frappe.throw(contracts.refusal_text(err, _))


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
    """组成这个对话的各段远端会话，旧 → 新；最后一段是当前在用的。代录会话（`op_` 开头）还没开远端时是空列表。"""
    found = json.loads(row.get("remote_ids") or "[]")
    if found:
        return found
    return [] if local(row) else [row.session_id]


def local(row) -> bool:
    """本站先建、编号不是远端会话编号的代录会话（远端会话记在 remote_ids 里）。"""
    return bool(row.get("operator_task")) and str(row.get("session_id") or "").startswith(LOCAL_PREFIX)


def _remote_ready(row, api) -> None:
    """代录会话第一次要用引擎（发话、传附件）时才开远端会话，记进 remote_ids（浏览器始终只认本站的编号）。"""
    if segments(row):
        return
    fresh, _status = api.open(engine.prepared(api), {**_metadata(_user(), config.product()),
                                                     "pearnly_conversation": row.session_id})
    engine.tools_current(fresh, OPERATOR)
    engine.keep_tools_current(api, fresh, OPERATOR)  # 代录会话一开就只给代录的工具，不等第一条消息
    _route(fresh)
    remote_ids = json.dumps([fresh])
    frappe.db.set_value(SESSION, row.name, {"remote_ids": remote_ids, "context_tokens": 0}, update_modified=False)
    row.update({"remote_ids": remote_ids, "context_tokens": 0})


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
            "scope": row.get("project_scope") or "", "mode": "operator" if row.get("operator_task") else "chat",
            "task": row.get("operator_task") or "", "ledger": row.get("project_ledger") or ""}


def _tool_runs(row, ids) -> dict:
    """停着等的这几次调用在本站的执行状态 `{调用编号: awaiting_approval / running / …}`（本站库，不调引擎）。"""
    return {run.tool_use_id: run.status for run in _run_rows(row, ids)}


def _run_status(status, row, latest, as_of):
    """引擎状态 + 停着等的调用在本站的执行状态与挂着等的（`waits`）→ `contracts.run_state`。"""
    event = latest() if status == "idle" else None
    rows = _run_rows(row, contracts.parked_ids(event))
    return contracts.run_state(status, event, {run.tool_use_id: run.status for run in rows}, as_of,
                               waiting=[run.tool_use_id for run in rows if _parked_wait(run)])


def _parked_wait(run) -> bool:
    return run.status == "running" and bool(run.get("wait_until"))


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
                              fields=["tool_use_id", "status", "modified", "wait_until"])
    or_filters = [["modified", ">=", contracts.local_time(since, frappe.utils.get_system_timezone()) - notices.FEED_SLACK]]
    if ids:
        or_filters.append(["tool_use_id", "in", ids])
    rows = frappe.get_all(toolrun.TOOL_RUN, filters=filters, or_filters=or_filters,
                          fields=["tool_use_id", "status", "modified", "wait_until"])
    return list({run.tool_use_id: run for run in rows}.values())


def _with_tool_runs(events, row, since=None) -> list:
    """这页事件里出现过的调用在本站的执行记录，作为本站事件（`contracts.tool_run_event`）接在这页最后：对话页重开、补拉时
    按本站记录定下卡片（执行完、出错、拒绝、作废），不靠引擎那边的结果猜（第 11 轮 A 段）。增量补拉给 `since`：锚点之后
    改过的记录一并带上（`_run_rows`）。读不出来就不带，不挡取事件。"""
    try:
        runs = _run_rows(row, contracts.tool_use_ids(events), since)
        events = events + [contracts.tool_run_event(run.tool_use_id, run.status, _when(run.modified) if run.modified else None,
                                                    waiting=_parked_wait(run))
                           for run in runs if run.status in contracts.TOOL_RUN_STATES]
    except Exception:
        frappe.log_error(title="Assistant tool runs read failed")
    # 写入计划的状态（`write_plan`：页面据此出「录吧」），增量时带锚点之后改过的（同执行记录前推 FEED_SLACK）
    after = None if since is None else (contracts.local_time(since, frappe.utils.get_system_timezone()) - notices.FEED_SLACK)
    events = events + write_plan.feed(row.session_id, after)
    # 挂着等后台结论的调用此刻的进度（`waits`，识别读到第几页），现算并进来
    return events + waits.progress_events(row.session_id)


def run_state(row, api, latest, as_of=None, operator=None):
    """这段对话此刻的运行状态（`contracts.run_state`）：读一次引擎的会话状态；引擎空闲时由 `latest()` 给出最新一条会话
    状态事件（调用方已读过事件就从读过的里取，不再读一遍）分出「等待处理」，再看停着的调用在本站是等「允许」还是正在
    执行。读不到回 None：对话页这次不改，下一个时刻再读。
    代录会话（14.2）：引擎在回话 → running；否则按任务（`feed.run_state`：在排在录 → running，等着人点的卡片 →
    requires_action）。`operator` 是已投影的代录事件（调用方读过就给，不再投影一遍）。"""
    if row.get("operator_task"):
        return _operator_run(row, api, latest, as_of, operator)
    try:
        status = _remote_status(api, current(row))
        return _run_status(status, row, latest, as_of)
    except Exception:
        frappe.log_error(title="Assistant status read failed")
        return None


def _remote_status(api, remote) -> str:
    """这段远端会话此刻的状态，用读得到它的那把密钥读（`engine.reader`）；哪把都读不到（没配以前的密钥时当前密钥回「不存在」
    也算）按空闲。"""
    source = engine.reader(api, remote)
    if source is None:
        return "idle"
    try:
        return source.status(remote)
    except Exception as err:
        if engine.gone(source, err):
            return "idle"
        raise


def _remote_events(api, remote, newest_first=False):
    """这段远端会话的事件，用读得到它的那把密钥读；哪把都读不到（同上）就没有。"""
    source = engine.reader(api, remote)
    if source is None:
        return iter(())
    return _unless_gone(source, lambda: source.events(remote, newest_first=newest_first))


def _unless_gone(source, read):
    """边读边交；读的时候（调用或迭代时）引擎说「不存在」就到此为止。"""
    try:
        yield from read()
    except Exception as err:
        if not engine.gone(source, err):
            raise


def _operator_run(row, api, latest, as_of, operator):
    from pearnly_core.express_operator import feed

    if segments(row):
        try:
            status = _remote_status(api, current(row))
        except Exception:
            frappe.log_error(title="Assistant status read failed")
            status = None
        if status in ("running", "rescheduling"):
            return contracts.run_state(status, None, None, as_of)
        if status == "idle":
            engine_run = _run_status(status, row, latest, as_of)
            if engine_run and engine_run["status"] == "requires_action":
                return engine_run
    try:
        found = operator if operator is not None else feed.events(row)
    except Exception:
        frappe.log_error(title="Operator run state read failed")
        return None
    return feed.run_state(found, as_of)


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
        run = run_state(row, api, lambda row=row: contracts.latest_status(_remote_events(api, current(row), newest_first=True)))
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
    mode = base.get("operator_task")
    only = "" if mode is None else (" AND IFNULL(operator_task, '') != ''" if mode == FILED
                                    else " AND IFNULL(operator_task, '') = ''")
    found = frappe.db.sql(f"""SELECT project, COUNT(*) AS count FROM `tab{SESSION}`
        WHERE user = %(user)s AND engine_code = %(engine_code)s AND archived = %(archived)s AND starred = 0
        AND IFNULL(project, '') != ''{only} GROUP BY project""",
        {key: base[key] for key in ("user", "engine_code", "archived")}, as_dict=True)
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


def requested_mode(mode=None):
    """两种模式（14.8）：调用方没给就取这次请求的 `mode` 参数（`api.listing` / `api.create` 照旧原样转交，页面在同一个请求里
    多带一个 `mode`；不另开接口，也不改接口模块的签名）。"""
    if mode not in (None, ""):
        return mode
    form = getattr(frappe, "form_dict", None) or {}
    return form.get("mode") or None


def _mode_filter(mode) -> dict:
    if mode in (None, ""):
        return {}
    if mode not in MODES:
        frappe.throw(_("Invalid request"))
    return {"operator_task": FILED if mode == "operator" else UNFILED}


def listing(archived=False, after=None, check=None, grouped=False, group=None, mode=None) -> dict:
    """侧栏列表，按最后活动时间从新到旧一页一页给（同 Claude：先显示一页，滚到底再加载）。
    加星标的全部在第一页（侧栏顶上的「已置顶」一组要齐）；之后的页只翻其余的。有没看过的提醒的标 `unread`。
    `check`：侧栏以为还在回复的对话编号（逗号分隔）；给了就各读一次此刻的运行状态放进 `runs`（侧栏按它改，不按漏没漏事件猜）。
    不给就不读引擎：整页逐个读太贵。
    `grouped`（侧栏按公司显示，ADR-020）：第一页 = 全部加星标的 + 「最近」一页（`nextCursor`）+ 各组最新几条（`groups`：
    还有更多的组 → 游标）+ 组里手排过的（`ordered`：它们的编号）；带 `group`（"" = 「最近」，或组编号）就只翻那一组
    （`after` 是那一组的游标）。
    不带 `grouped` 时与以前一样（全部对话一起翻，不分组）。
    `mode`（统一对话框架 14.8）：`chat` 只列直写的对话，`operator` 只列代录会话，不带 = 全部；分组、翻页、星标、各组的数都按同一筛选。"""
    user = _user()
    mode = requested_mode(mode)
    base = {"user": user, "engine_code": config.engine_code(), "archived": 1 if archived else 0, **_mode_filter(mode)}
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


def create(project=None, period=None, scope=None, mode=None, ledger=None) -> dict:
    """新对话；`project` / `period` / `scope`：建在哪家、处理哪个月、哪个范围（产品不认这家或这个人不能用，照常建在
    「最近」；产品不收月份、范围的丢掉）。
    `mode="operator"`（统一对话框架 14.8）：同一事务里建代录任务并互相指上（`tasks.create`）；要有公司和月份，产品不允许这个人
    对这家代录就拒（不悄悄建成直写）。
    `ledger`：新建页选的本对话账套（`ledger_options` 列的那几本，测试机1 11-12）；要在这家这个月可选的里，不在按请求错误拒绝
    （同 `move`），没给照函数算（`ledger.initial`）。"""
    mode = requested_mode(mode)
    if mode not in (None, "", *MODES):
        frappe.throw(_("Invalid request"))
    if mode == "operator":
        return _create_operator(project, period, scope, ledger)
    steps = timing.Steps(TURN_LOG)
    user, product = _user(), config.product()
    project, period, scope = _guard(projects.placement, project, period, scope, user)
    chosen = _guard(ledger_initial, project, period, scope, user, ledger)
    api = engine.adapter()
    handle = engine.prepared(api)
    steps.done("prepare")
    sid, status = api.open(handle, _metadata(user, product))
    steps.done("open")
    engine.tools_current(sid)
    steps.done("tools")
    _route(sid)
    doc = frappe.get_doc({"doctype": SESSION, "session_id": sid, "user": user, "product": product,
                          "engine_code": config.engine_code(), "title": "", "project": project, "period": period,
                          "project_scope": scope, "project_ledger": chosen}).insert(ignore_permissions=True)
    sidebar.changed(user, "created", sid)
    steps.done("store")
    steps.log("conversation create")
    return _summary(doc, status)


def ledger_initial(project, period, scope, user, wanted=None) -> str:
    """新建时存哪本（`ledger.initial`）：没挂公司的不看 ``wanted``（给了也按请求错误拒绝，不悄悄丢掉）。"""
    if not project:
        if wanted is not None and str(wanted).strip():
            raise contracts.ContractError("invalid ledger")
        return ""
    return ledger.initial(project, period, scope, user, wanted)


def ledger_options(project, period=None, scope=None) -> dict:
    """新建页（还没有会话，直写、代录同一个）右栏账套行要的：这家这个月（该分店）的默认与可选（`ledger.offered`，只读不存）。
    本人不能用的公司按建对话的落点判据（`projects.placement`）拒绝。"""
    user = _user()
    target, period, scope = _guard(projects.placement, project, period, scope, user)
    if not target:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return ledger.offered(target, period, scope, user)


def _create_operator(project, period, scope, wanted=None) -> dict:
    from pearnly_core.express_operator import products, tasks

    user = _user()
    ref = str(project or "").strip()
    if not ref or not period:
        frappe.throw(_("Invalid request"))
    if not products.enabled() or not products.allowed(user, ref):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    chosen = ""
    if wanted is not None and str(wanted).strip():
        # 新建页选的本对话账套：先核再建任务（不在可选里不建）；没选的由 `open_operator` 照函数算
        _target, checked_period, checked_scope = _guard(projects.placement, ref, period, scope, user)
        chosen = _guard(ledger.initial, ref, checked_period or period, checked_scope, user, wanted)
    title = (projects._view([ref], user).get(ref) or {}).get("title") or ""
    made = tasks.create(ref, period, title, scope=scope or "", ledger=chosen)
    row = frappe.db.get_value(SESSION, {"session_id": made.get("session_id") or "", "user": user}, FIELDS, as_dict=True)
    if not row:
        frappe.throw(_("Invalid request"))
    return _summary(row, None)


def open_operator(task, scope="", ledger_id="") -> str:
    """代录任务的会话（`tasks.create` 与旧任务补建的补丁共用）：本站先建一行（编号 `op_<任务号>`，挂在任务的公司、月份上，
    标题先放任务名或公司名、记成占位标题，首条消息发出时同直写一样换成用户的话再概括，`titles.PLACEHOLDER`），远端会话第一次
    发话时才开。没配引擎的站点不建，回空串。``ledger_id``：新建页选的本对话账套（`_create_operator` 已核过）；没给照函数算。"""
    code = config.engine_code()
    if not code:
        return ""
    sid = LOCAL_PREFIX + task.name
    if frappe.db.exists(SESSION, {"session_id": sid}):
        return sid
    try:
        scope = contracts.project_scope(scope) if scope else ""
    except contracts.ContractError:
        scope = ""
    title = contracts.title_from(task.get("title") or task.get("company_title") or task.get("company") or "")
    frappe.get_doc({"doctype": SESSION, "session_id": sid, "user": task.owner, "product": config.product(),
                    "engine_code": code, "title": title, "title_source": titles.PLACEHOLDER,
                    "starred": 1 if task.get("starred") else 0,
                    "project": task.company, "period": task.period, "project_scope": scope,
                    "project_ledger": ledger_id or ledger.initial(task.company, task.period, scope, task.owner),
                    "operator_task": task.name, "remote_ids": "[]"}).insert(ignore_permissions=True)
    sidebar.changed(task.owner, "created", sid)
    return sid


def _task_mirror(row, values) -> None:
    """代录会话改名、星标：任务记录跟着记（只是镜像，读以会话为准）；不改任务的最后改动时间。"""
    if row.get("operator_task"):
        from pearnly_core.express_operator import tasks

        frappe.db.set_value(tasks.DOCTYPE, {"name": row.operator_task, "owner": _user()}, values, update_modified=False)


def get(session_id) -> dict:
    row = owned(session_id, allow_archived=True)
    if row.get("operator_task"):
        run = run_state(row, engine.adapter(), lambda: None)
        return _summary(row, run["status"] if run else None)
    return _summary(row, engine.adapter().status(current(row)))


BUSY = "This conversation could not be changed; please try again"


def _fresh_retried(function, *args):
    """结束这个请求的读快照（之前做完的写随之提交），再调 ``function``；撞上 1020 / 1213 整笔回滚、退避重来，最多 4 次
    （`transaction.conflicts.retried`）。用尽、或等锁超时，回一句用户看得懂的话，不是 500。"""
    from pearnly_core.transaction import conflicts

    frappe.db.commit()
    try:
        return conflicts.retried(function, *args)
    except (conflicts.Exhausted, conflicts.RollbackFailed, frappe.QueryTimeoutError):
        frappe.db.rollback()
        frappe.throw(_(BUSY))


def _lock(sid):
    """本人这段对话的最新一行，并锁住到这笔事务结束（等锁，不是 NOWAIT）；没有回 None。"""
    return frappe.db.get_value(SESSION, {"session_id": sid, "user": _user(), "engine_code": config.engine_code()},
                               FIELDS, as_dict=True, for_update=True)


def _write(session_id, write, allow_archived=False, missing_ok=False, nested=False):
    """改本站这一段对话（改名、星标、移月份、归档、删除的本站那一半）：先结束这个请求的读快照，在新事务里锁住这一行（等锁，
    起标题、推送线程的写都是毫秒级短事务）再写；撞上快照冲突（1020）或死锁（1213）整笔回滚、退避重来，最多 4 次
    （`transaction.conflicts.retried`）。用尽、或等锁超时，回一句用户看得懂的话，不是 500。

    为什么要这样（11-05 删对话 500）：站点库开着快照隔离，请求先读了这一行（`owned`），中间再调引擎（网络调用），最后才加锁写；
    这期间推送线程记最后活动时间、起标题任务写标题都会改这一行并提交，加锁时就报 1020，Frappe 的 `delete_doc` 把它转成
    「暂时不能删除」。``write(row)`` 拿到的是锁住的最新一行；这一行没了：``missing_ok`` 时什么都不做（删除重来时已删掉算成功），
    否则同 `owned` 报找不到。调用方在这之前做完的写会随这一步之前的提交落库（删除时远端已删，不能回滚掉）。``nested``：在别人
    的事务里被调（代录任务删除回头删它的对话），就地锁住、写，不提交、不重来（由外层整笔重来）。"""
    sid = _guard(contracts.session_id, session_id)

    def attempt():
        row = _lock(sid)
        if not row:
            if missing_ok:
                return None
            frappe.throw(_("Conversation not found"), frappe.DoesNotExistError)
        if row.archived and not allow_archived:
            frappe.throw(_("Conversation not found"), frappe.DoesNotExistError)
        return write(row)

    return attempt() if nested else _fresh_retried(attempt)


def rename(session_id, title) -> None:
    """手动改名：记成用户起的名字，之后的自动标题不再覆盖（`titles`）。先改引擎那边的名字，再在短事务里写本站（`_write`）。"""
    row = owned(session_id)
    title = contracts.title_from(title)
    if segments(row):
        engine.adapter().rename(current(row), title)

    def write(locked):
        # 改名不算活动：不改最后活动时间（侧栏「上次更新」按它排，`context.active`）
        frappe.db.set_value(SESSION, locked.name, {"title": title, "title_source": titles.BY_USER}, update_modified=False)
        _task_mirror(locked, {"title": title})

    _write(session_id, write)
    sidebar.changed(_user(), "titled", row.session_id, title=title)


def star(session_id, starred) -> None:
    """加星标 / 取消：只是本站的整理标记，不动引擎那边的会话。"""
    row = owned(session_id)
    value = 1 if starred else 0

    def write(locked):
        frappe.db.set_value(SESSION, locked.name, "starred", value, update_modified=False)
        _task_mirror(locked, {"starred": value})

    _write(session_id, write)
    sidebar.changed(_user(), "starred", row.session_id, starred=bool(starred))


def move(session_id, project, period=None, scope=None, ledger_id=None) -> dict:
    """改处理月份 / 范围：只动本人的会话。对话不能换公司（用户 10-17：侧栏拖动只在本组排先后，「移到」已删）——请求里的公司
    要与这段对话现在的一致（"" = 「最近」），不一致（含从「最近」挂进公司、移回「最近」）一律拒绝；产品不认这家时同样拒绝。
    挂在「最近」的对话没有月份、范围（一并清空）。新对话开在哪家走 `create`，删公司时对话回「最近」走 `projects.release`，
    都不经这里。只是整理，不改最后活动时间、不动置顶。
    本对话账套（`ledger.kept`，A 线 11-12）：``ledger_id`` 给了（右栏下拉）就存它（要在新的可选里）；没给时用户选过的那本在新
    月份 / 分店仍可选就保留，否则重算。产品没声明账套的不写这一列、不回不推 ``ledger``。"""
    row = owned(session_id)
    user = _user()
    wanted = str(project or "").strip()
    if wanted != (row.get("project") or ""):
        frappe.throw(_("A conversation cannot be moved to another company."))
    target, period, scope = _guard(projects.placement, wanted, period, scope, user)
    if wanted and not target:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    kept = _guard(ledger.kept, row, target, period, scope, ledger_id)

    def write(locked):
        if wanted != (locked.get("project") or ""):
            frappe.throw(_("A conversation cannot be moved to another company."))
        if locked.get("operator_task") and period != (locked.get("period") or ""):
            from pearnly_core.express_operator import tasks

            # 代录会话的月份就是任务的月份：交给任务改（只在准备中能改，交单后拒），会话跟着记
            tasks.update(locked.operator_task, period=period)
        frappe.db.set_value(SESSION, locked.name, {"project": target, "period": period, "project_scope": scope,
                                                   **({} if kept is None else {"project_ledger": kept})},
                            update_modified=False)

    _write(session_id, write)
    sidebar.project(user, "move", session=row.session_id, project=target, period=period, scope=scope,
                    **({} if kept is None else {"ledger": kept}))
    return {"id": row.session_id, "project": target, "period": period, "scope": scope,
            **({} if kept is None else {"ledger": kept})}


FILED_UNDER = "filed"
# 挂不上的原因（只给模型，`file_chosen`）
ALREADY_FILED = "already_filed"
NOT_CHOSEN = "not_chosen_by_user"
NOT_AVAILABLE = "company_not_available"


def file_chosen(session_id, project) -> dict:
    """CC 把「最近」里的这段对话挂到用户刚在提问卡上选的那家（用户 11-10「CC 严格只写对话里选中的公司」；主会话 11-10 定）。
    只给 CC 的工具用（`tools.file_conversation`），页面没有这个口子（页面照旧走 `move`，不能换公司）。只开这一个口子：
    - 只能从「最近」挂、只挂一次：已挂公司的（含代录会话）照旧不能换（10-17），回 ``already_filed``；
    - 只认用户自己在本对话最近一张提问卡上选的、或在「其他」里打的那家（`toolrun.last_answers` + `ask.names_one_of`，
      比编号、名称、税号），CC 自己挑的不算，回 ``not_chosen_by_user``；
    - 产品要认这家、本人能用（`projects.placement`），不然回 ``company_not_available``。
    挂上时不带月份、范围（之后照常 `move` 改）；不改最后活动时间。挂上后推给本人：先 upsert 这家（侧栏还没有这一组时有名字），
    再 move（侧栏把这一行移进去）。回 ``{"status": "filed", "project", "title"}`` 或 ``{"status": "refused", "reason"}``。"""
    row = owned(session_id)
    user = _user()
    if row.get("project") or row.get("operator_task"):
        return {"status": "refused", "reason": ALREADY_FILED, "project": row.get("project") or ""}
    target = _guard(projects.placement, str(project or "").strip(), None, None, user)[0]
    shown = projects.shown(target, user) if target else None
    if not target or not shown:
        return {"status": "refused", "reason": NOT_AVAILABLE}
    if not ask.names_one_of(toolrun.last_answers(row.session_id), (target, shown.get("title"), shown.get("subtitle"))):
        return {"status": "refused", "reason": NOT_CHOSEN}

    def write(locked):
        if locked.get("project") or locked.get("operator_task"):
            return False
        frappe.db.set_value(SESSION, locked.name, {"project": target, "period": "", "project_scope": ""},
                            update_modified=False)
        return True

    if not _write(session_id, write):
        return {"status": "refused", "reason": ALREADY_FILED}
    sidebar.project(user, "upsert", project={"id": target, "title": shown.get("title") or "",
                                             "subtitle": shown.get("subtitle") or ""})
    sidebar.project(user, "move", session=row.session_id, project=target, period="", scope="")
    return {"status": FILED_UNDER, "project": target, "title": shown.get("title") or ""}


def archive(session_id) -> None:
    """归档：先停下、归档引擎那边的会话（已经归档 / 不存在算成功，重来不失败），再在短事务里标本站（`_write`）。"""
    row = owned(session_id)
    api = engine.adapter()
    for remote in segments(row):
        api.archive(remote)
    _write(session_id, lambda locked: frappe.db.set_value(SESSION, locked.name, "archived", 1))
    sidebar.changed(_user(), "archived", row.session_id)


def delete(session_id) -> None:
    """删对话。代录会话（14.8）转给任务删除（`tasks.delete`：交出去还没收尾的不能删；它再回来删这段会话）。
    先删引擎那边（已经不存在算成功），再在短事务里删本站这一行与它的提醒、附件（`_write`）；本站这一行已经被删掉的（重来、
    两台设备同时删）算成功。代录会话整个任务删除同样在新事务里做、撞上冲突整笔重来。"""
    row = owned(session_id, allow_archived=True)
    nested = bool(row.get("operator_task")) \
        and getattr(frappe.local, "pearnly_operator_deleting", None) == row.operator_task
    if row.get("operator_task") and not nested:
        from pearnly_core.express_operator import tasks

        if frappe.db.exists(tasks.DOCTYPE, {"name": row.operator_task, "owner": _user()}):
            frappe.local.pearnly_operator_deleting = row.operator_task
            try:
                _fresh_retried(tasks.delete, row.operator_task)
            finally:
                frappe.local.pearnly_operator_deleting = None
            if not frappe.db.exists(SESSION, row.name):
                return
    api = engine.adapter()
    for remote in segments(row):
        api.delete(remote)

    def write(locked):
        from pearnly_core.assistant import files

        notices.detach(locked.session_id, _user())
        stopped.forget(locked.session_id)
        write_plan.forget(locked.session_id)
        frappe.delete_doc(SESSION, locked.name, ignore_permissions=True)
        files.forget(locked.name)
        return True

    if _write(session_id, write, allow_archived=True, missing_ok=True, nested=nested):
        sidebar.changed(_user(), "deleted", row.session_id)


MONTH_SCAN = 1000  # 「公司 · 月份」找对话时一次最多看这么多段（新到旧）


def pending_months(items) -> list:
    """产品待处理的 `pending_counts` 加的 `months`（Code 版 CC B3）：按「公司 · 月份」计数（`contracts.pending_months`），
    每项带 `conversation`：本人这家这个月最近更新、没归档、当前引擎、不属于实操任务的那段对话（侧栏亮「等你处理」的那一行），
    没有是空串（页面出占位行，点了才建）。只读；没登录、没配引擎时一律空串。"""
    months = contracts.pending_months(items)
    user, code = frappe.session.user, config.engine_code()
    if not months or not user or user == "Guest" or not code:
        return [{**item, "conversation": ""} for item in months]
    rows = frappe.get_all(SESSION, filters={"user": user, "engine_code": code, "archived": 0, "operator_task": UNFILED,
                                            "project": ["in", sorted({item["company"] for item in months})],
                                            "period": ["in", sorted({item["month"] for item in months})]},
                          fields=["session_id", "project", "period"], order_by="modified desc, name desc",
                          limit_page_length=MONTH_SCAN)
    latest = {}
    for row in rows:
        latest.setdefault((row.project, row.period), row.session_id)
    return [{**item, "conversation": latest.get((item["company"], item["month"]), "")} for item in months]


def _worked_on(row) -> tuple[str, str]:
    """接续时除了最近的文字还带上的：本对话的工作清单与留存的文件（都从本站读，不多调模型、不重传文件）。
    只是附带的：读不出来就不带，不挡这一轮。"""
    from pearnly_core.assistant import files  # files 导入 sessions，这里用时再导入

    try:
        return toolrun.work_list(row.session_id), contracts.files_note(files.kept(row))
    except Exception:
        frappe.log_error(title="Assistant carry list failed")
        return "", ""


# 同一对话同时来几个请求、都到了界线（CC 桌面版一次传 5–9 份附件）：按对话排队，只接续一次。11-08 晚 erpcowork 各自开了
# 新的一段、同时写对话记录，只有一份写成，其余 500「Record has changed since last read」，还多开了远端会话。
SEGMENT_LOCK_SECONDS = 120


def segment_for_turn(row, api) -> str:
    """新一轮（发消息、传附件）用哪段远端会话：输入量到了所选模型的计价界线就接续到新的一段。接续按对话排队：拿到锁先结束
    本事务（读到的是请求开始时的快照）、重读对话记录，排在前面的已经接续过就直接用新的那段；接续完先提交再放锁。"""
    _remote_ready(row, api)
    s = config.settings()
    limit = engines.context_limit(s.engine.code, s.model)
    mode = OPERATOR if row.get("operator_task") else CHAT
    # 当前段是以前的密钥建的（换了密钥、还没接着聊过）：当前密钥不能往里写，同到了界线一样接续到新的一段（11-12）
    if (not limit or int(row.get("context_tokens") or 0) < limit) and engine.reader(api, current(row)) is api:
        remote = current(row)
        engine.keep_tools_current(api, remote, mode)
        return remote
    with frappe.cache.lock(f"{frappe.local.site}:pearnly_assistant:segment:{row.name}", timeout=SEGMENT_LOCK_SECONDS,
                           blocking_timeout=SEGMENT_LOCK_SECONDS):
        frappe.db.commit()
        row.update(frappe.db.get_value(SESSION, row.name, ["remote_ids", "context_tokens", "pending_carry", "project_noted"],
                                       as_dict=True) or {})
        remote = current(row)
        source = engine.reader(api, remote)
        if (not limit or int(row.get("context_tokens") or 0) < limit) and source is api:
            engine.keep_tools_current(api, remote, mode)
            return remote
        # 前文用读得到这段的那把密钥读（以前的密钥只读）；哪把都读不到就不带前文。新的一段一律由当前密钥开
        said = contracts.carry_over(source.events(remote)) if source is not None else ""
        carry = contracts.carry_note(said, *_worked_on(row))
        fresh, _status = api.open(engine.prepared(api), {**_metadata(_user(), config.product()),
                                                         "pearnly_conversation": row.session_id})
        engine.tools_current(fresh, mode)
        engine.keep_tools_current(api, fresh, mode)
        _route(fresh)
        remote_ids = json.dumps(segments(row) + [fresh])
        # 新的一段不知道前面带过的公司说明：清空，下一批重带
        fresh_row = {"remote_ids": remote_ids, "context_tokens": 0, "pending_carry": carry, "project_noted": ""}
        frappe.db.set_value(SESSION, row.name, fresh_row)
        frappe.db.commit()
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
    if row.get("operator_task"):
        return _operator_events(row, user, api, after, status)
    if after:
        anchor = _guard(contracts.event_id, after)
        try:
            tap = contracts.StatusTap(_remote_events(api, current(row), newest_first=True))
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
                                                                          recent=since is None)
                    + stopped.feed(row.session_id, since=since),  # 被停掉的半段回复（批 A ④）
                    "full": False}
            return _with_run(page, row, api, tap.latest, found[-1].get("processed_at")) if status else page
    parts = _readable(_segment_events(api, segments(row)), segments(row), row)
    history = [event for part in parts for event in part]
    part = parts[-1]
    _remember_context(row, part)
    history = _with_tool_runs(contracts.merge_by_time(history, notices.feed(row.session_id, user)
                                                      + stopped.feed(row.session_id)), row)
    page = {"events": history, "full": True, "session": {"id": row.session_id, "title": row.title or "",
                                                         "archived_at": _when(row.modified) if row.archived else None}}
    if not status:
        return page
    return _with_run(page, row, api, lambda: contracts.latest_status(reversed(part)),
                     part[-1].get("processed_at") if part else None)


def _operator_events(row, user, api, after, status) -> dict:
    """代录会话（统一对话框架 14.2）：引擎事件（开过远端才有）照旧，代录事件源（`feed`）与提醒按时间并进来；增量时带锚点时间
    之后的。锚点找不到（含代录事件的编号）退回整段。"""
    from pearnly_core.express_operator import feed

    remotes = segments(row)
    if after and remotes:
        anchor = _guard(contracts.event_id, after)
        try:
            tap = contracts.StatusTap(_remote_events(api, current(row), newest_first=True))
            found = contracts.since(tap, anchor)
        except Exception:
            frappe.log_error(title="Assistant events fetch failed")
            return {"events": notices.feed(row.session_id, user, recent=True), "full": False, "partial": True}
        if found is not None:
            newer = found[1:]
            _remember_context(row, newer)
            since = contracts.instant(found[0].get("processed_at"))
            extra = notices.feed(row.session_id, user, since=since, recent=since is None)
            operator = feed.events(row, since) if since is not None else feed.events(row)
            page = {"events": _with_tool_runs(newer, row, since) + extra + operator, "full": False}
            if status:
                page = _with_run(page, row, api, tap.latest, found[-1].get("processed_at"),
                                 operator=feed.events(row) if since is not None else operator)
            return page
    parts = _readable(_segment_events(api, remotes), remotes, row) if remotes else []
    history = [event for part in parts for event in part]
    part = parts[-1] if parts else []
    if parts:
        _remember_context(row, part)
    # 资料与开始前卡片垫在引擎记下的第一句之后（本站记的发出时刻比引擎的早几百毫秒，整段重放按时间排时卡片会跑到第一句前面）
    said = next((event.get("processed_at") for event in history if event.get("type") == "user.message"), None)
    operator = feed.events(row, said=said)
    history = _with_tool_runs(contracts.merge_by_time(history, notices.feed(row.session_id, user) + operator), row)
    page = {"events": history, "full": True, "session": {"id": row.session_id, "title": row.title or "",
                                                         "archived_at": _when(row.modified) if row.archived else None}}
    if not status:
        return page
    as_of = history[-1].get("processed_at") if history else None
    return _with_run(page, row, api, lambda: contracts.latest_status(reversed(part)), as_of, operator=operator)


def _segment_events(api, remotes) -> list[list]:
    """各段远端会话的全部事件（旧 → 新），与 `remotes` 同序。接续过的长对话由几段组成，各段互不依赖：同时读（每段里仍按页
    顺序读），整段重放的等待从各段相加变成最慢的那一段（2026-10-15 验收：2040 条事件的对话打开约 7 秒，都在这里）。
    结果与逐段读相同；有一段读不到照旧抛出那一段的错。只有一段时就在本线程读。读事件只走引擎适配器，不碰站点库。
    每段用读得到它的那把密钥读（`engine.reader`，在本线程先定好：它要读站点配置与缓存）；哪把都读不到的段给 None。"""
    pairs = [(engine.reader(api, remote), remote) for remote in remotes]

    def read(pair):
        source, remote = pair
        if source is None:
            return None
        try:
            return list(source.events(remote))
        except Exception as err:
            if engine.gone(source, err):  # 没配以前的密钥、当前密钥回「不存在」：同哪把都读不到
                return None
            raise

    if len(pairs) < 2:
        return [read(pair) for pair in pairs]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(len(pairs), SEGMENT_READERS),
                            thread_name_prefix="pearnly-assistant-history") as pool:
        return list(pool.map(read, pairs))


def _readable(parts, remotes, row) -> list[list]:
    """哪把密钥都读不到的段换成一句「之前的消息不可用」（`HISTORY_UNAVAILABLE`）：时间取它后面第一条读得到的事件，没有就取
    前面最后一条，都没有取对话的修改时间（站点时区 → UTC）。"""
    def stamp(part):
        return [event.get("processed_at") for event in part or () if event.get("processed_at")]

    out = []
    for index, (part, remote) in enumerate(zip(parts, remotes)):
        if part is not None:
            out.append(part)
            continue
        after = next((stamp(p)[0] for p in parts[index + 1:] if stamp(p)), None)
        before = next((stamp(p)[-1] for p in reversed(parts[:index]) if stamp(p)), None)
        at = after or before or contracts.utc_iso(row.get("modified") or frappe.utils.now_datetime(),
                                                  frappe.utils.get_system_timezone())
        out.append([{"type": "agent.message", "id": f"pearnly_gap_{remote}"[:120], "processed_at": at,
                     "content": [{"type": "text", "text": _(HISTORY_UNAVAILABLE)}]}])
    return out


def _with_run(page, row, api, latest, as_of, operator=None) -> dict:
    run = run_state(row, api, latest, as_of, operator)
    if run:
        page["run"] = run
        _follow(row, run)
    return page


def _follow(row, run) -> None:
    """读到运行状态的时刻（打开对话、回到前台、重连）顺带补两件事，都不多读引擎：
    - 这段对话在跑（例如在别的设备上发的）而本站没有推送线程（进程重启、过了时限）：接上，这台设备也实时跟上；
    - 已经停下、还挂着临时标题（推送线程没看到这一轮结束）：排概括（`titles`）。"""
    if not segments(row):
        return
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


def send(session_id, text) -> dict:
    steps = timing.Steps(TURN_LOG)
    row = owned(session_id)
    text = _guard(contracts.message_text, text)
    api = engine.adapter()
    remote = segment_for_turn(row, api)
    steps.done("segment")
    toolrun.remember_lang(row.session_id)
    relay.ensure(row.session_id, remote, frappe.session.user)
    steps.done("relay")
    shown = notices.uncarried(row.session_id, frappe.session.user)
    company, noted = projects.note(row, frappe.session.user)
    if row.get("operator_task"):
        from pearnly_core.express_operator import tasks as operator_tasks

        # 实操任务里的对话：每条都带上是哪个任务（工具按任务号查、改）
        company = "\n\n".join(p for p in (company, operator_tasks.thread_note(row.operator_task)) if p)
    # 有调用挂着等后台结论（`waits`，例如识别还没完）：引擎这时只收工具结果，先把它按「还在做」交回，再发这条消息
    waits.quietly("Assistant wait release failed", waits.release, row.session_id)
    # 提问卡、「允许 / 拒绝」卡挂着时用户没点卡片、直接打字发来（11-11 压测 S8a：这句发不出）：引擎停在等卡片结果时不收用户
    # 消息，卡片结果和消息也不能同一批发；先交结果再发消息，模型会在两者之间多说一轮（11-12 真模型实测：重复回答、先问
    # 一遍）。所以走已有的「先停这一轮、再说话」：结束引擎那一轮（不记成用户下令停止），卡片作废——没点「允许」的调用
    # 不执行（`toolrun.cancel_waiting`）——再发这条消息，随它告诉模型卡片没答、这句就是回答
    typed_instead = waits.quietly("Assistant card check failed", toolrun.waiting, row.session_id, default=False)
    if typed_instead:
        api.post(remote, [{"type": "user.interrupt"}])
        frappe.db.commit()  # 作废开头要结束读快照：先提交这次请求已有的写入，不被回滚掉
        waits.quietly("Assistant card cancel failed", toolrun.cancel_waiting, row.session_id)
    batch = [{"type": "user.message", "content": [{"type": "text", "text": text}]}]
    # 接续的前文、公司说明、还没带过的提醒、界面语言、当地日期时间合成一条系统消息：引擎规定每批最多一条、只能放在最后。
    # 界面语言每条都带（同标题一个来源，`toolrun.interface_language`）：CC 按它回复，不按消息里夹的外文专名猜；
    # 不写进智能体说明（说明一变远端就出新版本）。系统消息不进对话记录、标题素材和接续的前文
    # 回复语言跟用户这句话自己的文字（11-10 S6，服务端判、不靠模型猜），判不出才用界面语言；记成这一轮的语言给工具结果用
    spoken = contracts.message_language(text)
    toolrun.remember_turn_language(row.session_id, spoken)
    note = contracts.system_message(contracts.carry_text(row.get("pending_carry")), company,
                                    contracts.notices_note(shown), toolrun.TYPED_INSTEAD_NOTE if typed_instead else "",
                                    contracts.language_note(spoken or toolrun.interface_language(row.session_id)),
                                    contracts.now_note(frappe.utils.now_datetime(), frappe.utils.get_system_timezone()))
    if note:
        batch.append(note)
    steps.done("prepare")
    api.post(remote, batch)
    steps.done("post")
    if row.get("operator_task"):
        from pearnly_core.express_operator import tasks as operator_tasks
        from pearnly_core.express_operator import uploads as operator_uploads

        # 代录会话的第一句发出了：记下此刻，资料与开始前卡片这时才出、排在这句后面（``feed.project``）。记不上只留日志
        try:
            operator_tasks.message_sent(row.operator_task)
        except Exception:
            frappe.log_error(title="Operator message sent mark failed")

        # 代录会话：这条消息带着的、输入框里已交识别的资料这时才进对话（上传卡片排在这条消息后面，files._operator_take）。
        # 消息已发出：记不上（任务刚被删等）只留日志，不当发送失败
        try:
            operator_uploads.sent(row.operator_task, text)
        except Exception:
            frappe.log_error(title="Operator upload sent mark failed")
    # 发出成功才算这段对话有了动静：最后活动时间记成现在（侧栏「上次更新」按它排；CC 回复时由 relay 再记）
    context.active(row.name)
    turn = _count_turn(row.name)
    if row.get("pending_carry"):
        frappe.db.set_value(SESSION, row.name, "pending_carry", "")
    if noted is not None:
        # 发出成功才算带过（同提醒）
        frappe.db.set_value(SESSION, row.name, "project_noted", noted, update_modified=False)
    if shown:
        # 发出成功才算带过（只标这次真带上的几条）；发不出（抛错）下次再带
        notices.mark_carried(frappe.session.user, [item["name"] for item in shown])
    first = titles.provisional(row)
    if first:
        # 首条消息原文先当临时标题（只写本站，会话列表读本站表），不拖慢发送；第一轮结束后才概括（`titles`）。
        # 代录会话建时放的公司名同样换掉（直写、代录同一套：标题 = 用户第一句的简要）
        titles.start(row, text, frappe.session.user)
    elif titles.due(row):
        # 上一轮结束时推送线程没排上概括（进程重启、过了时限）：这时补排
        titles.request(row.name, after_commit=True)
    steps.done("after")
    steps.log("message send", first=int(bool(first)), operator=int(bool(row.get("operator_task"))))
    return {"user_turn": turn}


def _count_turn(name: str) -> int:
    """这段对话发出成功的用户消息序号（只增不减，A 线复核卡 11-10：确认工具只认卡片出现之后用户发的消息；本站不存用户消息）。
    原子加一（不先读再写），读回本事务里的新值。"""
    frappe.db.sql(f"update `tab{SESSION}` set user_turn = ifnull(user_turn, 0) + 1 where name = %s", name)
    return int(frappe.db.get_value(SESSION, name, "user_turn") or 0)


def user_turn(session_id) -> int:
    """本人这段对话到此刻发出了几条用户消息（`send` 每发出一条加一）。只读；归档的也能读。"""
    row = owned(session_id, allow_archived=True)
    return int(frappe.db.get_value(SESSION, row.name, "user_turn") or 0)


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
    # 代录卡片（`pop_` 开头）不经引擎、不经本站执行记录：卡片自己调代录接口，结论由代录事件源投影出来
    batch = [item for item in _guard(contracts.confirmations, confirmations)
             if not str(item["tool_use_id"]).startswith("pop_")]
    # 通用提问卡片（`ask_user`）的作答随确认项带来（``answers``），本站记、本站交回，不转给引擎
    answers = {contracts.event_id(item.get("tool_use_id")): item.get("answers") for item in confirmations
               if isinstance(item, dict) and item.get("answers") is not None}
    if not batch:
        return {"confirmed": 0, "runs": {}}
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
        try:
            status = toolrun.decide(row.session_id, remote, use_id, item["result"] == "allow", answers.get(use_id))
        except ask.AnswerError:
            frappe.throw(_("Invalid request"))
        # 没抢到（同一刻别处点了、或刚被作废）：回此刻的状态
        runs[use_id] = status if status != "not_waiting" else _tool_runs(row, [use_id]).get(use_id, runs[use_id])
    return {"confirmed": len(batch), "runs": runs}


def interrupt(session_id) -> None:
    """停止。代录会话（E13）：远端会话开过就照旧打断引擎（CC 在回话）；任务交出去还在排、在录时记一条任务控制 stop，执行的
    那台电脑的 CC 页面取走后经桥停下。"""
    from pearnly_core.transaction import conflicts

    row = owned(session_id)
    # 记下这一刻用户下令停了（停止键、说停就停都走这里；A 线写入计划逐段写时核它，变了就停）。这之前只有读：撞上冲突整笔重来
    conflicts.retried(_mark_stop, row.name)
    if row.get("operator_task"):
        _operator_stop(row)
        if not segments(row):
            return
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


def _utc_now():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _mark_stop(name: str) -> None:
    """这段对话最近一次停止的时刻（UTC，只增不减，原子写：不先读再写）。"""
    frappe.db.sql(f"update `tab{SESSION}` set last_stop_at = greatest(ifnull(last_stop_at, %(at)s), %(at)s) "
                  "where name = %(name)s", {"at": _utc_now(), "name": name})


def last_stop(session_id):
    """本人这段对话最近一次停止的时刻（UTC 的裸时间；从没停过回 None）。只读；归档的也能读。A 线写入计划建时记下，
    逐段写时核对，变了就停。"""
    row = owned(session_id, allow_archived=True)
    return frappe.db.get_value(SESSION, row.name, "last_stop_at") or None


def _operator_stop(row) -> None:
    from pearnly_core.express_operator import tasks

    found = frappe.db.get_value(tasks.DOCTYPE, {"name": row.operator_task, "owner": _user()}, "status")
    if found in ("queued", "running"):
        tasks.push_control(row.operator_task, tasks.STOP, [])


def run_tool(session_id, tool_use_id) -> dict:
    """兜底：页面提醒执行一次自定义工具调用（通常推送线程已先执行）。调用内容从引擎读回，不信浏览器给的工具名或参数。"""
    row = owned(session_id)
    use_id = _guard(contracts.event_id, tool_use_id)
    if not segments(row) or use_id.startswith("pop_"):
        frappe.throw(_("Action not found"), frappe.DoesNotExistError)
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
