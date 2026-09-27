# -*- coding: utf-8 -*-
"""DMS 暴露给界面调用的接口：**绑定第三方 DMS（连接向导）**。

这是"开户 → 使用"闭环里最关键的一环：租户拿到站点以后，第一件要做的事就是把
自己的 DMS 连接配上，而"配得对不对"必须能当场验 —— 真登一次、把主档下拉抓回来。
抓不到主档的"连接成功"是假成功。

顺序照老产品（`static/dms/dms-connect.js`）：**先在内存里用刚填的账号密码真登一次，
登上了才写库**。免得把错的凭据存进去，也免得"保存成功"其实是废的。

权限：只有「DMS 管理员」和系统管理员能调 —— 这个动作会**用租户配置的账号真登外部系统**，
不能让任意登录用户敲门。
"""

from __future__ import annotations

import frappe
from frappe import _

from pearnly_core.endpoints import read_password

# 本站只连一个 DMS（老产品把地址写死隐藏，向导只维护一组账号密码）。保持一致。
ADAPTER = "mrerp_dms"
DEFAULT_LABEL = "MR.ERP DMS"

ENDPOINT = "Pearnly ERP Endpoint"
# 角色名必须和控制面 `features.PRODUCT_ADMIN_ROLE` 一字不差（那边是 "DMS Admin"）。
# 这里写过中文名，改名后租户管理员会被判成"没权限" —— 表现为整块功能突然打不开。
ADMIN_ROLES = ("DMS Admin", "System Manager")


def _require_admin() -> str:
    """权限边界在后端，不在"前端把菜单藏起来"。"""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if not set(ADMIN_ROLES) & set(frappe.get_roles(user)):
        frappe.throw(_("Only an admin can configure DMS connections"), frappe.PermissionError)
    return user


def _endpoint_name() -> str | None:
    return frappe.db.get_value(ENDPOINT, {"adapter": ADAPTER, "owner_user": ["is", "not set"]}, "name")


def _row(doc) -> dict:
    # 凭据用"读得出来吗"判断，不是"这条记录存在吗"：站点密钥换过或密文损坏时，
    # 记录照样在，但每次读取都失败（2026-09-20 现场）。
    password = read_password(ENDPOINT, doc.name, "password")
    admin_password = read_password(ENDPOINT, doc.name, "admin_password")
    return {
        "name": doc.name,
        "label": doc.label,
        "adapter": doc.adapter,
        "enabled": bool(doc.enabled),
        "system_url": doc.system_url,
        "username": doc.username,
        "has_password": bool(password),
        "has_admin_creds": bool(doc.admin_username and admin_password),
        # "可用"= 启用 + 这组账号的密码现在真解得开。列表/状态显示都用它。
        "usable": bool(doc.enabled and (
            (doc.username and password) or (doc.admin_username and admin_password)
        )),
        "last_status": doc.last_status or "",
        "last_used_at": str(doc.last_used_at or ""),
        "success_count": doc.success_count or 0,
        "failure_count": doc.failure_count or 0,
    }


@frappe.whitelist()
def get_connection() -> dict:
    """本站的 DMS 连接现状。没配过就回 `connected=False`，界面直接进空态。

    `connected` 只说**现在能不能用**：连接记录存在不算。以前这里只要有一条 Endpoint
    就回 `connected=True`，于是向导显示"已连接"、状态显示"已连接"，而每次读凭据其实
    都在失败（2026-09-20 现场：解密报错与"已连接"同时出现）。
    """
    _require_admin()
    name = _endpoint_name()
    if not name:
        return {"connected": False, "default_url": _default_url()}
    row = _row(frappe.get_doc(ENDPOINT, name))
    return {"connected": bool(row["usable"]), "default_url": _default_url(), "endpoint": row}


@frappe.whitelist()
def test_credentials(username: str, password: str, system_url: str | None = None) -> dict:
    """**先测再存**：用还没落库的凭据真登一次 DMS，并把主档抓回来。

    失败也不抛异常 —— 界面把 `error_friendly` 显示出来就行（老产品同款行为）。
    """
    _require_admin()
    username = (username or "").strip()
    password = password or ""
    if not username or not password:
        frappe.throw(_("Both account and password are required"))

    from pearnly_dms.adapter.session import MrerpDmsAdapter
    from pearnly_dms.adapter.push import test_adapter

    adapter = MrerpDmsAdapter(
        system_url=(system_url or _default_url()).strip(),
        username=username,
        password=password,
        headless=True,
        screenshot_dir=frappe.get_site_path("private", "files", "dms-shots"),
    )
    adapter.verify_password = True  # 先测再存：必须真登，不能复用缓存里的会话
    return test_adapter(adapter)


