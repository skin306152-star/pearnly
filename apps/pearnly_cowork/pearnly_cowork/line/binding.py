"""Cowork 账号与本站点 LINE 官方号的绑定（网页侧），照 ERP `integrations/binding.py`。

- 绑定是**自己绑自己**：码只发给当前登录的账号，不接收"绑哪个账号"的参数。
- 一个账号同时只有一个有效码（再发一次旧码作废），10 分钟有效（旧线上同一口径）。
- 解绑只解自己。谁能用 LINE：团队管理里勾了 LINE 的人 + Cowork Admin 本人。

一次性消费与行锁在共用层 `pearnly_core.line_binding`，这里只做权限与"当前账号是谁"。
"""

import base64
import io

import frappe
import pyqrcode
from frappe import _
from frappe.utils import get_datetime

from pearnly_core import line, line_binding

from pearnly_cowork.access import menu

BINDING = "Pearnly LINE Binding"
TTL_MINUTES = 10


def enabled(user=None) -> bool:
    """这个账号开没开 LINE（与团队管理那一勾、个人信息页那一块同一判据）。"""
    user = user or frappe.session.user
    if not user or user == "Guest" or frappe.conf.get("pearnly_product") != "cowork":
        return False
    if user == "Administrator":
        return True
    return bool(frappe.db.get_value("User", user, "enabled")
                and {menu.LINE_ROLE, menu.ADMIN_ROLE} & set(frappe.get_roles(user)))


def _require():
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if not enabled(user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)
    return user


def _active_binding(user: str):
    return frappe.db.get_value(BINDING, {"user": user, "status": "active"},
                               ["name", "line_user_id", "display_name", "bound_at"], as_dict=True)


def status() -> dict:
    row = _active_binding(_require())
    return {
        "bound": bool(row),
        "display_name": (row or {}).get("display_name") or None,
        # 官方 Datetime 控件只认到秒；库里带微秒，原样给会弹"日期格式不对"。
        "bound_at": (get_datetime(row.bound_at).strftime("%Y-%m-%d %H:%M:%S")
                     if row and row.get("bound_at") else None),
    }


def start() -> dict:
    """发一个新的绑定码。先取官方号身份：渠道没配好就在这里失败，不先把码发出去。"""
    user = _require()
    bot = line.public_account()
    code = line_binding.generate_bind_code(user, TTL_MINUTES)
    return {**code, "bot_basic_id": bot["basic_id"], "bot_friend_url": bot["url"],
            "bot_name": bot["name"], "bot_qr": _qr(bot["url"])}


def _qr(url: str) -> str:
    # 加好友二维码在本站生成，不把官方号地址交给第三方画图网站（旧线上用 api.qrserver.com）。
    output = io.BytesIO()
    pyqrcode.create(url).svg(output, scale=4, background="white")
    return "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode()


def unbind() -> dict:
    row = _active_binding(_require())
    if row:
        line_binding.unbind(row.line_user_id)
    return {"bound": False}
