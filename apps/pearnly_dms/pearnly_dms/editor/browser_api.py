# -*- coding: utf-8 -*-
"""LINE 内编辑器页（LIFF）的**接口面**：配置 / 换取票据 / 读草稿 / 换颜色 / 地址级联 / 保存。

对应老产品的 `routes/line_dms_booking_edit_routes.py`（线上基准 501589f0），路径从
`/api/line/dms-booking/*` 换成 Frappe 的白名单方法（`/api/method/pearnly_dms.editor.browser_api.*`）。

## 几个照搬过来的判断

· **错误用稳定代码回给页面**（`dms_booking.expired` 这种），页面按码取话术。
  报错文案要按**收件人的语言**，而这个页面本身就有语言开关，所以由页面自己选文案，
  服务端不替它写死一句中文。
· **票据是唯一的凭证**，不是站点登录态：它只绑 LINE 用户 + 编辑模式（见
  `dms_browser_ticket`）。`browser_*` 全部 `allow_guest`，所以**每个请求都重新判一次**
  绑定还在不在、这份草稿是不是他的、nonce 还新不新 —— 票据本身不缓存任何权限。
· **拉长会话的地方一律不做**：load/paints/geo 只校验不消费 nonce，save 才换新 nonce。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import frappe
from frappe import _

from pearnly_core import line as line_api
from pearnly_dms.editor import booking_edit as dms_booking_edit
from pearnly_dms.editor import browser_ticket as tickets
from pearnly_dms.editor import customer_edit as dms_customer_edit

logger = logging.getLogger(__name__)

BINDING = "Pearnly LINE Binding"


class _Conflict(frappe.ValidationError):
    """409：这份草稿的 nonce 已过期/已被换掉（用户在用一张旧卡）。"""

    http_status_code = 409


class _Unavailable(frappe.ValidationError):
    """503：外部（DMS / LINE）暂时不可用 —— 这种错**重试有意义**。"""

    http_status_code = 503


# 错误码 → HTTP 语义。页面按**码**取话术，但状态码也要如实：
# "过期"和"服务器炸了"对调用方是两件事。
# Frappe 从异常对象上读 `http_status_code`（`frappe/app.py:388`），所以上面两个自建异常直接生效，
# 不用借它那几个名字别扭的（409 那个居然叫 `NameError`）。
_STATUS_EXC = {
    400: frappe.ValidationError,
    401: frappe.AuthenticationError,
    403: frappe.PermissionError,
    404: frappe.DoesNotExistError,
    409: _Conflict,
    503: _Unavailable,
}


def _fail(code: str, status: int = 400):
    """把编辑器错误码抛出去（页面按码选话术）。"""
    logger.info("[dms browser] %s", code)
    frappe.throw(code, _STATUS_EXC.get(status, frappe.ValidationError))


def _guard(call):
    """把 `DmsBookingEditError` 翻成 `_fail`；别的异常照原样抛出（那是真 bug）。"""
    try:
        return call()
    except dms_booking_edit.DmsBookingEditError as exc:
        _fail(exc.code, exc.status)


def _editor(mode: str):
    """按模式选实现（与老产品 `_editor(mode)` 同款）。"""
    if mode not in ("customer", "booking"):
        _fail("dms_booking.line_auth_required", 403)
    return dms_customer_edit if mode == "customer" else dms_booking_edit


def _line_user_from_request(id_token: str = "") -> str:
    """请求是谁发的 → LINE 用户 id。

    1. 给了 `id_token`：走 LINE 验签（**正式路径**）。验不过就 401，绝不"猜一个"。
    2. 没给：用**本站点已登录会话**去找这个账号名下的生效绑定（回落路径，本机验收与
       没有 LIFF 的站点用）。
    """
    token = str(id_token or "").strip()
    if token:
        claims = line_api.verify_id_token(token)
        if not claims or not claims.get("sub"):
            _fail("dms_booking.line_auth_required", 401)
        return str(claims["sub"])
    user = frappe.session.user
    if not user or user == "Guest":
        _fail("dms_booking.line_auth_required", 401)
    row = frappe.db.get_value(
        BINDING, {"user": user, "status": "active"}, ["line_user_id"], as_dict=True
    )
    if not row or not row.get("line_user_id"):
        _fail("dms_booking.not_bound", 403)
    return str(row["line_user_id"])


def _ticket_line_user(ticket: str) -> Dict[str, str]:
    """票据 → `{line_user_id, mode}`；无效票据一律 401（和"没验身份"同码，不泄漏票据是否存在）。"""
    resolved = tickets.resolve(ticket)
    if not resolved:
        _fail("dms_booking.line_auth_required", 401)
    return resolved


def _as_dict(value: Any) -> Dict[str, Any]:
    """POST 上来的 JSON：前端可能给对象，也可能给字符串（两种都收）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            _fail("dms_booking.invalid_payload")
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _page_language(line_user_id: str) -> str:
    """从 LINE 打开的编辑页用什么语言：**和 LINE 里的卡片同一个来源**——他手机上 LINE 的语言
    （用户 2026-09-27：DMS 的 LINE 语言跟随设备，与 ERP 一致）；问不到再退回账号语言。

    否则会出现"聊天里是中文、点开编辑页变泰文"。
    """
    account = _bound_language(line_user_id)
    try:
        from pearnly_core import line

        return line.user_language(line_user_id, default=account or "th") if line_user_id else account
    except Exception:
        return account


