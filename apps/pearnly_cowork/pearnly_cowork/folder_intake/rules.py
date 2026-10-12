"""自动录入规则：网页建、改、启停、立即运行、看运行记录（只经本域写，判据与新建录入同一套）。

规则就是「新建录入」弹窗那一组前置条件（Express 连接、年度账套、进项 / 销项、服务 / 存货、存货科目组），
外加文件夹、每月几号几点和「这一类以后都这样处理」的默认值。谁存或启用规则，谁就是负责人：自动推送以他的身份
做与网页「提交」完全相同的事，每次推送前照现有判据重核他的录入权限与连接使用授权。

2026-10-12 起新建的任务（`include_existing`）到点处理文件夹里本任务还没处理过的所有文件，含建任务前已有的；
老任务照旧只收启用之后放进来的（口径见 `contracts` 文件头）。`remind`：运行前一天提醒负责人放资料（提醒本身见
`notify`）；`cc_session`：最近一次在哪段 CC 对话里建、改或启停，只由 CC 工具以参数传入，网页报文传不进来。
"""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.access import scope
from pearnly_cowork.connections import access as connection_access
from pearnly_cowork.connections import preparation
from pearnly_cowork.connections import service as connections
from pearnly_cowork.folder_intake import agent, contracts, destinations, inbox
from pearnly_cowork.folder_intake.access import FILE, RULE, RUN, read, write
from pearnly_cowork.targets.identity import normalize_express_account_key

CONNECTION = "Cowork ERP Connection"
MAX_RULES = 50
# CC 一张「允许」卡片一次存几个任务：与共用层卡片行数上限（`pearnly_core.assistant.tools.PREVIEW_ROWS`）相同，
# 卡片上一个任务一行，多出的行会被截掉、用户看不到。
BATCH_MAX = 20


def _error(error):
    frappe.throw(_(error.code))


def _connection(name):
    """定时任务挂在能用的 Express / MR.ERP 连接上（与新建录入同一判据：录入权限 + 连接使用授权 + 连接启用）。"""
    if not isinstance(name, str) or not name or len(name) > 140:
        frappe.throw(_("cowork.folder.invalid"))
    doc = frappe.get_doc(CONNECTION, name)
    connection_access.require_connection(doc)
    # 读文件夹的是这条连接的 Windows 小助手（配对码身份）；去向的适配见 `destinations`。
    if not destinations.supported(doc.adapter) or int(doc.binding_generation or 0) != 0:
        frappe.throw(_("cowork.folder.unsupported_connection"))
    return doc


def _require():
    scope.require("intake")


def _catalog_item(connection, account_set_key):
    """年度账套必须在这条连接的账套目录里（与新建录入下拉同一份目录）。"""
    key = normalize_express_account_key(account_set_key)
    for group in connections.targets(connection).get("groups") or []:
        for item in group.get("items") or []:
            if normalize_express_account_key(item.get("value")) == key:
                return item.get("value"), " · ".join(filter(None, [group.get("label"), item.get("label")]))
    frappe.throw(_("cowork.folder.account_set_invalid"))


def _masters(doc, account_set_key):
    found = preparation.reported_masters(doc, normalize_express_account_key(account_set_key)) or {}
    accounts = found.get("reported_accounts")
    departments = found.get("reported_departments")
    return ({str(row.get("code")) for row in accounts if isinstance(row, dict) and row.get("code")} if accounts is not None else None,
            {str(row.get("code")) for row in departments if isinstance(row, dict) and row.get("code")} if departments is not None else None,
            found)


def account_label(connection, account_set_key):
    """账套的显示名（与新建录入下拉同一份目录，CC 允许卡片预览用）；不在目录里或没有权限回空串，不报错。"""
    try:
        _require()
        return _catalog_item(connection, account_set_key)[1]
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return ""


