"""手机端盘点（LINE 里打开的那一页）：一次扫码/一次点数 = 一条记录，与桌面端同一批接口。

身份走**一次性票据**，照 DMS 编辑器那条链（`pearnly_dms/editor/browser_ticket.py`）：

1. LINE 内：LIFF 的 `id_token` 经 LINE 验签 → 找到这个 LINE 用户名下**生效的 ERP 绑定**；
2. 本站点已登录账号（回落，给本机验收用）：账号名下有生效绑定就用他自己那条（不放宽任何权限）；

拿到票据之后，每个请求把票据带上，服务端据此把请求认成那个人（`frappe.set_user`），
后面的权限、岗位、盘点口径与桌面端**完全同一条路**。票据不是登录态：它只能调下面这几个
接口，进不了 Desk。
"""

import secrets

import frappe
from frappe import _
from pearnly_core.execution import state as pearnly_state

from pearnly_core import line
from pearnly_erp_portal.inventory import count as counts

BINDING = "Pearnly LINE Binding"
PREFIX = "pearnly:erp:stocktake-ticket:"
# 30 分钟：够在货架间走一圈；与 DMS 那张票据同量级。
TTL_SECONDS = 30 * 60
NOT_BOUND = "This LINE account is not bound to an ERP user"
AUTH_REQUIRED = "Open this page from LINE, or sign in to the site first"


def _key(ticket: str) -> str:
    return f"{PREFIX}{ticket}"


def binding_user(line_user_id: str) -> str:
    """这个 LINE 用户名下生效的 ERP 账号；没有就空串。"""
    if not line_user_id:
        return ""
    return frappe.db.get_value(BINDING, {"line_user_id": line_user_id, "status": "active"},
                               "user") or ""


def session_binding() -> tuple:
    """本站点已登录账号自己的那条绑定（回落路径）。"""
    user = frappe.session.user
    if not user or user == "Guest":
        return "", ""
    row = frappe.db.get_value(BINDING, {"user": user, "status": "active"}, "line_user_id")
    return (row or "", user if row else "")


def issue(line_user_id: str, user: str) -> str:
    ticket = secrets.token_urlsafe(24)
    pearnly_state.store().set_value(_key(ticket), {"line_user_id": line_user_id, "user": user},
                           expires_in_sec=TTL_SECONDS)
    return ticket


def resolve(ticket: str) -> dict:
    if not ticket:
        return {}
    return pearnly_state.store().get_value(_key(ticket)) or {}


def _header(name: str) -> str:
    """请求头里的票据（浏览器用 header 带，脚本/队列里没有 request 对象）。"""
    try:
        return frappe.get_request_header(name) or ""
    except RuntimeError:
        return ""


def as_ticket_user(ticket=None) -> str:
    """把这次请求认成票据上那个人。认不出来就拒（不返回空）。

    票据首选接口参数；只在参数没给时才看请求头（`ticket` 是签名里的具名参数，
    进了函数就从 `**kwargs` 里消失了 —— 只看 `kwargs` 会永远拿不到它）。
    """
    ticket = str(ticket or _header("X-Stocktake-Line") or "")
    payload = resolve(ticket)
    user = payload.get("user") or ""
    if not user or not frappe.db.exists("User", {"name": user, "enabled": 1}):
        frappe.throw(_(AUTH_REQUIRED), frappe.AuthenticationError)
    # 绑定还在不在：解绑之后票据立刻失效（票据本身不缓存权限）。
    if binding_user(payload.get("line_user_id") or "") != user:
        frappe.throw(_(NOT_BOUND), frappe.PermissionError)
    if frappe.session.user != user:
        # 同一个浏览器里网页登录着、又打开了手机盘点页时，这些接口带着网页的登录会话进来：只这一次请求当票据上的人，
        # 换一个新的会话对象、不写回网页的登录会话。`frappe.set_user` 是就地改会话，请求结束框架会把改过的会话写回缓存，
        # 网页登录就成了未登录（10-24 验收）。同 `pearnly_core.cc_desktop.http_auth`。
        frappe.local.session = frappe._dict(user="Guest", sid="Guest", data=frappe._dict())
        frappe.local.session_obj = None
        frappe.set_user(user)
    return user


