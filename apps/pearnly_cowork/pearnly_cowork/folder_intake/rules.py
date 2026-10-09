"""自动录入规则：网页建、改、启停、立即运行、看运行记录（只经本域写，判据与新建录入同一套）。

规则就是「新建录入」弹窗那一组前置条件（Express 连接、年度账套、进项 / 销项、服务 / 存货、存货科目组），
外加文件夹、每月几号几点和「这一类以后都这样处理」的默认值。谁存或启用规则，谁就是负责人：自动推送以他的身份
做与网页「提交」完全相同的事，每次推送前照现有判据重核他的录入权限与连接使用授权。
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
    return {"connection": doc.name, "adapter": doc.adapter, "label": str(label or doc.name)[:80],
            "fresh": agent.fresh(state), "version": state.get("version") or "", "roots": state.get("roots") or []}


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


def save(values):
    """建或改一条规则；改了去向或文件夹就升版本，小助手下一次同步换用新规则。"""
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
    rule.update({"title": title, "workspace": doc.workspace, "client": doc.client or "", "connection": doc.name,
                 "reader": reader, "rule_owner": frappe.session.user, "account_set_key": account_set_key,
                 "account_label": label,
                 "direction": direction, "posting_kind": posting_kind, "payment": payment, "root_id": root, "folder": folder,
                 "run_day": day, "run_time": at, "defaults_json": json.dumps(defaults, sort_keys=True)})
    if not name:
        rule.enabled, rule.active_since = 1, now_datetime()
        # 结果文件夹按建规则这个人的网页语言起名，之后改语言也不变（不在盘上再长出第二个结果文件夹）
        rule.result_folder = contracts.result_folder_for(getattr(frappe.local, "lang", None))
    if before != _routing(rule) or not name:
        rule.rule_revision = int(rule.rule_revision or 0) + 1
    write(rule)
    return project(rule)


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


def set_enabled(name, enabled):
    """启用 / 停用。重新启用时「启用时刻」改成现在：这之前放进文件夹的文件不处理（用户 2026-10-07）。"""
    _require()
    rule = read(RULE, name, for_update=True)
    _connection(rule.connection)
    enabled = 1 if str(enabled) in ("1", "true", "True") else 0
    if enabled and not rule.enabled:
        _no_overlap(_reader(rule), rule.root_id, rule.folder or "", rule.name)
        rule.active_since = now_datetime()
        rule.rule_owner = frappe.session.user
    if enabled != int(rule.enabled or 0):
        rule.enabled = enabled
        rule.rule_revision = int(rule.rule_revision or 0) + 1
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
            fresh, labels = agent.fresh(state), {item["id"]: item["label"] for item in state.get("roots") or []}
            row.update(connection_label=usable[-1]["label"], agent_fresh=fresh, root_label=labels.get(row["root_id"], ""),
                       # 小助手报这条规则的文件夹不在了（授权移除 / 改名 / 子文件夹删了）：列表显示「文件夹不可用」
                       folder_state=(state.get("health") or {}).get(name) if fresh else None)
            rows.append(row)
    return {"rules": rows, "connections": usable}


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
            "active_since": rule.active_since, "run_request": int(rule.run_request or 0),
            "last_run": last[0] if last else None, "counts": counts(rule.name), "results": results(rule.name)}


def results(rule, run=None):
    """按发票算：已推成功几张、要人处理几张（停下待选、退回、失败的文件）、还在路上几份文件。
    给了 `run` 就只算那一次运行的（运行记录与列表同一口径，10-09 实测 #20：运行记录按文件数、把退回的也算成已推送）。"""
    entered = attention = 0
    sql, values = f"SELECT state, outcome_json FROM `tab{FILE}` WHERE rule=%s AND state IN ('done', 'failed')", [rule]
    if run:
        sql += " AND run=%s"
        values.append(run)
    for row in frappe.db.sql(sql, values, as_dict=True):
        if row.state == "failed":
            attention += 1
            continue
        for _name, status, _reason in json.loads(row.outcome_json or "{}").get("_signature") or []:
            entered += status == "success"
            attention += status != "success"
    moving = sum(count for state, count in counts(rule, run).items() if state not in ("done", "failed", "not_invoice"))
    return {"entered": entered, "attention": attention, "moving": moving}


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