def master_names(connection, account_set_key):
    """账套主档里科目与部门的名称（小助手报过的，CC 允许卡片把默认科目 / 部门显示成「代码 · 名称」）：
    ``{"accounts": {代码: 名称}, "departments": {代码: 名称}}``；没读过、不是 Express 或没有权限就是空的。"""
    result = {"accounts": {}, "departments": {}}
    try:
        _require()
        doc = _connection(connection)
        if doc.adapter != "express" or not account_set_key:
            return result
        found = _masters(doc, account_set_key)[2]
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return result
    for kind, field in (("accounts", "reported_accounts"), ("departments", "reported_departments")):
        for row in found.get(field) or []:
            if isinstance(row, dict) and row.get("code"):
                result[kind][str(row["code"])] = str(row.get("name") or "")
    return result


def _readers(doc):
    """能给这个任务读文件夹的小助手（各自的连接）：Express 任务只能是它自己那台（写账套的也是它）；MR.ERP 任务可以是
    同一工作空间里任一台已配好的小助手 —— Express 的，或 MR.ERP 自己发配对码配的（2026-10-09：同时用两种 ERP 的
    不用再配第二个配对码，只用 MR.ERP 的才在 MR.ERP 上配一台）。都要当前用户能用这条连接。"""
    if doc.adapter == "express":
        return [doc]
    found = [doc]
    for row in connections.records(doc.workspace, doc.client or None, 0, 100) or []:
        if row.get("name") == doc.name or row.get("adapter") != "express":
            continue
        try:
            found.append(_connection(row["name"]))
        except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
            frappe.clear_messages()
    return found


def _reader_view(doc):
    state = agent.read(doc.name)
    report = {}
    try:
        report = json.loads(frappe.db.get_value(CONNECTION, doc.name, "report_json") or "{}")
    except (TypeError, ValueError):
        pass
    # 电脑名：小助手同步文件夹时报的（1.3.23 起）→ Express 心跳报的本机名 → 连接名。
    label = state.get("device") or (report.get("agent_device_name") if isinstance(report, dict) else "") or doc.label
    fresh = agent.fresh(state)
    roots = state.get("roots") or []
    if not fresh:
        # 各文件夹的文件数（`counts` / `partial`，1.4.4 起）：小助手好久没同步就是旧数，不显示（不知道，不用旧数冒充）。
        roots = [{key: value for key, value in row.items() if key not in ("counts", "partial")} for row in roots]
    return {"connection": doc.name, "adapter": doc.adapter, "label": str(label or doc.name)[:80],
            "fresh": fresh, "version": state.get("version") or "", "roots": roots}


def computers():
    """网页设置里只读的「连接」（2026-10-10）：本工作空间里连着电脑的 Express / MR.ERP 连接 —— 哪台电脑、在不在线、
    网页可读取的文件夹。只能看；连接、断开、加减文件夹都只在那台电脑的 CC 桌面端上做（文件夹权限是电脑上的安全边界）。
    与定时任务页同一判据：录入权限 + 这条连接的使用授权；没有连过电脑的连接不列。"""
    from pearnly_cowork.companion import credentials

    _require()
    result = []
    for row in connections.records(None, None, 0, 100) or []:
        if row.get("adapter") not in ("express", "mrerp") or not row.get("enabled"):
            continue
        try:
            doc = _connection(row["name"])
        except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
            frappe.clear_messages()
            continue
        view = _reader_view(doc)
        # Express 以心跳为准（3 分钟内报过到）；MR.ERP 的电脑只同步文件夹，以最近同步为准。
        online = bool(credentials.online(doc.name)) if doc.adapter == "express" else view["fresh"]
        if not (online or view["version"]):
            continue
        result.append({"adapter": doc.adapter, "device": view["label"], "online": online, "version": view["version"],
                       "folders": [str(item.get("label") or "") for item in view["roots"] if isinstance(item, dict)]})
    return result


