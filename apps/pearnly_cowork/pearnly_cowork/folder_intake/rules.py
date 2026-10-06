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
from pearnly_cowork.folder_intake import agent, contracts, destinations, settings
from pearnly_cowork.folder_intake.access import FILE, RULE, RUN, read, write
from pearnly_cowork.targets.identity import normalize_express_account_key

CONNECTION = "Cowork ERP Connection"
MAX_RULES = 50


def _error(error):
    frappe.throw(_(error.code))


def _connection(name):
    """规则只挂在能用的 Express 连接上（与新建录入同一判据：录入权限 + 连接使用授权 + 连接启用）。"""
    if not isinstance(name, str) or not name or len(name) > 140:
        frappe.throw(_("cowork.folder.invalid"))
    doc = frappe.get_doc(CONNECTION, name)
    connection_access.require_connection(doc)
    # 读文件夹的是这条连接的 Windows 小助手（配对码身份）；去向的适配见 `destinations`。
    if not destinations.supported(doc.adapter) or int(doc.binding_generation or 0) != 0:
        frappe.throw(_("cowork.folder.express_only"))
    return doc


def _require():
    settings.require()
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


def options(connection, account_set_key=None):
    """规则表单要的选项：这台小助手允许读取的根目录与文件夹、账套的科目与部门、存货科目组。"""
    _require()
    doc = _connection(connection)
    state = agent.read(doc.name)
    result = {"agent": {"fresh": agent.fresh(state), "version": state.get("version") or "", "at": state.get("at")},
              "roots": state.get("roots") or [], "accounts": None, "departments": None, "stock_group": None}
    if account_set_key:
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
    try:
        title = contracts.text(values.get("title"), maximum=140)
        direction = values.get("direction")
        posting_kind = values.get("posting_kind")
        if direction not in contracts.DIRECTIONS or posting_kind not in contracts.POSTING_KINDS:
            raise contracts.ContractError("cowork.folder.invalid")
        root = contracts.root_id(values.get("root_id"))
        folder = contracts.folder(values.get("folder"))
        day, at = contracts.schedule(values.get("run_day"), values.get("run_time"))
        accounts, departments, _found = _masters(doc, values.get("account_set_key"))
        defaults = contracts.defaults(values.get("defaults"), direction=direction, posting_kind=posting_kind,
                                      accounts=accounts, departments=departments)
    except contracts.ContractError as error:
        _error(error)
    account_set_key, label = _catalog_item(doc.name, values.get("account_set_key"))
    if posting_kind == "stock":
        _stock_ready(doc.name, account_set_key)
    roots = {row["id"] for row in agent.read(doc.name).get("roots") or []}
    if root not in roots:
        frappe.throw(_("cowork.folder.root_invalid"))
    name = values.get("name") or None
    rule = read(RULE, name, for_update=True) if name else frappe.new_doc(RULE)
    if name and rule.connection != doc.name:
        # 换连接（换电脑）等于另一条规则：原规则停用，另建一条。
        frappe.throw(_("cowork.folder.connection_fixed"))
    _no_overlap(doc.name, root, folder, rule.name if name else None)
    if not name and frappe.db.count(RULE, {"connection": doc.name}) >= MAX_RULES:
        frappe.throw(_("cowork.folder.too_many"))
    before = _routing(rule) if name else None
    rule.update({"title": title, "workspace": doc.workspace, "client": doc.client or "", "connection": doc.name,
                 "rule_owner": frappe.session.user, "account_set_key": account_set_key, "account_label": label,
                 "direction": direction, "posting_kind": posting_kind, "root_id": root, "folder": folder,
                 "run_day": day, "run_time": at, "defaults_json": json.dumps(defaults, sort_keys=True)})
    if not name:
        rule.enabled, rule.active_since = 1, now_datetime()
    if before != _routing(rule) or not name:
        rule.rule_revision = int(rule.rule_revision or 0) + 1
    write(rule)
    return project(rule)


def _routing(rule):
    return [rule.get(key) for key in ("account_set_key", "direction", "posting_kind", "root_id", "folder",
                                      "run_day", "run_time", "defaults_json", "rule_owner")]


def _no_overlap(connection, root, folder, current):
    """同一台小助手上启用的规则，文件夹不能相同或互相包含：否则同一份文件会按两套口径各推一次。"""
    rows = frappe.get_all(RULE, filters={"connection": connection, "enabled": 1, "root_id": root},
                          fields=["name", "folder"], ignore_permissions=True)
    for row in rows:
        if row.name != current and contracts.nested(row.folder or "", folder):
            frappe.throw(_("cowork.folder.overlap"))


def set_enabled(name, enabled):
    """启用 / 停用。重新启用时「启用时刻」改成现在：这之前放进文件夹的文件不处理（用户 2026-10-07）。"""
    _require()
    rule = read(RULE, name, for_update=True)
    _connection(rule.connection)
    enabled = 1 if str(enabled) in ("1", "true", "True") else 0
    if enabled and not rule.enabled:
        _no_overlap(rule.connection, rule.root_id, rule.folder or "", rule.name)
        rule.active_since = now_datetime()
        rule.rule_owner = frappe.session.user
    if enabled != int(rule.enabled or 0):
        rule.enabled = enabled
        rule.rule_revision = int(rule.rule_revision or 0) + 1
        write(rule)
    return project(rule)


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
        fresh = agent.fresh(agent.read(doc.name))
        for name in frappe.get_all(RULE, filters={"connection": doc.name}, pluck="name",
                                   order_by="creation asc", ignore_permissions=True):
            rows.append({**project(read(RULE, name)), "connection_label": usable[-1]["label"], "agent_fresh": fresh})
    return {"rules": rows, "connections": usable}


def project(rule):
    last = frappe.get_all(RUN, filters={"rule": rule.name}, fields=["started_at", "found", "new_files", "trigger"],
                          order_by="started_at desc", limit=1, ignore_permissions=True)
    return {"name": rule.name, "title": rule.title, "connection": rule.connection, "enabled": int(rule.enabled or 0),
            "account_set_key": rule.account_set_key, "account_label": rule.account_label,
            "direction": rule.direction, "posting_kind": rule.posting_kind, "root_id": rule.root_id,
            "folder": rule.folder or "", "run_day": int(rule.run_day or 0), "run_time": rule.run_time,
            "defaults": json.loads(rule.defaults_json or "{}"), "rule_owner": rule.rule_owner,
            "active_since": rule.active_since, "run_request": int(rule.run_request or 0),
            "last_run": last[0] if last else None, "counts": counts(rule.name)}


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
    return {"rule": project(rule), "runs": rows}