@frappe.whitelist(methods=["POST"])
def save_connection(username: str, password: str, system_url: str | None = None) -> dict:
    """把凭据写进本站唯一那条 DMS 连接（没有就建）。

    ⚠️ **写库前不重复探针**：调用方必须先 `test_credentials` 通过再调这里。
    要求"存"之前必须"测"，是为了不出现"保存成功但连不上"的假成功。

    绑定成功后**自动建缓存底座**：把四组（车辆/客户/公司/地址）排队读一次。凭据一换，
    缓存 scope 就变（scope 含凭据指纹），所以这里总是要重读。界面用原生白屏覆盖显示
    实时进度，管理员绑完就能看到"底座正在建"。
    """
    actor = _require_admin()
    username = (username or "").strip()
    if not username or not password:
        frappe.throw(_("Both account and password are required"))

    url = (system_url or _default_url()).strip()
    name = _endpoint_name()
    if name:
        doc = frappe.get_doc(ENDPOINT, name)
    else:
        doc = frappe.new_doc(ENDPOINT)
        doc.adapter = ADAPTER
        doc.label = DEFAULT_LABEL

    doc.system_url = url
    doc.username = username
    doc.password = password
    # 老产品把同一组凭据同时当主凭据和管理员凭据（销售人员另建操作员行，
    # 改写客户档时继承老板这组）。
    doc.admin_username = username
    doc.admin_password = password
    doc.enabled = 1
    doc.auto_push = 0
    doc.is_default = 0
    doc.flags.ignore_permissions = False
    doc.save(ignore_permissions=False)
    frappe.db.commit()

    # **保存后回读**：写进去但解不开（站点密钥被换过）不是"保存成功"。
    saved = _row(frappe.get_doc(ENDPOINT, doc.name))
    if not saved["usable"]:
        frappe.throw(_("The saved credentials cannot be read back; check the site encryption key"))
    from pearnly_dms.connections import snapshots as connection_snapshots

    # 内部入口，不开 RPC：读取只在"绑定"和"操作顺手带回"两个时点发生。
    reading = connection_snapshots.bootstrap(doc.name, actor)
    return {"endpoint": saved, "reading": reading.get("state")}


@frappe.whitelist(methods=["POST"])
def set_connection_enabled(enabled: int | bool = 1) -> dict:
    """停用/启用这条连接。停用后推送会判定为"没凭据"，不再碰外部系统。"""
    _require_admin()
    name = _endpoint_name()
    if not name:
        frappe.throw(_("No DMS connection has been configured yet"))
    doc = frappe.get_doc(ENDPOINT, name)
    doc.enabled = 1 if int(enabled or 0) else 0
    doc.save(ignore_permissions=False)
    frappe.db.commit()
    return {"endpoint": _row(doc)}


@frappe.whitelist()
def test_endpoint(endpoint: str) -> dict:
    """测试**已保存**的连接（界面上那个「测试」按钮）。"""
    _require_admin()
    if not endpoint:
        frappe.throw(_("endpoint is missing (which connection to test)"))

    from pearnly_dms.adapter.push import test_dms_endpoint

    return test_dms_endpoint(endpoint)


def _default_url() -> str:
    """老产品写死的那个地址。已有连接就用连接里的地址，别把人家填的改掉。"""
    from pearnly_dms.adapter.push import DMS_DEFAULT_URL

    name = _endpoint_name()
    if name:
        existing = frappe.db.get_value(ENDPOINT, name, "system_url")
        if existing:
            return existing
    return DMS_DEFAULT_URL


# ── 本系统专用账号（ADR-013，用户 2026-09-27）──────────────────────────────
# 读档账号一个：只用于白天的后台读取（每分钟同步）。改写账号若干：客户档写入与权威读取，
# 登记了就不再用连接自己的管理员账号。都要先按用途真试一次（`adapter/account_probe`）才存。

SYSTEM_ACCOUNT = "DMS System Account"


def _connection():
    name = _endpoint_name()
    if not name:
        frappe.throw(_("No DMS connection has been configured yet"))
    return frappe.get_doc(ENDPOINT, name)


def _forget_session(system_url: str, username: str) -> None:
    from pearnly_core.session_lock import dms_login_key
    from pearnly_dms.adapter import session_cache
    from pearnly_dms.adapter.session import MrerpDmsAdapter

    login_url, _base = MrerpDmsAdapter._normalize_urls(system_url or "")
    session_cache.forget(dms_login_key(login_url, username))


def _taken_names(endpoint, *, ignore: set) -> set:
    """不能再登记的用户名：本站启用中的 DMS 连接用到的账号（公司连接 + 每个操作员的销售账号），
    以及已登记的系统账号（`ignore` 里的那几条除外）。同一账号不能同时给两处用 —— DMS 会互踢。"""
    names = {endpoint.username, endpoint.admin_username}
    for row in frappe.get_all(ENDPOINT, filters={"adapter": "mrerp_dms", "enabled": 1},
                              fields=["username", "admin_username"]):
        names.update((row.username, row.admin_username))
    for row in frappe.get_all(SYSTEM_ACCOUNT, fields=["name", "username"]):
        if row.name not in ignore:
            names.add(row.username)
    return {name for name in names if name}