def options(connection, account_set_key=None):
    """任务表单要的选项：能读文件夹的小助手与它们允许读取的根目录、文件夹；Express 另有账套的科目与部门、存货科目组。"""
    _require()
    doc = _connection(connection)
    readers = [_reader_view(reader) for reader in _readers(doc)]
    own = readers[0]
    result = {"agent": {"fresh": own["fresh"], "version": own["version"]}, "roots": own["roots"], "readers": readers,
              "accounts": None, "departments": None, "stock_group": None}
    if account_set_key and doc.adapter == "express":
        _value, _label = _catalog_item(doc.name, account_set_key)
        _a, _d, found = _masters(doc, account_set_key)
        if "reported_accounts" in found:
            result["accounts"] = [{"code": row["code"], "name": row.get("name") or ""}
                                  for row in found["reported_accounts"] if isinstance(row, dict) and row.get("code")][:2000]
        if "reported_departments" in found:
            result["departments"] = [{"code": row["code"], "name": row.get("name") or ""}
                                     for row in found["reported_departments"] if isinstance(row, dict) and row.get("code")]
        result["stock_group"] = connections.stock_acc_group(doc.name, account_set_key)
    return result


def _stock_ready(connection, account_set_key):
    """选存货而这份账套有两个以上存货科目组时，与新建录入一样要先选定一个（存回账套，见 `targets/stock_groups.py`）。"""
    view = connections.stock_acc_group(connection, account_set_key) or {}
    if view.get("known") and not view.get("chosen") and len(view.get("groups") or []) >= 2:
        frappe.throw(_("cowork.folder.stock_group_needed"))


def _flag(value):
    return 1 if str(value).strip().lower() in ("1", "true", "yes", "on") else 0


def save(values, *, cc_session=None):
    """建或改一条规则；改了去向或文件夹就升版本，小助手下一次同步换用新规则。

    新建的任务处理文件夹里已有的文件（`include_existing`）；老任务换了读文件夹的电脑、根目录或文件夹也改成这个口径
    （原来拍的底本来就对不上新文件夹）。`remind` 带了才改（网页表单、CC 不带就保留原值，新任务默认关）；提醒开着时，
    刚打开、换了负责人或改了运行日 / 时间，`remind_since` 记成现在（这之前的提醒时段不补发）。`cc_session` 只从参数取。"""
    _require()
    values = json.loads(values) if isinstance(values, str) else values
    if not isinstance(values, dict):
        frappe.throw(_("cowork.folder.invalid"))
    doc = _connection(values.get("connection"))
    # 与「新建录入」同一口径：Express 选过账类型（及默认处理），MR.ERP 选付款方式（2026-10-09 加 MR.ERP）。
    express = doc.adapter == "express"
    try:
        title = contracts.text(values.get("title"), maximum=140)
        direction = values.get("direction")
        posting_kind = values.get("posting_kind") if express else ""
        payment = "" if express else values.get("payment")
        if direction not in contracts.DIRECTIONS or (express and posting_kind not in contracts.POSTING_KINDS) or (
                not express and payment not in contracts.MRERP_PAYMENTS[direction]):
            raise contracts.ContractError("cowork.folder.invalid")
        root = contracts.root_id(values.get("root_id"))
        folder = contracts.folder(values.get("folder"))
        day, at = contracts.schedule(values.get("run_day"), values.get("run_time"))
        defaults = {}
        if express:
            accounts, departments, _found = _masters(doc, values.get("account_set_key"))
            defaults = contracts.defaults(values.get("defaults"), direction=direction, posting_kind=posting_kind,
                                          accounts=accounts, departments=departments)
    except contracts.ContractError as error:
        _error(error)
    account_set_key, label = _catalog_item(doc.name, values.get("account_set_key"))
    if posting_kind == "stock":
        _stock_ready(doc.name, account_set_key)
    reader = values.get("reader") or doc.name
    if reader not in {item.name for item in _readers(doc)}:
        frappe.throw(_("cowork.folder.root_invalid"))
    roots = {row["id"] for row in agent.read(reader).get("roots") or []}
    if root not in roots:
        frappe.throw(_("cowork.folder.root_invalid"))
    name = values.get("name") or None
    rule = read(RULE, name, for_update=True) if name else frappe.new_doc(RULE)
    if name and rule.connection != doc.name:
        # 换目标 ERP 等于另一个任务：原任务停用，另建一个。
        frappe.throw(_("cowork.folder.connection_fixed"))
    _no_overlap(reader, root, folder, rule.name if name else None)
    if not name and _count(reader) >= MAX_RULES:
        frappe.throw(_("cowork.folder.too_many"))
    before = _routing(rule) if name else None
    previous = _reminding(rule) if name else None
    place = (_reader(rule), rule.root_id, rule.folder or "") if name else None
    rule.update({"title": title, "workspace": doc.workspace, "client": doc.client or "", "connection": doc.name,
                 "reader": reader, "rule_owner": frappe.session.user, "account_set_key": account_set_key,
                 "account_label": label,
                 "direction": direction, "posting_kind": posting_kind, "payment": payment, "root_id": root, "folder": folder,
                 "run_day": day, "run_time": at, "defaults_json": json.dumps(defaults, sort_keys=True)})
    if not name:
        rule.enabled, rule.active_since = 1, now_datetime()
        # 结果文件夹按建规则这个人的网页语言起名，之后改语言也不变（不在盘上再长出第二个结果文件夹）
        rule.result_folder = contracts.result_folder_for(getattr(frappe.local, "lang", None))
    if not name or place != (reader, root, folder):
        rule.include_existing = 1
    if values.get("remind") is not None:
        rule.remind = _flag(values.get("remind"))
    elif not name:
        rule.remind = 0
    if int(rule.remind or 0) and _reminding(rule) != previous:
        rule.remind_since = now_datetime()
    ref = contracts.session_ref(cc_session)
    if ref:
        rule.cc_session = ref
    if before != _routing(rule) or not name:
        rule.rule_revision = int(rule.rule_revision or 0) + 1
    write(rule)
    return project(rule)