def _bound_language(line_user_id: str) -> str:
    """这个 LINE 用户对应账号的语言（ADR-006：语言跟账号走）。取不到回空串。"""
    try:
        from pearnly_core import line_binding

        user = line_binding.user_of(line_user_id)
        if not user:
            return ""
        return str(frappe.db.get_value("User", user, "language") or "")
    except Exception:
        logger.warning("[dms browser] 读账号语言失败", exc_info=True)
        return ""


# ── 配置 ─────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=True)
def browser_config() -> dict:
    """页面第一件事：这个站点有没有配置 LIFF。

    `available=False` **不是错误** —— 页面据此走"站点会话回落"那条路（老产品在这件事上
    直接报 503，新平台本机要能点开验收，见 `dms_browser_ticket` 的模块注释）。
    """
    liff = line_api.liff_id()
    return {
        "liff_id": liff,
        "available": bool(liff),
        "configured": line_api.is_configured(),
        # 写操作要带的 CSRF（与 `pearnly_core.shell.describe` 同一做法）。
        "csrf_token": frappe.sessions.get_csrf_token(),
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def browser_auth(id_token: str = "", draft: str = "", editor: str = "booking") -> dict:
    """换取一张编辑票据。`draft` 只是用来记日志的上下文，票据不绑 nonce。"""
    mode = tickets.normalize_mode(editor)
    line_user_id = _line_user_from_request(id_token)
    ticket = tickets.issue(line_user_id, mode)
    return {
        "ticket": ticket,
        "mode": mode,
        "language": _page_language(line_user_id),
        "draft": str(draft or ""),
    }


# ── 读 ───────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=True)
def browser_draft(ticket: str, nonce: str) -> dict:
    """编辑器首屏：客户现值 + 主档选项 + 首屏带回来的四级地址级联。"""
    who = _ticket_line_user(ticket)
    return _guard(lambda: _editor(who["mode"]).load(who["line_user_id"], nonce))


@frappe.whitelist(allow_guest=True)
def browser_paints(ticket: str, nonce: str, car_id: str) -> list:
    """换车型 → 该车型的颜色选项（只服务订车编辑器）。"""
    who = _ticket_line_user(ticket)
    if who["mode"] != "booking":
        _fail("dms_booking.expired", 409)
    return _guard(lambda: dms_booking_edit.paints(who["line_user_id"], nonce, car_id))


@frappe.whitelist(allow_guest=True)
def browser_geo(ticket: str, nonce: str, level: str, parent_id: str = "") -> list:
    """地址级联（两种模式共用一份实现）。"""
    who = _ticket_line_user(ticket)
    return _guard(lambda: _editor(who["mode"]).geo(who["line_user_id"], nonce, level, parent_id))


# ── 写 ───────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=True, methods=["POST"])
def browser_save(ticket: str, nonce: str, form: Any = None) -> dict:
    """保存草稿 → 服务端换新 nonce 并把新版卡发回 LINE；**LINE 没收到就不算成功**。"""
    from pearnly_dms import i18n

    who = _ticket_line_user(ticket)
    submitted = _as_dict(form)
    # 保存后发回 LINE 的新版卡按这个人手机上的语言生成（与聊天里其它卡一致）：这条路不经过
    # webhook，以前没套收件人语言，编辑完回来的卡片一律是泰文（用户 2026-09-27 截图）。
    with i18n.line_language(who["line_user_id"]):
        new_nonce = _guard(lambda: _editor(who["mode"]).save(who["line_user_id"], nonce, submitted))
    return {"nonce": new_nonce}


# ── 文案 ─────────────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=True)
def browser_text(keys: Any = None) -> dict:
    """页面要的文案：**四语一次给全**（页面有语言开关，切换时不该再跑一趟网络）。

    实现已经挪到公共层 `pearnly_core.browser_text`（Cowork / ERP 的页面也要用它，
    而它们不装这个 App —— 2026-09-19 定的方案 A）。这里只保留**老的入口形状**，
    让已经在用的 DMS 编辑器不用改调用。

    两种用法（与公共层一致）：

      · 给了 key 列表 → 只回这些键（白名单命名空间内）；
      · **给空列表** → 回这个页面的整个命名空间（`dms.edit.*`）。
    """
    from pearnly_core import browser_text as core_text

    return core_text.browser_text(keys=keys, prefix="dms.edit.")


__all__ = [
    "browser_auth",
    "browser_config",
    "browser_draft",
    "browser_geo",
    "browser_paints",
    "browser_save",
    "browser_text",
]
