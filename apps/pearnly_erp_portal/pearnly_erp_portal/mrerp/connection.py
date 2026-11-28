"""ERP 的 MR.ERP 连接（`ERP MRERP Connection`）：网址只由 ERP 管理员在服务端设，账号密码只经 CC 桌面版授权写入（ADR-026 第 7 条，
评估 §14.4）：共用机器通道 `pearnly_core.cc_desktop.machine.mrerp_login` 经本产品的 ``pearnly_cc_desktop`` 钩子
``mrerp_login_scope`` / ``mrerp_login_store`` 交进来。网页与任何 RPC 都不收、不回密码；只有导入 / 读取的后台任务按连接现取
（:func:`lease`）。

范围（服务端判，主会话 11-02 定的口径）：ERP 管理员 ``site``（写进连接的 Password 字段，直写用）；ERP 没有连接的使用名单，别人一律
空串（拿不到票、交来也拒）。:data:`pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED` 关着时都不认。
"""
import hashlib

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.mrerp.urls import system_key, validate_url

from pearnly_erp_portal import mrerp
from pearnly_erp_portal.access import rules

CONNECTION = "ERP MRERP Connection"
DEFAULT_URL = "https://www.mrerp4sme.com"


def require_enabled():
    """所有 MR.ERP 入口的第一道：没开就当没有这个功能（同没有权限，不说明原因）。"""
    if not mrerp.enabled():
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def is_admin(user=None) -> bool:
    user = user or frappe.session.user
    return user == "Administrator" or rules.ADMIN_ROLE in frappe.get_roles(user)


def require_admin():
    require_enabled()
    if not is_admin():
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def configure(system_url, name=None, create_as=None) -> str:
    """建 / 改一条连接的网址（只限管理员）。回连接号。网址按共用层的存储语法校验（同 Cowork 连接）。

    没给连接号时按系统地址复用（11-23：testerp 多出一条没授权的空连接，旧弹窗两个按钮各存了一次）：这个站点已有同一系统地址的连接
    就回它、不新建（两个产品同一判据 `urls.system_key`）。新建时给了 ``create_as`` 就用它当连接号：同一请求号同时来两次只插得进一条，
    后到的撞主键回同一条。"""
    require_admin()
    url = str(system_url or "").strip() or DEFAULT_URL
    try:
        validate_url(url)
    except ValueError:
        frappe.throw(_("Invalid request"))
    if name:
        doc = frappe.get_doc(CONNECTION, name, for_update=True)
        if doc.system_url != url:
            doc.system_url = url
            doc.save(ignore_permissions=True)
        return doc.name
    found = same_system(url)
    if found:
        return found
    doc = frappe.get_doc({"doctype": CONNECTION, "system_url": url, "enabled": 1})
    if not create_as:
        doc.insert(ignore_permissions=True)
        return doc.name
    point = "erp_mrerp_create"
    frappe.db.savepoint(point)
    try:
        doc.insert(ignore_permissions=True, set_name=create_as)
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=point)
        frappe.clear_messages()
    return create_as


def same_system(url):
    """这个站点上同一系统地址（`urls.system_key`）最早建的那条连接；没有回 None。"""
    key = system_key(url)
    rows = frappe.get_all(CONNECTION, fields=["name", "system_url"], order_by="creation asc", limit_page_length=PAGE_MAX)
    return next((row.name for row in rows if system_key(row.system_url or DEFAULT_URL) == key), None)


PAGE_MAX = 100


def main_name():
    """主连接：最早建的那条启用的连接（ERP 每站点一般只有一条；CC 设置 → 连接的 MR.ERP 一行认它）。"""
    rows = frappe.get_all(CONNECTION, filters={"enabled": 1}, fields=["name"], order_by="creation asc",
                          limit_page_length=1)
    return rows[0].name if rows else None


def _when(value) -> str:
    """授权时刻（站点时区的裸时间）→ RFC3339 UTC，页面按设备时区显示到时分（同 Cowork `mrerp_login._when`）；空的回空。
    原先给 ``str()`` 原样，CC 设置 → 连接显示成「2026-11-28 11:08:35.095638」（11-28）。"""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    if not value:
        return ""
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
        moment = moment.replace(tzinfo=ZoneInfo(frappe.utils.get_system_timezone())).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return ""
    return moment.isoformat().replace("+00:00", "Z")