def _reminding(rule):
    """提醒这一组：开着与否、负责人、运行日、运行时间 —— 任一变了（且提醒开着）就从现在起算，之前的时段不补发。"""
    return [int(rule.get("remind") or 0), rule.get("rule_owner"), int(rule.get("run_day") or 0), rule.get("run_time")]


def save_many(items, *, cc_session=None):
    """一次建 / 改几个任务（CC 一张「允许」卡片，2026-10-12）：每个都走 `save` 同一套校验；任一个不通过就一个都不存，
    回每个不通过的序号（从 1 起）、名称、原因。失败项之后的照样校验，一次报全（少点几次「允许」）。

    同一事务里前面已存的任务对后面的「文件夹不能重叠」「一台电脑最多 50 个」可见，同一批里互相包含的文件夹也拦得住。
    `save` 写库之前没有任何副作用（不提交、不排队），回滚到保存点是干净的（只剩提交后才发的「定时任务页重拉」实时事件，
    回滚掉的任务最多让开着的页面多刷一次）；死锁等非业务异常不吞，整笔交给调用方回滚。"""
    _require()
    items = json.loads(items) if isinstance(items, str) else items
    if not isinstance(items, list) or not items or len(items) > BATCH_MAX or not all(isinstance(item, dict) for item in items):
        frappe.throw(_("cowork.folder.invalid"))
    names = [item.get("name") for item in items if item.get("name")]
    if len(names) != len(set(names)):
        frappe.throw(_("cowork.folder.invalid"))
    point = "cowork_folder_batch"
    frappe.db.savepoint(point)
    saved, failed = [], []
    for index, values in enumerate(items):
        item_point = f"{point}_{index}"
        frappe.db.savepoint(item_point)
        try:
            saved.append(save(values, cc_session=cc_session))
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback(save_point=item_point)
            frappe.clear_messages()
            failed.append({"task": index + 1, "title": str(values.get("title") or "")[:140], "reason": str(error)[:300]})
    if failed:
        frappe.db.rollback(save_point=point)
        return {"saved": [], "failed": failed}
    return {"saved": saved, "failed": []}


