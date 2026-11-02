"""MR.ERP 连接的登录在 CC 桌面版里授权（ADR-026 第 7 条，评估 §14.4，2026-11-02）。

网页不再收 MR.ERP 密码：用户本人在 CC 桌面版原生小窗输一次，同一次输入存两处——那台电脑的凭据管理器（代录用）和这里
（经机器通道交来，写进连接原有的 Password 字段，Frappe 加密存库；直写导入、目录采集、菜单读取照旧从这里取）。

- **谁能授权**（:func:`require_authorizer`，服务端判）：能管理这条连接的人（同 `service.configure`：管理角色 + 主档权益），
  或在这条连接使用名单上、能用它的人（同 `access.require_connection`）。只有 MR.ERP 连接。
- **写什么**（:func:`store`）：只改 ``credential_username`` / ``credential_password`` 与授权记录三项（谁、哪台电脑、什么时候），
  **不升连接版本**（同 `service.set_members`）：换登录不让账套目录、菜单与默认值、排队的单失效。老连接在网页上存的密码照用、
  不迁移；在 CC 里重新授权就覆盖。
- **回什么**（:func:`view`）：「已授权：用户名 · 电脑 · 时间」；从不回密码，也不把密码下发给任何电脑。

不提交事务、没有 RPC：会话侧的发票在 `companion.api.mrerp_login_ticket`，机器侧的收下在 `companion.credential_api`。
"""
from datetime import datetime, timezone

import frappe
from frappe.utils.password import get_decrypted_password

from . import access
from .state import read as _read

CONNECTION = access.TYPES[0]
MAX_USERNAME, MAX_PASSWORD, MAX_DEVICE = 140, 512, 140


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _manager(doc, user, lock):
    try:
        access.require_connection(doc, actor=user, management=True, enabled=False, lock=lock)
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def require_authorizer(doc, user, *, lock=True):
    """``user`` 此刻能不能改这条 MR.ERP 连接的登录：管理这条连接（停用的也行），或在使用名单上、能用它。不能就按无权拒。"""
    if doc.get("adapter") != "mrerp":
        access.deny()
    if _manager(doc, user, lock):
        return user
    return access.require_connection(doc, actor=user, lock=lock)


def can_authorize(name, user):
    """不抛错的判断（发票前、对话页显示按钮用）。不加锁。"""
    try:
        require_authorizer(_read(CONNECTION, name, lock=False), user, lock=False)
    except (frappe.PermissionError, frappe.ValidationError):
        frappe.clear_messages()
        return False
    return True


def _username(doc):
    if not doc.get("credential_username"):
        return ""
    return get_decrypted_password(CONNECTION, doc.name, "credential_username", raise_exception=False) or ""


def view(doc):
    """连接上 MR.ERP 登录的现状（给页面）：``{username, device, at}``；没有登录是 None。老连接（网页上存的）只有用户名。
    从不含密码。"""
    if doc.get("adapter") != "mrerp" or not doc.get("credential_username"):
        return None
    return {"username": _username(doc), "device": doc.get("credential_device") or "",
            "at": _when(doc.get("credential_authorized_at"))}


def _when(value):
    """库里存 UTC（不带时区）→ 带时区的 ISO，页面按设备时区显示（同 `companies.service.when`）。"""
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return ""
    if not isinstance(value, datetime):
        return ""
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _clean(username, password, device_label):
    if (not isinstance(username, str) or not username.strip() or username != username.strip()
            or len(username) > MAX_USERNAME or any(ord(ch) < 32 for ch in username)
            or not isinstance(password, str) or not password or len(password) > MAX_PASSWORD
            or set(password) == {"*"} or any(ord(ch) < 32 for ch in password)):
        frappe.throw(frappe._("connection.config_invalid"))
    return str(device_label or "").strip()[:MAX_DEVICE]


def store(name, username, password, *, actor, device_label):
    """收下 CC 桌面版交来的登录（机器通道已认过电脑与票）：按 ``actor``（发票的人）此刻的权限再核一次，写进 Password 字段与
    授权记录，不升版本。回 :func:`view` 加连接号与 MR.ERP 网址；不含密码。"""
    from . import model

    doc = _read(CONNECTION, name, lock=True)
    require_authorizer(doc, actor)
    device = _clean(username, password, device_label)
    moment = _now()
    doc.set("credential_username", username)
    doc.set("credential_password", password)
    doc.update({"credential_authorized_by": actor, "credential_device": device, "credential_authorized_at": moment})
    access.write(doc)
    config = model.pool_config(doc.adapter, doc.config_json)
    return {"connection": doc.name, "system_url": str(config.get("system_url") or ""),
            "username": username, "device": device, "at": _when(moment)}