def _probe_failure(result: dict, username: str) -> dict:
    return {"ok": False, "username": username, "missing": result.get("missing") or [],
            "error_friendly": result.get("error_friendly") or ""}


@frappe.whitelist()
def get_accounts() -> dict:
    """连接页展示用：读档账号（最多一个）与改写账号（按登记顺序）。不回密码。"""
    _require_admin()
    rows = frappe.get_all(SYSTEM_ACCOUNT, fields=["name", "username", "purpose", "enabled"],
                          order_by="creation asc")
    read = next((row for row in rows if row.purpose == "read"), None)
    return {
        "read": {"name": read.name, "username": read.username} if read else None,
        "write": [{"name": row.name, "username": row.username}
                  for row in rows if row.purpose != "read"],
    }


@frappe.whitelist(methods=["POST"])
def set_read_account(username: str, password: str) -> dict:
    """添加或更换读档账号：按"读档"真试一次，通过才替换旧的那个。"""
    _require_admin()
    from pearnly_dms.adapter.account_probe import probe

    username = (username or "").strip()
    if not username or not password:
        frappe.throw(_("Both account and password are required"))
    endpoint = _connection()
    current = frappe.get_all(SYSTEM_ACCOUNT, filters={"purpose": "read"}, fields=["name", "username"])
    keep = {row.name for row in current if row.username == username}
    if username in _taken_names(endpoint, ignore=keep):
        frappe.throw(_("This account is already in use by the connection or another system account"))
    result = probe(endpoint.system_url, username, password, "read")
    if not result.get("ok"):
        return _probe_failure(result, username)
    for row in current:
        _forget_session(endpoint.system_url, row.username)
        frappe.delete_doc(SYSTEM_ACCOUNT, row.name, ignore_permissions=True)
    frappe.get_doc({"doctype": SYSTEM_ACCOUNT, "username": username, "password": password,
                    "purpose": "read", "enabled": 1, "last_status": "ok",
                    "last_checked_at": frappe.utils.now_datetime()}).insert(ignore_permissions=True)
    frappe.db.commit()
    # 首次绑定时底座等的就是它：现在用读档账号把缺的那几组读起来（已齐就什么都不做）。
    from pearnly_dms.connections import snapshots as connection_snapshots

    reading = connection_snapshots.bootstrap(endpoint.name, frappe.session.user)
    return {"ok": True, "reading": reading.get("state")}


@frappe.whitelist(methods=["POST"])
def save_write_accounts(accounts) -> dict:
    """改写账号整表保存：新行与改了密码的行按"改写"真试；有一行不通过就一行都不存。

    `accounts` = `[{name?, username, password?}]`。带 `name` 且没填密码 = 原样保留；
    表里没有了的已登记改写账号 = 移除（同时丢掉它缓存着的会话）。
    """
    _require_admin()
    from pearnly_dms.adapter.account_probe import probe

    rows = frappe.parse_json(accounts) if isinstance(accounts, str) else (accounts or [])
    endpoint = _connection()
    existing = {row.name: row for row in frappe.get_all(
        SYSTEM_ACCOUNT, filters={"purpose": ["!=", "read"]}, fields=["name", "username"])}
    taken = _taken_names(endpoint, ignore=set(existing))
    seen, plan = set(), []
    for row in rows:
        name = str(row.get("name") or "")
        username = str(row.get("username") or "").strip()
        password = row.get("password") or ""
        if not username and not password:
            continue  # 点了"添加"又没填的空行
        if name and name not in existing:
            frappe.throw(_("This system account no longer exists; reload the page"))
        if name and username != existing[name].username:
            frappe.throw(_("Remove the account and add it again to change its name"))
        if not username or (not name and not password):
            frappe.throw(_("Both account and password are required"))
        if username in taken or username in seen:
            frappe.throw(_("This account is already in use by the connection or another system account"))
        seen.add(username)
        plan.append((name, username, password))
    for name, username, password in plan:
        if password:
            result = probe(endpoint.system_url, username, password, "write")
            if not result.get("ok"):
                return _probe_failure(result, username)
    kept = {name for name, _u, _p in plan if name}
    for name, row in existing.items():
        if name not in kept:
            _forget_session(endpoint.system_url, row.username)
            frappe.delete_doc(SYSTEM_ACCOUNT, name, ignore_permissions=True)
    for name, username, password in plan:
        if name and not password:
            continue
        if name:
            doc = frappe.get_doc(SYSTEM_ACCOUNT, name)
            doc.password = password
            _forget_session(endpoint.system_url, username)
        else:
            doc = frappe.get_doc({"doctype": SYSTEM_ACCOUNT, "username": username, "password": password,
                                  "purpose": "write", "enabled": 1})
        doc.last_status, doc.last_checked_at = "ok", frappe.utils.now_datetime()
        if name:
            doc.save(ignore_permissions=True)
        else:
            doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}