def _routing(rule):
    return [rule.get(key) for key in ("account_set_key", "direction", "posting_kind", "payment", "reader", "root_id", "folder",
                                      "run_day", "run_time", "defaults_json", "rule_owner")]


def _reader(row):
    return row.get("reader") or row.get("connection")


def _count(reader):
    return frappe.db.sql(f"""SELECT COUNT(*) FROM `tab{RULE}` WHERE IFNULL(NULLIF(reader, ''), connection) = %s""",
                         (reader,))[0][0]


def _no_overlap(reader, root, folder, current):
    """启用的任务，文件夹不能相同或互相包含：否则同一份文件会按两套口径各推一次（推进两个账套）。

    按「读文件夹的小助手」比（`reader`，MR.ERP 任务可以借用 Express 的小助手，2026-10-09）。同一台电脑：小助手授权时
    就不许两个授权文件夹相同或互相包含（不分成员，`companion/folder_store.py`），这里再管同一个授权文件夹之内。
    不同电脑：本机盘（C:\…）是各自的文件夹，不算重叠；网络共享（含映射盘换算回的网络路径）可能是同一个文件夹 ——
    同一工作空间里其他小助手的任务按网络路径比对（2026-10-08）。同一个共享在一台电脑映射成盘符、另一台用网络路径
    但小助手读不到映射的（非持久映射），认不出来。"""
    rows = frappe.db.sql(f"""SELECT name, title, folder FROM `tab{RULE}` WHERE enabled = 1 AND root_id = %s
        AND IFNULL(NULLIF(reader, ''), connection) = %s""", (root, reader), as_dict=True)
    for row in rows:
        if row.name != current and contracts.nested(row.folder or "", folder):
            frappe.throw(_("cowork.folder.overlap_with").format(row.title))
    mine = contracts.share_path(_share(reader, root), folder)
    if not mine:
        return
    workspace = frappe.db.get_value(CONNECTION, reader, "workspace")
    others = frappe.db.sql(f"""SELECT name, title, connection, reader, root_id, folder FROM `tab{RULE}`
        WHERE workspace = %s AND enabled = 1 AND IFNULL(NULLIF(reader, ''), connection) != %s""",
                           (workspace, reader), as_dict=True)
    for row in others:
        theirs = contracts.share_path(_share(_reader(row), row.root_id), row.folder or "")
        if row.name != current and theirs and contracts.path_nested(mine, theirs):
            frappe.throw(_("cowork.folder.overlap_with").format(row.title))


def _share(connection, root):
    return next((row.get("share") or "" for row in agent.read(connection).get("roots") or []
                 if row.get("id") == root), "")


def set_enabled(name, enabled=None, *, remind=None, cc_session=None):
    """启用 / 停用、开关提醒（``None`` = 不变；网页 `api.set_enabled(name, enabled)` 只管启停）。

    重新启用时「启用时刻」改成现在、负责人换成启用的人。新任务（`include_existing`）不重新拍底：启用时刻只当排程下限
    （启用当下不补跑已错过的时刻），到点照样处理停用期间放进来、还没处理过的文件；老任务照旧重新拍底，这之前放进
    文件夹的文件不处理（用户 2026-10-07 的口径只对老任务保留）。提醒开着时，刚打开或重新启用，`remind_since` 记成现在。"""
    _require()
    rule = read(RULE, name, for_update=True)
    _connection(rule.connection)
    changed = False
    previous = _reminding(rule)
    if enabled is not None:
        enabled = 1 if str(enabled) in ("1", "true", "True") else 0
        if enabled and not rule.enabled:
            _no_overlap(_reader(rule), rule.root_id, rule.folder or "", rule.name)
            rule.active_since = now_datetime()
            rule.rule_owner = frappe.session.user
            # 重新启用：提醒也从现在起算（停用期间的时段不补发）
            previous = None
        if enabled != int(rule.enabled or 0):
            rule.enabled = enabled
            rule.rule_revision = int(rule.rule_revision or 0) + 1
            changed = True
    if remind is not None and _flag(remind) != int(rule.remind or 0):
        rule.remind = _flag(remind)
        changed = True
    if int(rule.remind or 0) and _reminding(rule) != previous:
        rule.remind_since = now_datetime()
        changed = True
    ref = contracts.session_ref(cc_session)
    if ref and ref != rule.cc_session:
        rule.cc_session = ref
        changed = True
    if changed:
        write(rule)
    return project(rule)


