"""ERP 账号与本站点 LINE 官方号的绑定（网页侧）。

口径照旧线上 `routes/line_erp_routes.py`：

- 绑定是**自己绑自己**：码只发给当前登录的账号，网页端没有"给别人发码"的入口。
  所以这里不接收任何"绑哪个账号"的参数 —— 少一个参数就少一条越权路径。
- 一位员工同时只有一个有效码（再发一次，旧码立刻作废），码 10 分钟内有效
  （旧线上 `services/line_erp/store.py` 的 `new_code` 就是 10 分钟）。
- 解绑只解自己。

一次性消费与行锁在共用层（`pearnly_core.line_binding`）里做，这里只做权限与
"当前账号是谁"。
"""

import base64
import io

import frappe
import pyqrcode
from frappe import _
from frappe.utils import get_datetime

from pearnly_core import line, line_binding

from pearnly_erp_portal.access import guard

BINDING = "Pearnly LINE Binding"
# 旧线上页面写的就是 10 分钟（`page-integrations.ts` 的倒计时与这句文案同源）。
TTL_MINUTES = 10


def _current_user() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    return user


def _active_binding(user: str):
    return frappe.db.get_value(
        BINDING, {"user": user, "status": "active"},
        ["name", "line_user_id", "display_name", "bound_at"],
        as_dict=True,
    )


def account() -> dict:
    """本站点这个官方号对外的身份。页面用它显示"加谁为好友"。"""
    guard.require("integration.read")
    return line.public_account()


def status() -> dict:
    """当前账号绑了没有。"""
    guard.require("integration.read")
    row = _active_binding(_current_user())
    return {
        "bound": bool(row),
        "display_name": (row or {}).get("display_name") or None,
        # 页面用官方 Datetime 控件显示：库里带微秒，控件只认到秒，原样给会弹"日期格式不对"。
        "bound_at": (get_datetime(row.bound_at).strftime("%Y-%m-%d %H:%M:%S")
                     if row and row.get("bound_at") else None),
    }


def start() -> dict:
    """发一个新的绑定码。旧的未用码同时作废（共用层的既有语义）。"""
    guard.require("integration.manage")
    user = _current_user()
    # 先问官方号身份：渠道没配好就在这里失败，**不要**先把码发出去。
    # 发得出码、扫不进号 = 用户拿着一个用不了的码干等（旧线上就是这样）。
    bot = line.public_account()
    code = line_binding.generate_bind_code(user, TTL_MINUTES)
    return {**code, "bot_basic_id": bot["basic_id"], "bot_friend_url": bot["url"],
            "bot_name": bot["name"], "bot_qr": _qr(bot["url"])}


def _qr(url: str) -> str:
    """加好友二维码在本站生成（与 DMS 花名册同一个库）。旧线上把链接交给第三方网站
    画图（api.qrserver.com），等于把本租户官方号地址发给外部服务，这里不照搬。"""
    output = io.BytesIO()
    pyqrcode.create(url).svg(output, scale=4, background="white")
    return "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode()


def unbind() -> dict:
    """解绑当前账号。只解自己那一条。"""
    guard.require("integration.manage")
    row = _active_binding(_current_user())
    if row:
        line_binding.unbind(row.line_user_id)
        from pearnly_erp_portal.integrations import line_menu

        line_menu.request_sync(row.line_user_id)
    return {"bound": False}