def record(name, main=None) -> dict:
    """CC 设置 → 连接的 MR.ERP 一行要的形状（同 Cowork `connections.records` 的那几项：name / adapter / enabled / main /
    config.system_url / credential）；从不含密码。"""
    row = frappe.db.get_value(CONNECTION, name, ["name", "system_url", "enabled", "credential_username",
                                                 "credential_device", "credential_authorized_at"], as_dict=True)
    if not row:
        return {}
    out = {"name": row.name, "adapter": "mrerp", "enabled": int(bool(row.enabled)), "main": row.name == main,
           "config": {"system_url": row.system_url or DEFAULT_URL}}
    if row.credential_username:
        out["credential"] = {"username": row.credential_username, "device": row.credential_device or "",
                             "at": _when(row.credential_authorized_at)}
    return out


def records(start=0, limit=50) -> list:
    """连接列表（只限管理员，服务端分页）。"""
    require_admin()
    try:
        start, limit = max(0, int(start or 0)), max(1, min(PAGE_MAX, int(limit or 50)))
    except (TypeError, ValueError):
        frappe.throw(_("Invalid request"))
    rows = frappe.get_all(CONNECTION, fields=["name"], order_by="creation asc", limit_start=start,
                          limit_page_length=limit)
    main = main_name()
    return [record(row.name, main) for row in rows]


def configure_once(system_url, name=None, request_key=None) -> str:
    """CC 设置里的「连接」：同 :func:`configure`，另按 ``request_key`` 幂等——没给连接号时同一个请求号重发（双击、网络重试）
    回同一条，不重复建。新连接的连接号由请求人与请求号定（11-23：原来先查缓存再建，两次同时到都查不到、各建一条）；同一系统地址
    已有连接的回那一条（:func:`configure`）。"""
    require_admin()
    create_as = None
    if request_key and not name:
        create_as = "mrerp-" + hashlib.sha256(f"{frappe.session.user}\n{str(request_key)[:80]}".encode()).hexdigest()[:20]
        if frappe.db.exists(CONNECTION, create_as):
            name = create_as
    return configure(system_url, name, create_as=create_as)


def view(name) -> dict:
    """给页面 / 对话看的：网址、「已授权：用户名 · 电脑 · 时间」；从不含密码。"""
    row = frappe.db.get_value(CONNECTION, name, ["name", "system_url", "enabled", "credential_username",
                                                 "credential_device", "credential_authorized_at"], as_dict=True)
    if not row:
        return {}
    out = {"connection": row.name, "system_url": row.system_url or "", "enabled": bool(row.enabled)}
    if row.credential_username:
        out["credential"] = {"username": row.credential_username, "device": row.credential_device or "",
                             "at": _when(row.credential_authorized_at)}
    return out


# ---------------- CC 桌面版授权（共用通道钩子） ----------------


def login_scope(user, connection) -> str:
    """（钩子 ``mrerp_login_scope``）``user`` 此刻对这条连接的登录能做什么：管理员 ``site``，别人空串。"""
    if not mrerp.enabled() or not connection or not frappe.db.exists(CONNECTION, connection):
        return ""
    return "site" if is_admin(user) else ""


def login_store(connection, username, password, *, user, device_label, scope) -> dict:
    """（钩子 ``mrerp_login_store``）收下 CC 桌面版交来的登录：``site`` 写进连接的 Password 字段与授权记录（此刻不是管理员就拒写）。
    回 ``{connection, system_url, username, device, at, written}``，不含密码。"""
    if scope != "site" or login_scope(user, connection) != "site":
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    doc = frappe.get_doc(CONNECTION, connection, for_update=True)
    at = now_datetime()
    doc.update({"credential_username": str(username or "")[:140], "credential_password": password,
                "credential_authorized_by": user, "credential_device": str(device_label or "")[:140],
                "credential_authorized_at": at})
    doc.save(ignore_permissions=True)
    return {"connection": doc.name, "system_url": doc.system_url or DEFAULT_URL, "username": doc.credential_username,
            "device": doc.credential_device or "", "at": _when(at), "written": True}


# ---------------- 后台任务取凭据 ----------------


def lease(name) -> dict:
    """导入 / 读取任务用：``{system_url, username, password}``；连接停用或没有回空字典。只给本域的后台任务，不是 RPC。"""
    # 解密只在后台任务里用到：Express 入口（`express.push`）导入本域时不连带载入密码模块（离线检查的 Frappe 替身没有它）
    from frappe.utils.password import get_decrypted_password

    row = frappe.db.get_value(CONNECTION, name, ["name", "system_url", "enabled", "credential_username"], as_dict=True) \
        if name else None
    if not row or not row.enabled:
        return {}
    return {"system_url": str(row.system_url or DEFAULT_URL).strip(), "username": row.credential_username or "",
            "password": get_decrypted_password(CONNECTION, row.name, "credential_password", raise_exception=False) or ""}


__all__ = ["configure", "configure_once", "is_admin", "lease", "login_scope", "login_store", "main_name", "record",
           "records", "require_admin", "require_enabled", "same_system", "view"]