def delete(names):
    """删除选中的规则（列表勾选后右上角「删除」，2026-10-08）：连同它的运行记录与文件进度一起删，小助手下一次同步
    就不再跑它；已经建出的录入记录留在录入工作台，照常可看、可推。没有这条连接使用权的规则不删，已不存在的跳过。"""
    _require()
    names = json.loads(names) if isinstance(names, str) else names
    if not isinstance(names, list) or not names or len(names) > MAX_RULES * 10 \
            or not all(isinstance(name, str) and 0 < len(name) <= 140 for name in names):
        frappe.throw(_("cowork.folder.invalid"))
    deleted, inline = 0, []
    for name in dict.fromkeys(names):
        if not frappe.db.exists(RULE, name):
            continue
        rule = read(RULE, name, for_update=True)
        _connection(rule.connection)
        inline += frappe.get_all(FILE, filters={"rule": rule.name, "route": "inline"}, pluck="name",
                                 ignore_permissions=True)
        frappe.db.delete(FILE, {"rule": rule.name})
        frappe.db.delete(RUN, {"rule": rule.name})
        frappe.db.delete(RULE, {"name": rule.name})
        deleted += 1
    # 网页直传的原件暂存（还没建原件的）提交后再清，回滚时不丢文件。
    if inline:
        frappe.db.after_commit.add(lambda: [inbox.drop(name) for name in inline])
    return {"deleted": deleted}


def run_now(name):
    """立即运行：只记一个请求号，小助手下一次同步时取走并开跑；网页请求里不建原件、不交识别。"""
    _require()
    rule = read(RULE, name, for_update=True)
    _connection(rule.connection)
    if not rule.enabled:
        frappe.throw(_("cowork.folder.rule_disabled"))
    rule.run_request = int(rule.run_request or 0) + 1
    rule.run_request_at = now_datetime()
    write(rule)
    return project(rule)


def listing():
    _require()
    rows, usable = [], []
    for connection in connections.records(scope.default_workspace(), None, 0, 100) or []:
        if not destinations.supported(connection.get("adapter")):
            continue
        try:
            doc = _connection(connection["name"])
        except (frappe.PermissionError, frappe.ValidationError):
            frappe.clear_messages()
            continue
        usable.append({"name": doc.name, "label": connection.get("label") or doc.name})
        for name in frappe.get_all(RULE, filters={"connection": doc.name}, pluck="name",
                                   order_by="creation asc", ignore_permissions=True):
            row = project(read(RULE, name))
            # 文件夹在不在、小助手在不在线，看的是读文件夹的那台小助手（`reader`）。
            state = agent.read(row["reader"])
            roots = {item["id"]: item for item in state.get("roots") or []}
            fresh, root = agent.fresh(state), roots.get(row["root_id"]) or {}
            # 待处理数（1.4.4 起小助手报）：到点会处理、还没处理过的文件。停用的任务小助手不算；不知道就是 None（不用 0 冒充）。
            files = (state.get("pending") or {}).get(name) if fresh and row["enabled"] else None
            row.update(connection_label=usable[-1]["label"], agent_fresh=fresh, root_label=root.get("label", ""),
                       # 小助手报这条规则的文件夹不在了（授权移除 / 改名 / 子文件夹删了）：列表显示「文件夹不可用」
                       folder_state=(state.get("health") or {}).get(name) if fresh else None,
                       waiting=files, waiting_partial=bool(root.get("partial")) if files is not None else False)
            rows.append(row)
    return {"rules": rows, "connections": usable}


