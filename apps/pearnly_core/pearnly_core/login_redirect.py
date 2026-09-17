# -*- coding: utf-8 -*-
"""统一登录入口：业务站点的登录一律走身份服务。

要解决的问题（2026-09-18 用户实测踩到）：
业务账号的密码存在身份服务里，**站点上没有他们的密码**。而 Frappe 自带一个
`/login` 页面，长得很正常、有邮箱和密码框，于是在租户站点上输入账号密码
永远得到 "Invalid credentials" —— 密码是对的，页面是错的。

所以这里把**未登录访客**在站点壳上的入口一律送到身份服务：
`/`、`/login`、`/app`、`/desk` → `pearnly_core.identity.begin_login`。
身份服务登录完再带 code 回来，由 callback 建本地会话。

三个产品共用这一套（用户要求"一个登录口"）。

只对**业务站点**生效：控制面（ADR-004）故意用本地账号 earn/earn，不进统一身份。
判断依据是有没有 `pearnly_product`（开通时写入，控制面没有）。
"""

from urllib.parse import quote

import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.utils import redirect as werkzeug_redirect

# 未登录访客在这几个入口会被送去身份服务
GUEST_ENTRY_PATHS = {"/", "/login", "/app", "/desk"}

# 这些前缀一律不拦：接口、静态资源、身份服务自己的端点、LINE webhook
SKIP_PREFIXES = (
    "/api/",
    "/assets/",
    "/files/",
    "/private/",
)


class _Redirect(HTTPException):
    """把重定向当成"HTTP 异常"抛出去。

    为什么不用 `frappe.Redirect`：那个只在**网站渲染路径**里被捕获
    （`frappe/website/serve.py:28`），而 `before_request` 跑在那之前，抛出去会被
    通用异常处理当成 500（实测就是 "302: Uncaught Exception"）。

    而 `frappe/app.py` 的顶层异常处理对 **werkzeug 的 HTTPException** 有专门分支：
        response = e.get_response(request.environ) if isinstance(e, HTTPException) else ...
    所以继承 werkzeug 的类、在 `get_response` 里返回一个标准重定向响应，
    就能在 before_request 阶段干净地发出重定向。
    """

    code = 302

    def __init__(self, location: str, code: int = 302):
        super().__init__()
        self.location = location
        self.code = code

    def get_response(self, environ=None, scope=None):
        return werkzeug_redirect(self.location, self.code)


def is_business_site() -> bool:
    """业务站点 = 开通时写过 pearnly_product 的站点。控制面没有。"""
    return bool(str(frappe.conf.get("pearnly_product") or "").strip())


def redirect_guests_to_identity() -> None:
    """before_request 钩子：把人送到统一登录口。"""
    if frappe.session.user != "Guest":
        return
    if not is_business_site():
        return

    path = frappe.request.path if frappe.request else ""
    if not path:
        return
    if path.startswith(SKIP_PREFIXES):
        return

    normalized = path.rstrip("/") or "/"
    if normalized not in GUEST_ENTRY_PATHS:
        return

    # 登录后落到 desk（产品首页后续再细化）
    gate = (
        "/api/method/pearnly_core.identity.begin_login?redirect_to=" + quote("/desk")
    )
    # 必须用 302：301 会被浏览器**永久缓存**，以后即使登录了也会一直被弹回登录页
    # （踩过：表现为"清缓存才好"的假故障）
    raise _Redirect(gate, 302)
