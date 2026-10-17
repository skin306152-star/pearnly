"""Transactional Cowork connection and trusted projection store.

No collector, external request, dispatcher or commit lives here. Every mutation
locks Connection first; snapshots/refresh use current reads in the same unit.

Read-only views that only return a DTO (`detail`, `targets`, `account_set_facts`,
`records`, `refresh_status`, `stock_acc_group`, `mrerp_menu`) take no row locks:
under REPEATABLE READ their plain reads are one consistent snapshot. Locking them
let two concurrent list requests (sidebar `pending_counts` + `task_overview`)
each hold shared locks on the same Connection rows and then both upgrade to
exclusive -> deadlock 1213 (testcowork 2026-10-15), and blocked desktop
heartbeats writing the row. Whatever writes after reading reads through its own
locking path (`read_for_push`, `configure`, `request_refresh`, `choose_*`, ...).
Their authorization reads follow suit (`access.require_* / roles(..., lock=False)`):
under MariaDB 11.8 snapshot isolation a share-locked read of a `tabUser` row changed
after the view began fails with 1020 (testcowork 2026-10-15 13:48). Writers keep
current, share-locked authorization reads.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import uuid

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password
from pearnly_core.shell import entitlements

from pearnly_cowork.masters import push_inputs
from pearnly_cowork.targets import TargetAccess, TargetEvidence, catalog_groups
from pearnly_cowork.targets.identity import normalize_express_account_key
from pearnly_cowork.targets.projection import normalize_projection, normalize_scope
from pearnly_cowork.targets import mrerp_masters as master_rules, mrerp_menus as menu_rules, stock_groups
from . import access, account_masters, model, pool, snapshots, refresh as refresh_leases
from .preparation import reported_stock_groups
from .state import read as _read, heads as _heads, scope as _scope

CONNECTION, SNAPSHOT, REFRESH = access.TYPES


@dataclass
class LoadedConnection:
    doc: object
    endpoint: dict
    access: TargetAccess
    evidence: TargetEvidence
    revision: int


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _error(code):
    frappe.throw(_(code))


def _collector_available(doc):
    # MR.ERP 的账套目录由服务端采集（`mrerp_collector`）；Express 靠已绑定的小助手上报。
    if doc.adapter == "mrerp":
        return True
    return bool(doc.adapter == "express" and not doc.binding_generation
                and frappe.conf.get("pearnly_cowork_erp_push_enabled") in (True, 1))


def _dto(doc):
    return {"name": doc.name, "label": doc.label, "adapter": doc.adapter, "workspace": doc.workspace,
            "client": doc.client or None, "enabled": bool(doc.enabled), "revision": int(doc.record_revision),
            "config": model.pool_config(doc.adapter, doc.config_json), "users": model.parse(doc.allowed_users, []),
            "all_members": bool(int(doc.get("all_members") or 0)),
            "has_username": bool(doc.get("credential_username")), "has_password": bool(doc.get("credential_password")),
            "has_token": bool(doc.get("credential_token")), "collector_available": _collector_available(doc)}


def configure(workspace, adapter, request_key, label, config, client=None, users=None,
              name=None, expected_revision=None, enabled=True, credentials=None, all_members=None):
    """建或改连接。``all_members``（D2，ADR-021 第 4 批）：Express 池全所可用；``None`` = 不变（新建的 Express 池默认 1），
    给了就照它存（勾掉以后名单 ``users`` 才起作用）；MR.ERP 恒为 0，给真值按配置无效拒。"""
    actor = access.require_actor(management=True)
    # 界面上没有公司（Cowork 不做公司隔离），调用方没说明就挂站点内部那一份归属。
    workspace = workspace or access.default_workspace()
    access.require_scope(workspace, client)
    adapter = str(adapter or "").strip().lower()
    config = model.configuration(adapter, config)
    existing = _read(CONNECTION, name) if name else None
    if existing:
        access.require_connection(existing, management=True, enabled=False)
    users = model.parse(users, model.parse(existing.allowed_users, []) if existing else [actor])
    credentials = model.parse(credentials, {})
    if (not isinstance(label, str) or not label.strip() or len(label) > 80
            or not isinstance(users, list) or not users or len(users) > 200
            or any(not isinstance(u, str) or not u or len(u) > 140 for u in users)
            or len(users) != len(set(users)) or not isinstance(credentials, dict)
            or set(credentials) - {"username", "password", "token"}
            or any(not isinstance(v, str) or len(v) > 8192 or "***" in v for v in credentials.values())):
        _error("connection.config_invalid")
    for user in users:
        access.require_assignee(user, workspace, client)
    enabled = model.boolean(enabled)
    # 没给就不变（新建的 Express 池默认全所可用）；请求指纹只在给了时带它，没给的请求与以前同一个指纹
    given = [] if all_members is None else [model.boolean(all_members)]
    if given:
        all_members = given[0]
    elif existing:
        all_members = int(existing.get("all_members") or 0)
    else:
        all_members = 1 if adapter == "express" else 0
    if all_members and adapter != "express":
        _error("connection.config_invalid")
    operation = model.request_identity(frappe.local.site, actor, "configure:" + (name or workspace), request_key)
    digest = model.fingerprint([workspace, client or "", adapter, label, config, sorted(users), enabled, credentials,
                                *given])
    if name:
        doc = existing
        if (doc.workspace, doc.client or "", doc.adapter) != (workspace, client or "", adapter):
            _error("connection.scope_immutable")
        if doc.last_request_key == operation:
            if doc.last_request_hash != digest:
                _error("connection.request_conflict")
            return _dto(doc)
        if isinstance(expected_revision, bool) or str(expected_revision) != str(doc.record_revision):
            _error("connection.revision_conflict")
        doc.record_revision += 1
        # Retain immutable old evidence but remove its current authority.
        doc.projection_heads = "{}"
    else:
        name = "CWEC-" + operation
        # Scope row serializes first creation too; no gap-lock race swallowing.
        frappe.db.get_value("Cowork Workspace", workspace, "name", for_update=True)
        if frappe.db.get_value(CONNECTION, name, "name", for_update=True):
            doc = _read(CONNECTION, name)
            access.require_connection(doc, management=True, enabled=False)
            if doc.create_request_hash != digest:
                _error("connection.request_conflict")
            return _dto(doc)
        doc = frappe.get_doc({"doctype": CONNECTION, "record_key": name, "workspace": workspace,
                              "client": client or "", "adapter": adapter, "record_revision": 1,
                              "binding_generation": 0, "bound_account_set": "", "bound_profile_key": "",
                              "create_request_hash": digest, "projection_heads": "{}"})
    # 新建的 Express 池默认全所可用（D2，ADR-021）：成员在自己的电脑上登录 CC 就能加入，不用逐个加名单；勾掉以后看名单
    doc.update({"label": label.strip(), "enabled": enabled, "config_json": model.dumps(config), "all_members": all_members,
                "allowed_users": model.dumps(sorted(users)), "last_request_key": operation, "last_request_hash": digest})
    for key, value in credentials.items():
        doc.set("credential_" + key, value)
    access.write(doc)
    return _dto(doc)


def set_members(name, all_members=None, users=None):
    """设置里「电脑」的「全所可用」开关与名单（ADR-021 第 6 批；第 31 条说界面开关在第 6 批）：只限管理员、只给 Express 池
    （MR.ERP 没有全所可用，按配置无效拒）。只改 ``all_members`` 与 ``allowed_users`` 两个字段，不升 ``record_revision``、不动
    配置与目录（同补丁 `v0_0_22`：改谁能用不该让账套目录、刷新、读主档的证据失效）；``None`` = 那一项不变。勾掉全所可用时
    名单不能空（否则谁都用不了）；名单里每个人照 `configure` 逐个核（启用、有录入 / 推送记录 / 管理角色、在本归属）。
    与现值相同不写。放宽不作废排队的单；收窄后被移出的人录的单，电脑领取时按录入人重核权限（`_authorize`）照旧转人工。"""
    access.require_actor(management=True)
    doc = _read(CONNECTION, name)
    access.require_connection(doc, management=True, enabled=False)
    if doc.adapter != "express":
        _error("connection.config_invalid")
    current = model.parse(doc.allowed_users, [])
    flag = int(doc.get("all_members") or 0) if all_members is None else model.boolean(all_members)
    try:
        wanted = current if users is None else model.parse(users, None)
    except ValueError:
        wanted = None
    if (not isinstance(wanted, list) or len(wanted) > 200 or len(wanted) != len(set(wanted))
            or any(not isinstance(u, str) or not u or len(u) > 140 for u in wanted) or (not flag and not wanted)):
        _error("connection.config_invalid")
    if (flag, sorted(wanted)) == (int(doc.get("all_members") or 0), sorted(current)):
        return _dto(doc)
    for user in wanted:
        if user not in current:
            access.require_assignee(user, doc.workspace, doc.client or None)
    doc.update({"all_members": flag, "allowed_users": model.dumps(sorted(wanted))})
    access.write(doc)
    return _dto(doc)


def member_choices(name):
    """「全所可用」名单能勾的人（第 6 批审查第 6 条）：只给管理员、只给 Express 池，候选与 `set_members` 核新加的人同一口径
    （`access.assignees`）。只读。"""
    access.require_actor(management=True, lock=False)
    doc = _read(CONNECTION, name, lock=False)
    access.require_connection(doc, management=True, enabled=False, lock=False)
    if doc.adapter != "express":
        _error("connection.config_invalid")
    return access.assignees()


def detail(name):
    doc = _read(CONNECTION, name, lock=False)
    # A manager can inspect disabled/unassigned configuration without acquiring
    # permission to submit orders. All other callers need actual use permission.
    if _manager_view():
        access.require_connection(doc, management=True, enabled=False, lock=False)
    else:
        access.require_connection(doc, lock=False)
    return _dto(doc)


def _manager_view():
    return "master_data" in entitlements() and "Cowork Admin" in access.roles(frappe.session.user, lock=False)


def targets(name):
    """Safe current directory for selection; raw preparation never leaves IO."""
    doc = _read(CONNECTION, name, lock=False)
    access.require_connection(doc, management=_manager_view(), lock=False)
    _, _, key = _scope(None)
    head = _heads(doc).get(key, {})
    result = {"connection": doc.name, "connection_revision": doc.record_revision,
              "status": head.get("status") or "missing", "refresh_request_id": head.get("request_id"),
              "revision": head.get("revision"), "error_code": head.get("error_code"), "account_sets": [], "collector_available": _collector_available(doc)}
    if head.get("request_id"):
        refresh = _read(REFRESH, head["request_id"], lock=False)
        if refresh.connection != doc.name or refresh.connection_revision != doc.record_revision:
            _error("connection.snapshot_invalid")
        result["refresh_status"] = refresh.status
    if head.get("snapshot_id"):
        snapshot = _read(SNAPSHOT, head["snapshot_id"], lock=False)
        if snapshot.connection != doc.name or snapshot.connection_revision != doc.record_revision:
            _error("connection.snapshot_invalid")
        for row in model.parse(snapshot.body)["account_sets"]:
            attributes = row.get("attributes") or {}
            safe = {key: attributes[key] for key in ("path", "root", "root_label", "writable", "company", "company_name", "row", "comidyear", "seldb")
                    if key in attributes}
            mapping = attributes.get("mapping")
            if isinstance(mapping, dict):
                safe["mapping"] = {key: mapping[key] for key in ("revenue_acc", "ar_acc", "vat_output_acc", "fallback_acc", "ap_acc", "vat_input_acc") if key in mapping}
            result["account_sets"].append({"source_id": row["source_id"], "label": row.get("label"),
                                           "active": row.get("active", True), "attributes": safe})
    # 录入时要选的两级（网页弹窗与 LINE 同一份，见 targets/catalog_groups.py）。
    result["groups"] = catalog_groups.groups(doc.adapter, result["account_sets"])
    return result


# 公司主档（ADR-020，`companies`）认公司、列账套要的目录属性。`targets()` 的形状网页和 LINE 共用，不放宽；
# 这里另给一份只读视图，只给 `companies` 调用。
FACT_KEYS = ("company", "company_name", "tax_id", "branch", "period_start", "period_end", "next_end", "row", "root",
             "root_label", "writable")


def account_set_facts(name):
    """这条连接最近一次读到的账套目录：`{connection, adapter, status, account_sets: [{source_id, label, active,
    attributes: {FACT_KEYS 里有的}}]}`。权限同 `targets()`（连接使用授权）；没读到目录时 `account_sets` 为空。"""
    doc = _read(CONNECTION, name, lock=False)
    access.require_connection(doc, management=_manager_view(), lock=False)
    _, _, key = _scope(None)
    head = _heads(doc).get(key, {})
    result = {"connection": doc.name, "adapter": doc.adapter, "status": head.get("status") or "missing",
              "account_sets": []}
    if head.get("snapshot_id"):
        snapshot = _read(SNAPSHOT, head["snapshot_id"], lock=False)
        if snapshot.connection != doc.name or snapshot.connection_revision != doc.record_revision:
            _error("connection.snapshot_invalid")
        for row in model.parse(snapshot.body)["account_sets"]:
            attributes = row.get("attributes") or {}
            result["account_sets"].append({
                "source_id": row["source_id"], "label": row.get("label"), "active": row.get("active", True),
                "attributes": {key: attributes[key] for key in FACT_KEYS if key in attributes}})
    return result


def account_sets_written(connection, account_keys):
    """这条连接上这几份 Express 账套各自最近一次经 CC 桌面版写成的时刻（池登记上回执成功时由服务端记，`pool.note_written`）：
    ``{规范键: UTC 时刻}``，没登记、没写过的不在里面。一次主键查询；只读、不核权限（只给 `companies.current_book` 判两份同期时
    记住的回答还算不算数用，候选来自调用方已按连接授权读到的目录）。"""
    return {row.account_key: row.last_written_at for row in pool.registrations_of(connection, account_keys).values()
            if row.last_written_at}


MAIN_ADAPTERS = ("express", "mrerp")


def main_connections(workspace, *, enabled=True):
    """每种 ERP 的主连接（ADR-021，方案 2.10）：本归属下、不挂客户、启用、代数为 0 的连接里最早建的那条，``{adapter: 名字}``。

    电脑登记（`companion.credentials.register`）、网页录入、LINE、CC 都按它认，不再各取「列表里第一条」（列表按修改时间排，
    erpcowork 有两条启用的 Express 连接时网页和电脑可能各认一条）。``enabled=False``（只给列表标记用）：这种 ERP 一条启用的都
    没有时，标最早建的那条停用的，入口照旧显示「已停用」而不是「未连接」。只读。"""
    rows = frappe.db.sql("""SELECT name, adapter FROM `tabCowork ERP Connection`
        WHERE workspace=%s AND COALESCE(client,'')='' AND binding_generation=0 AND adapter IN %s"""
        + (" AND enabled=1" if enabled else "") + " ORDER BY enabled DESC, creation, name",
        (workspace, MAIN_ADAPTERS), as_dict=True)
    found = {}
    for row in rows:
        found.setdefault(row.adapter, row.name)
    return found


def main_of(rows, adapter):
    """``records()`` 里这种 ERP 的主连接那一行（带 ``main``）；本人不能用主连接时没有，不退到别的连接。"""
    return next((row for row in rows or () if row.get("adapter") == adapter and row.get("main")), None)


def records(workspace, client=None, start=0, limit=50):
    actor = frappe.session.user
    management = _manager_view()
    access.require_actor(management=management, lock=False)
    workspace = workspace or access.default_workspace()
    access.require_scope(workspace, client, lock=False)
    try:
        start, limit = max(0, int(start)), max(1, min(100, int(limit)))
    except (ValueError, TypeError):
        _error("connection.config_invalid")
    # Exact client scope: no-client is an explicit scope, not every client.
    names = frappe.db.sql("""SELECT name FROM `tabCowork ERP Connection`
        WHERE workspace=%s AND COALESCE(client,'')=%s ORDER BY modified DESC,name
        LIMIT %s OFFSET %s""", (workspace, client or "", limit, start), as_dict=True)
    result = []
    main = set(main_connections(workspace, enabled=False).values()) if not client else set()
    for row in names:
        doc = _read(CONNECTION, row.name, lock=False)
        if management or (doc.enabled and access.listed(doc, actor)):
            access.require_connection(doc, management=management, enabled=not management, lock=False)
            # 每种 ERP 的主连接（`main_connections`）：录入入口、LINE、CC 只认带它的那一行
            # ``manageable``：本人是管理员（设置里「全所可用」开关与名单只给管理员，第 6 批）
            result.append({**_dto(doc), "main": doc.name in main, "manageable": management})
    return result


def request_refresh(name, request_key, account_set_key=None):
    doc = _read(CONNECTION, name)
    actor = access.require_connection(doc)
    kind, key, head_key = _scope(account_set_key, doc.adapter)
    refresh_leases.expire(doc, _now())
    identity = model.request_identity(frappe.local.site, actor, "refresh:" + name, request_key)
    signature = model.fingerprint([name, int(doc.record_revision), kind, key])
    aliases = model.parse(doc.get("refresh_request_aliases"), {})
    alias = aliases.get(identity)
    # Older rows created before aliases use their operation UUID as row name.
    # Retain that durable receipt when first replayed after the schema upgrade.
    replay_name = alias.get("request_id") if alias else frappe.db.get_value(REFRESH, identity, "name", for_update=True)
    if replay_name:
        refresh = _read(REFRESH, replay_name)
        if (refresh.connection != name or refresh.request_hash != signature
                or refresh.connection_revision != doc.record_revision
                or refresh.binding_generation != doc.binding_generation
                or (alias and alias.get("request_hash") != signature)):
            _error("connection.request_conflict")
        if alias is None:
            aliases[identity] = {"request_id": refresh.name, "request_hash": signature}
            doc.refresh_request_aliases = model.dumps(aliases)
            access.write(doc)
        return _refresh_dto(refresh)
    heads = _heads(doc)
    previous = heads.get(head_key, {})
    if previous.get("request_id"):
        old = _read(REFRESH, previous["request_id"])
        if (old.status == "requested" and old.connection == name
                and old.connection_revision == doc.record_revision
                and old.binding_generation == doc.binding_generation
                and old.adapter == doc.adapter and (old.scope_kind, old.scope_key) == (kind, key)):
            # Fixed source target_refresh.request_refresh coalesces requested
            # work, updating requested_at instead of replacing its request ID.
            old.requested_at = _now()
            access.write(old)
            aliases[identity] = {"request_id": old.name, "request_hash": signature}
            doc.refresh_request_aliases = model.dumps(aliases)
            access.write(doc)
            return _refresh_dto(old)
        refresh_leases.supersede(doc, old, _now())
    refresh = refresh_leases.create(doc, kind, key, head_key, actor=actor, identity=identity, signature=signature,
                                    now=_now())
    aliases[identity] = {"request_id": refresh.name, "request_hash": signature}
    doc.refresh_request_aliases = model.dumps(aliases)
    access.write(doc)
    return _refresh_dto(refresh)


def _refresh_dto(doc):
    return {"request_id": doc.name, "connection": doc.connection, "status": doc.status,
            "account_set_key": doc.account_set_key, "result_revision": doc.result_revision or None,
            "error_code": doc.get("error_code") or None,
            "collector_available": _collector_available(doc)}


def refresh_status(name, request_id):
    doc = _read(CONNECTION, name, lock=False)
    access.require_connection(doc, lock=False)
    refresh = _read(REFRESH, request_id, lock=False)
    if refresh.connection != doc.name or refresh.connection_revision != doc.record_revision:
        _error("connection.refresh_stale")
    return _refresh_dto(refresh)


def ensure_account_masters(name, account_set_key, *, actor=None):
    """要用这份 Express 账套的主档（科目表、部门、供应商 / 客户、商品、存货科目组）：没有、或别的账套读过很久了，
    就请这条连接的电脑读一次（同一份账套同一时刻只排一个，不打断正在读的）。回此刻的状况（`account_masters.view`），
    建了请求时带 ``requested``；调用方负责提交。MR.ERP 由服务端读主档，回 ``ready``。"""
    doc = _read(CONNECTION, name)
    actor = access.require_connection(doc, actor=actor)
    if doc.adapter != "express":
        return {"state": account_masters.READY, "account_set": str(account_set_key or ""), "present": True}
    account = _stock_group_account(doc, account_set_key)
    now = _now()
    refresh_leases.expire(doc, now)
    return account_masters.ensure(doc, account, actor, now)


def _stock_group_account(doc, account_set_key):
    if doc.adapter != "express":
        _error("connection.adapter_invalid")
    account = normalize_express_account_key(account_set_key)
    if not account:
        _error("connection.config_invalid")
    return account


def stock_acc_group(name, account_set_key):
    """这份账套的合格存货科目组与选过的那个（新建录入、LINE 与提交前按它决定要不要请用户选）。"""
    doc = _read(CONNECTION, name, lock=False)
    access.require_connection(doc, lock=False)
    account = _stock_group_account(doc, account_set_key)
    return stock_groups.view(doc.stock_acc_groups, account,
                             reported_stock_groups(doc, account))


def choose_stock_acc_group(name, account_set_key, acccod):
    """记下这份账套的存货科目组，以后在这份账套新建的库存品都挂它（每个账套选一次，用户 2026-09-28）。

    与连接配置分开存：不升连接版本、不作废账套目录，正在推的票照常。同一个值重复提交结果不变。
    """
    doc = _read(CONNECTION, name)
    actor = access.require_connection(doc)
    account = _stock_group_account(doc, account_set_key)
    groups = reported_stock_groups(doc, account)
    current = stock_groups.choices(doc.stock_acc_groups).get(account) or {}
    if current.get("acccod") != str(acccod or "").strip():
        value = stock_groups.choose(doc.stock_acc_groups, account, acccod, groups,
                                    actor=actor, at=_now().isoformat())
        if value is None:
            _error("connection.stock_acc_group_invalid")
        doc.stock_acc_groups = model.dumps(value)
        access.write(doc)
    return stock_groups.view(doc.stock_acc_groups, account, groups)


_MRERP_ACCOUNT = re.compile(r"\d{1,9}:\d{1,9}")


def mrerp_account_key(value):
    """MR.ERP 账套键 `comidyear:seldb`（公司 + 年度）；不像就是空串。"""
    key = str(value or "").strip()
    return key if _MRERP_ACCOUNT.fullmatch(key) else ""


def _mrerp_menu_account(doc, account_set_key):
    if doc.adapter != "mrerp":
        _error("connection.adapter_invalid")
    account = mrerp_account_key(account_set_key)
    if not account:
        _error("connection.config_invalid")
    return account


def mrerp_menu(name, account_set_key):
    """这份 MR.ERP 账套每类单据的已关联日记账菜单、选过哪个、要不要请人选（录入提交前按它决定问不问）。"""
    doc = _read(CONNECTION, name, lock=False)
    access.require_connection(doc, lock=False)
    account = _mrerp_menu_account(doc, account_set_key)
    return _mrerp_view(doc, account)


def _mrerp_view(doc, account):
    """菜单视图 + 主档默认值（同一个选择弹窗一次问齐）。"""
    return {**menu_rules.view(doc.mrerp_menus, doc.mrerp_menu_options, account),
            "defaults": master_rules.view(doc.get("mrerp_defaults"), doc.get("mrerp_master_options"), account)}


def choose_mrerp_menu(name, account_set_key, doc_type, idmenu):
    """记下这份账套这类单据推哪个 MR.ERP 菜单（选一次，按连接 + 账套 + 单据类型记住，用户 2026-09-28）。

    只收最近一次从 MR.ERP 导入页读到、当前年度已关联日记账的选项。与连接配置分开存：不升连接版本，
    正在推的票照常（它们冻结的是准备推送时的选择）。同一个值重复提交结果不变。
    """
    doc = _read(CONNECTION, name)
    actor = access.require_connection(doc)
    account = _mrerp_menu_account(doc, account_set_key)
    if menu_rules.chosen_map(doc.mrerp_menus, account).get(doc_type) != str(idmenu or "").strip():
        value = menu_rules.choose(doc.mrerp_menus, account, doc_type, idmenu, doc.mrerp_menu_options,
                                  actor=actor, at=_now().isoformat())
        if value is None:
            _error("connection.mrerp_menu_invalid")
        doc.mrerp_menus = model.dumps(value)
        access.write(doc)
    return _mrerp_view(doc, account)


def choose_mrerp_default(name, account_set_key, field, code):
    """记下这份账套这个主档字段用哪个值（部门 / 员工 / 科目等，选一次按连接 + 账套记住，用户 2026-10-08）。

    只收最近一次从 MR.ERP 主档读到的可用值。与连接配置分开存，不升连接版本：正在推的票用的是准备推送时冻结的选择。
    """
    doc = _read(CONNECTION, name)
    actor = access.require_connection(doc)
    account = _mrerp_menu_account(doc, account_set_key)
    if master_rules.chosen_map(doc.get("mrerp_defaults"), account).get(field) != str(code or "").strip():
        value = master_rules.choose(doc.get("mrerp_defaults"), account, field, code, doc.get("mrerp_master_options"),
                                    actor=actor, at=_now().isoformat())
        if value is None:
            _error("connection.mrerp_default_invalid")
        doc.mrerp_defaults = model.dumps(value)
        access.write(doc)
    return _mrerp_view(doc, account)


def request_mrerp_menu(name, account_set_key):
    """申请去 MR.ERP 读这份账套的导入页菜单（后台任务，见 `mrerp_menu_reader`）；返回申请时刻，调用方轮询视图。"""
    doc = _read(CONNECTION, name)
    actor = access.require_connection(doc)
    account = _mrerp_menu_account(doc, account_set_key)
    if not doc.enabled:
        _error("connection.disabled")
    return {"connection": doc.name, "account_set_key": account, "actor": actor, "requested_at": _now().isoformat()}


def observe_mrerp_menus(name, account_set_key, pages, error=None):
    """内部：存下这次从 MR.ERP 读到的导入页选项（推送与读取任务共用）。不是 RPC，调用方负责提交。"""
    account = mrerp_account_key(account_set_key)
    if not account:
        return
    doc = _read(CONNECTION, name)
    if doc.adapter != "mrerp":
        return
    value = menu_rules.observe(doc.mrerp_menu_options, account, pages, at=_now().isoformat(), error=error)
    frappe.db.set_value(CONNECTION, doc.name, "mrerp_menu_options", model.dumps(value), update_modified=False)


def observe_mrerp_masters(name, account_set_key, fields, error=None):
    """内部：存下这次从 MR.ERP 读到的主档可用值（推送与读取任务共用）。不是 RPC，调用方负责提交。"""
    account = mrerp_account_key(account_set_key)
    if not account or (fields is None and not error):
        return
    doc = _read(CONNECTION, name)
    if doc.adapter != "mrerp":
        return
    value = master_rules.observe(doc.get("mrerp_master_options"), account, fields, at=_now().isoformat(), error=error)
    frappe.db.set_value(CONNECTION, doc.name, "mrerp_master_options", model.dumps(value), update_modified=False)


def mrerp_chosen_defaults(loaded, account_set_key):
    """准备推送时冻结进载荷的：这份账套各主档字段选过的代码。"""
    return master_rules.chosen_map(loaded.doc.get("mrerp_defaults"), mrerp_account_key(account_set_key))


def mrerp_chosen_menus(loaded, account_set_key):
    """准备推送时冻结进载荷的：这份账套各单据类型选过的菜单号。"""
    return menu_rules.chosen_map(loaded.doc.get("mrerp_menus"), mrerp_account_key(account_set_key))


def install_snapshot(name, observation, request_id, expected_revision, preparation=None):
    """Trusted internal collector completion, deliberately NOT an RPC.

    The caller has authenticated and collected the observation. This function
    only checks and atomically stores it; it does not create collection evidence.
    """
    doc = _read(CONNECTION, name)
    if not doc.enabled or str(expected_revision) != str(doc.record_revision):
        _error("connection.refresh_stale")
    normalized = normalize_projection(observation)
    if normalized.adapter != doc.adapter:
        _error("connection.snapshot_invalid")
    prepared = model.preparation(preparation)
    kind, key, head_key = _scope(None if normalized.scope_kind == "endpoint" else normalized.scope_key, doc.adapter)
    refresh = _read(REFRESH, request_id)
    heads = _heads(doc)
    head = heads.get(head_key, {})
    if (refresh.connection != name or refresh.connection_revision != doc.record_revision
            or refresh.binding_generation != doc.binding_generation or refresh.adapter != doc.adapter
            or (refresh.scope_kind, refresh.scope_key) != (kind, key) or head.get("request_id") != request_id
            or refresh.status not in {"requested", "succeeded"}):
        _error("connection.refresh_stale")
    content_hash = model.fingerprint([normalized.source_hash, prepared])
    current = _read(SNAPSHOT, head["snapshot_id"]) if head.get("snapshot_id") else None
    if refresh.status == "succeeded" and not (current and current.content_hash == content_hash):
        _error("connection.refresh_stale")
    result = snapshots.publish(doc, normalized, preparation=prepared, request_id=request_id)
    refresh.status = "succeeded"
    refresh.result_revision = result["revision"]
    refresh.completed_at = _now()
    access.write(refresh)
    return result


def read_for_push(connection_id, workspace, client=None, actor=None, lock=True):
    if lock is not True:
        _error("connection.lock_required")
    doc = _read(CONNECTION, connection_id, lock=True)
    actor = access.require_connection(doc, actor=actor)
    if (doc.workspace, doc.client or "") != (workspace, client or ""):
        access.deny()
    # Express 池里没有连接级默认账套（ADR-021，方案 2.3 第 7 条）：选目标、冻结、推送准备都按池目录核
    config = model.pool_config(doc.adapter, doc.config_json)
    endpoint = {"id": doc.name, "adapter": doc.adapter, "enabled": bool(doc.enabled), "config": config,
                "tenant_id": frappe.local.site, "workspace_client_id": doc.workspace,
                "binding_generation": int(doc.binding_generation or 0), "bound_account_set": doc.bound_account_set or "",
                "bound_profile_key": doc.bound_profile_key or ""}
    _, _, key = _scope(None)
    head = _heads(doc).get(key, {})
    snapshot, latest = None, None
    if head.get("snapshot_id"):
        saved = _read(SNAPSHOT, head["snapshot_id"])
        if saved.connection != doc.name or saved.connection_revision != doc.record_revision:
            _error("connection.snapshot_invalid")
        body = model.parse(saved.body)
        snapshot = {"snapshot_id": saved.name, "adapter": saved.adapter, "account_sets": body["account_sets"],
                    "head_snapshot_id": saved.name, "head_revision": head["revision"], "head_status": head["status"]}
    if head.get("request_id"):
        saved = _read(REFRESH, head["request_id"])
        if saved.connection != doc.name or saved.connection_revision != doc.record_revision:
            _error("connection.snapshot_invalid")
        latest = {"id": saved.name, "status": saved.status, "account_set_key": saved.account_set_key,
                  "adapter": saved.adapter, "result_revision": saved.result_revision}
    grant = TargetAccess(frappe.local.site, actor, doc.name, doc.workspace, True)
    evidence = TargetEvidence(frappe.local.site, actor, doc.name, latest, snapshot)
    return LoadedConnection(doc, endpoint, grant, evidence, int(doc.record_revision))


def preparation_for_target(loaded, target):
    """Return complete facts only for this exact currently locked target."""
    doc = _read(CONNECTION, loaded.doc.name)
    access.require_connection(doc, actor=loaded.access.user_id)
    if int(doc.record_revision) != loaded.revision:
        _error("connection.revision_conflict")
    if (target.endpoint_id != doc.name or target.tenant_id != frappe.local.site
            or target.user_id != loaded.access.user_id or target.binding_generation != doc.binding_generation):
        _error("connection.preparation_invalid")
    identity = normalize_express_account_key if doc.adapter == "express" else lambda v: str(v or "").strip()
    heads = _heads(doc)
    _, _, account_key = _scope(target.account_set, doc.adapter)
    _, _, endpoint_key = _scope(None)
    # An exact-account refresh owns the authority as soon as it exists. Never
    # fall back to old endpoint facts while that account is pending or stale.
    head = heads[account_key] if account_key in heads else heads.get(endpoint_key, {})
    if head.get("status") != "fresh" or not head.get("snapshot_id"):
        _error("connection.preparation_incomplete")
    saved = _read(SNAPSHOT, head["snapshot_id"])
    if (saved.connection != doc.name or saved.connection_revision != loaded.revision
            or saved.binding_generation != target.binding_generation):
        _error("connection.preparation_invalid")
    value = model.parse(saved.preparation)
    if (not value or identity(value["account_set"]) != identity(target.account_set)
            or value["complete"] is not True or (doc.adapter == "express" and
            normalize_express_account_key(value["root_key"]) != normalize_express_account_key(target.root_key))):
        _error("connection.preparation_incomplete")
    return deepcopy(value)


def read_for_log(connection_id, workspace=None, client=None, actor=None, lock=False):
    # Caller has already checked the dedicated push-log feature/role. Keep this
    # authorization independent of intake (viewing logs never grants sending).
    actor = actor or frappe.session.user
    if (frappe.conf.get("pearnly_product") != "cowork" or "push_log" not in entitlements()
            or actor == "Guest" or not {"Cowork Push Log", "Cowork Admin"}.intersection(access.roles(actor))):
        access.deny()
    user = access._current("SELECT enabled FROM `tabUser` WHERE name=%s", (actor,))
    if not user or not user[0].enabled:
        access.deny()
    doc = _read(CONNECTION, connection_id, lock=True)
    access.require_scope(doc.workspace, doc.client, actor=actor)
    if not access.listed(doc, actor) or (workspace and (workspace, client or "") != (doc.workspace, doc.client or "")):
        access.deny()
    return _dto(doc)


def adapter_of(connection_id):
    """连接的 ERP 类型（express / mrerp）；只给推送编排选确认通道用，授权仍在各自入口里核。"""
    return frappe.db.get_value(CONNECTION, connection_id, "adapter")


def mrerp_mappings(loaded):
    """MR.ERP 推送用的已配置映射（客户 / 科目）。没有配置不是错误：推送时按税号自建客户（旧版同）。"""
    if loaded.doc.adapter != "mrerp":
        _error("connection.adapter_invalid")
    return push_inputs.mappings(loaded.doc.client, "mrerp")


def credentials_for_use(loaded):
    """Internal plaintext boundary for a future authenticated outbound adapter."""
    current = read_for_push(loaded.doc.name, loaded.doc.workspace, loaded.doc.client, actor=loaded.access.user_id)
    if current.revision != loaded.revision:
        _error("connection.revision_conflict")
    return {key.removeprefix("credential_"): get_decrypted_password(CONNECTION, current.doc.name, key, raise_exception=False) or ""
            for key in model.SECRET_FIELDS}


def stock_groups_for_log(connection_id, target):
    """Current reported labels of the receipt's account set (its Account Facts, ADR-021).

    Facts belong to the account set, not to the computer or key that read them, so a later
    computer switch cannot rename another account set's stock group. Empty candidates retain
    the code from the frozen request, without a label.
    """
    read_for_log(connection_id)
    if target.get('endpoint_id') != connection_id:
        return []
    groups = (pool.facts(connection_id, target.get('account_set')) or {}).get('reported_stock_acc_groups')
    return deepcopy(groups) if isinstance(groups, list) else []