def waiting(reader, root_id, folder):
    """文件夹计数（新任务的允许卡片与表单「文件夹里现有 N 份」）：``{"files": n, "partial": 数没数完}``；
    小助手好久没同步、版本旧没报计数、文件夹不在上报里（深于 3 层）或没有这台小助手的使用权时回 None。"""
    try:
        _require()
        _connection(reader)
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.clear_messages()
        return None
    state = agent.read(reader)
    if not agent.fresh(state):
        return None
    root = next((row for row in state.get("roots") or [] if row.get("id") == root_id), None)
    if not root or not isinstance(root.get("counts"), dict):
        return None
    try:
        wanted = contracts.folder(folder).casefold()
    except contracts.ContractError:
        return None
    name = next((item for item in ["", *(root.get("folders") or [])] if item.casefold() == wanted), None)
    if name is None:
        return None
    return {"files": int(root["counts"].get(name) or 0), "partial": bool(root.get("partial"))}


def project(rule):
    last = frappe.get_all(RUN, filters={"rule": rule.name}, fields=["started_at", "found", "new_files", "trigger"],
                          order_by="started_at desc", limit=1, ignore_permissions=True)
    return {"name": rule.name, "title": rule.title, "connection": rule.connection, "enabled": int(rule.enabled or 0),
            "adapter": frappe.db.get_value(CONNECTION, rule.connection, "adapter") or "",
            "reader": _reader(rule),
            "account_set_key": rule.account_set_key, "account_label": rule.account_label,
            "direction": rule.direction, "posting_kind": rule.posting_kind or "", "payment": rule.payment or "",
            "root_id": rule.root_id,
            "folder": rule.folder or "", "run_day": int(rule.run_day or 0), "run_time": rule.run_time,
            "defaults": json.loads(rule.defaults_json or "{}"), "rule_owner": rule.rule_owner,
            "active_since": rule.active_since, "include_existing": int(rule.include_existing or 0),
            "remind": int(rule.remind or 0), "remind_since": rule.remind_since,
            "run_request": int(rule.run_request or 0),
            "last_run": last[0] if last else None, "counts": counts(rule.name), "results": results(rule.name)}


def results(rule, run=None):
    """按发票算：已推成功几张、要人处理几张（停下待选、失败的票与文件）、还在路上几份文件（及录入中的票）。
    给了 `run` 就只算那一次运行的（运行记录与列表同一口径，10-09 实测 #20：运行记录按文件数、把退回的也算成已推送）。

    与完成简报同一个 `contracts.tally`（2026-10-12）：录入工作台里已删的票算有了结论、不再计入待处理；「处理中」不计
    登记后 24 小时还停在 registered 的（上传没完成、盘上文件又删了的残留，永远不会走完）。"""
    sql, values = f"SELECT state, last_code, outcome_json, registered_at FROM `tab{FILE}` WHERE rule=%s", [rule]
    if run:
        sql += " AND run=%s"
        values.append(run)
    found = contracts.tally(frappe.db.sql(sql, values, as_dict=True), now_datetime())
    return {"entered": found["pushed"], "attention": found["stopped"] + found["failed"], "moving": found["moving"]}


def counts(rule, since=None):
    """这条规则（或某次运行）的文件各处在哪一步。"""
    sql = f"SELECT state, COUNT(*) AS total FROM `tab{FILE}` WHERE rule=%s"
    values = [rule]
    if since:
        sql += " AND run=%s"
        values.append(since)
    rows = frappe.db.sql(sql + " GROUP BY state", values, as_dict=True)
    return {row.state: int(row.total) for row in rows}


def runs(name):
    _require()
    rule = read(RULE, name)
    _connection(rule.connection)
    rows = frappe.get_all(RUN, filters={"rule": rule.name}, order_by="started_at desc", limit=20,
                          fields=["name", "trigger", "started_at", "scanned_at", "found", "new_files", "skipped",
                                  "upload_failed", "note"], ignore_permissions=True)
    for row in rows:
        row["states"] = counts(rule.name, row.name)
        row["results"] = results(rule.name, row.name)
    return {"rule": project(rule), "runs": rows}