@frappe.whitelist(allow_guest=True)
def api_line_stocktake_config():
    """页面开局要的两样东西：本站的 LIFF id（没有就空）与 csrf 令牌。"""
    return {"liff_id": line.liff_id(), "csrf_token": frappe.sessions.get_csrf_token()}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def api_line_stocktake_auth(id_token=None, **kwargs):
    """LIFF 身份换票据。没有 id_token 时回落到"本站点已登录账号自己的绑定"。"""
    line_user_id = ""
    user = ""
    if id_token:
        claims = line.verify_id_token(str(id_token))
        line_user_id = str((claims or {}).get("sub") or "")
        user = binding_user(line_user_id)
        if not user:
            frappe.throw(_(NOT_BOUND), frappe.PermissionError)
    else:
        line_user_id, user = session_binding()
        if not user:
            frappe.throw(_(AUTH_REQUIRED), frappe.AuthenticationError)
    return {"ticket": issue(line_user_id, user), "user": user}


@frappe.whitelist(allow_guest=True)
def api_line_stocktake_tasks(ticket=None, **kwargs):
    """还能数的那些盘点（进行中）。"""
    as_ticket_user(ticket)
    return counts.list_counts(status="Active", limit=20)


@frappe.whitelist(allow_guest=True)
def api_line_stocktake_task(ticket=None, name=None, **kwargs):
    """这一份盘点的头部 + 前几条记录（手机上看进度用）。"""
    as_ticket_user(ticket)
    task = counts.get_count(name)
    task["entries"] = counts.list_entries(count=name, limit=10)
    return task


@frappe.whitelist(allow_guest=True)
def api_line_stocktake_lookup(ticket=None, name=None, code=None, **kwargs):
    """扫到的码 → 这个商品在这一份盘点里的**所有行**（一个货架一行）。

    手机端要拿这份清单让员工挑仓位：挑哪一行，那一发就记在那一行上。
    快照里完全没有这个商品时才登记一行（账面取当前结存，库位留空）——
    只是登记，不写实盘（写 0 会被当成"数出来是 0"）。
    """
    as_ticket_user(ticket)
    found = counts.product_of_code(code, counts.count_company(name))
    if not found:
        return {}
    rows = counts.rows_of_product(count=name, product=found["name"])
    if not rows:
        registered = counts.ensure_item(count=name, code=code)
        rows = counts.rows_of_product(count=name, product=found["name"]) or [registered]
    if not rows:
        return {}
    chosen = rows[0]
    return {"item": chosen, "rows": rows,
            "entries": counts.list_entries(count=name, item=chosen["name"], limit=5)}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def api_line_stocktake_add(ticket=None, name=None, code=None, item=None, qty=None, location=None,
                           note=None, request_id=None, photos=None, warehouse=None, stock_token=None, **kwargs):
    """记一笔。手机端每一发都带 `request_id`：重试不会记两笔。

    `item` 是员工挑的那一行（商品 × 仓库 × 库位）；只给码时按 `location` 找行。
    """
    as_ticket_user(ticket)
    if isinstance(photos, str):
        photos = frappe.parse_json(photos)
    return counts.add_entry(count=name, code=code, item=item, qty=qty, location=location,
                            note=note, request_id=request_id, photos=photos, warehouse=warehouse,
                            stock_token=stock_token, source="LINE")


@frappe.whitelist(allow_guest=True, methods=["POST"])
def api_line_stocktake_undo(ticket=None, entry=None, **kwargs):
    """撤销一笔（撤销不进合计）。"""
    as_ticket_user(ticket)
    return counts.edit_entry(entry=entry, voided=1)
