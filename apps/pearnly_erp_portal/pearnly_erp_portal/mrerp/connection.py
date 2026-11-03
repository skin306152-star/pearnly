"""ERP 的 MR.ERP 连接（`ERP MRERP Connection`）：网址只由 ERP 管理员在服务端设，账号密码只经 CC 桌面版授权写入（ADR-026 第 7 条，
评估 §14.4）：共用机器通道 `pearnly_core.cc_desktop.machine.mrerp_login` 经本产品的 ``pearnly_cc_desktop`` 钩子
``mrerp_login_scope`` / ``mrerp_login_store`` 交进来。网页与任何 RPC 都不收、不回密码；只有导入 / 读取的后台任务按连接现取
（:func:`lease`）。

范围（服务端判，主会话 11-02 定的口径）：ERP 管理员 ``site``（写进连接的 Password 字段，直写用）；ERP 没有连接的使用名单，别人一律
空串（拿不到票、交来也拒）。:data:`pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED` 关着时都不认。
"""
import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.mrerp.urls import validate_url

from pearnly_erp_portal import mrerp
from pearnly_erp_portal.access import rules

CONNECTION = "ERP MRERP Connection"
DEFAULT_URL = "https://www.mrerp4sme.com"


def require_enabled():
    """所有 MR.ERP 入口的第一道：没开就当没有这个功能（同没有权限，不说明原因）。"""
    if not mrerp.enabled():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def is_admin(user=None) -> bool:
    user = user or frappe.session.user
    return user == "Administrator" or rules.ADMIN_ROLE in frappe.get_roles(user)


def require_admin():
    require_enabled()
    if not is_admin():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def configure(system_url, name=None) -> str:
    """建 / 改一条连接的网址（只限管理员）。回连接号。网址按共用层的存储语法校验（同 Cowork 连接）。"""
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
    doc = frappe.get_doc({"doctype": CONNECTION, "system_url": url, "enabled": 1})
    doc.insert(ignore_permissions=True)
    return doc.name


def view(name) -> dict:
    """给页面 / 对话看的：网址、「已授权：用户名 · 电脑 · 时间」；从不含密码。"""
    row = frappe.db.get_value(CONNECTION, name, ["name", "system_url", "enabled", "credential_username",
                                                 "credential_device", "credential_authorized_at"], as_dict=True)
    if not row:
        return {}
    out = {"connection": row.name, "system_url": row.system_url or "", "enabled": bool(row.enabled)}
    if row.credential_username:
        out["credential"] = {"username": row.credential_username, "device": row.credential_device or "",
                             "at": str(row.credential_authorized_at or "")}
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
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    doc = frappe.get_doc(CONNECTION, connection, for_update=True)
    at = now_datetime()
    doc.update({"credential_username": str(username or "")[:140], "credential_password": password,
                "credential_authorized_by": user, "credential_device": str(device_label or "")[:140],
                "credential_authorized_at": at})
    doc.save(ignore_permissions=True)
    return {"connection": doc.name, "system_url": doc.system_url or DEFAULT_URL, "username": doc.credential_username,
            "device": doc.credential_device or "", "at": str(at), "written": True}


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


__all__ = ["configure", "is_admin", "lease", "login_scope", "login_store", "require_admin", "require_enabled", "view"]
