# -*- coding: utf-8 -*-
"""统一登录入口：业务站点的登录一律走身份服务。

要解决的问题（2026-09-18 用户实测踩到）：
业务账号的密码存在身份服务里，**站点上没有他们的密码**。而 Frappe 自带一个
`/login` 页面，长得很正常、有邮箱和密码框，于是在租户站点上输入账号密码
永远得到 "Invalid credentials" —— 密码是对的，页面是错的。

所以这里把**未登录访客**在站点壳上的入口一律送到身份服务：
`/`、`/login`、`/app`、`/desk` → `pearnly_core.identity.begin_login`；智能体对话页 `/assistant` → CC 入口。
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
# `/pearnly-app` 是业务前端的入口（见 www/pearnly-app.html）—— 访客打开它
# 也该先去身份服务，而不是先看到一屏"取不到启动数据"的错误。
# `/assistant` 是智能体对话页（ADR-019），同样从这一个登录口进。
GUEST_ENTRY_PATHS = {"/", "/login", "/app", "/desk", "/pearnly-app", "/assistant"}

# 业务前端的入口**前缀**：它的子路径同样是入口。`/pearnly-app/roster` 这种是
# 别人发来的深链接，访客打开时也该先去登录，而不是先看到一屏取不到数据的空壳。
APP_ENTRY_PREFIX = "/pearnly-app/"

# 这些前缀一律不拦：接口、静态资源、身份服务自己的端点、LINE webhook
SKIP_PREFIXES = (
    "/api/",
    "/assets/",
    "/files/",
    "/private/",
)

BEGIN_LOGIN = "/api/method/pearnly_core.identity.begin_login?redirect_to="

# 对话页 `/assistant` 的访客送去 CC 入口（ADR-019），登录页是 CC 的样子；入口登录后经本站登录口回到对话页。
# 送去时留一个两分钟的记号：记号还在又以访客身份回来（本站没登上），改走本站登录口，不在两边来回跳。
CC_ENTRY_MARK = "pearnly_cc_entry"
CC_ENTRY_MARK_SECONDS = 120


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

    def __init__(self, location: str, code: int = 302, mark: bool = False):
        super().__init__()
        self.location = location
        self.code = code
        self.mark = mark

    def get_response(self, environ=None, scope=None):
        response = werkzeug_redirect(self.location, self.code)
        if self.mark:
            set_entry_mark(response.set_cookie)
        return response


def set_entry_mark(set_cookie) -> None:
    # 参数取 Frappe CookieManager.set_cookie 与 werkzeug 都认的那几个（Frappe 的不收 path，两边默认都是 /）
    set_cookie(CC_ENTRY_MARK, "1", max_age=CC_ENTRY_MARK_SECONDS,
               secure=True, httponly=True, samesite="Lax")


def assistant_gate(cookies, entry_url: str) -> tuple[str, bool]:
    """访客打开对话页去哪：CC 入口（并留记号）；记号还在就是刚从入口回来仍没登上，改走本站登录口。"""
    if (cookies or {}).get(CC_ENTRY_MARK):
        return BEGIN_LOGIN + quote("/assistant"), False
    return entry_url.rstrip("/") + "/", True


def is_business_site() -> bool:
    """业务站点 = 开通时写过 pearnly_product 的站点。控制面没有。"""
    return bool(str(frappe.conf.get("pearnly_product") or "").strip())


def liff_target(state) -> str:
    """LIFF 深链要去的站内地址（`liff.state` 的值）；不是本站相对路径就不认（防止被拿去跳到别的网站）。"""
    target = str(state or "").strip()
    if not target:
        return ""
    if not target.startswith("/"):
        target = "/" + target
    if target.startswith("//") or "\\" in target or any(ord(char) < 32 for char in target):
        return ""
    return target


def follow_liff_state() -> None:
    """before_request 钩子：LINE 用 LIFF 打开深链时先落站点根 `/?liff.state=<站内路径>`（LIFF 的约定，本该由
    页面里的 LIFF SDK 读出再跳；后台页面不加载 SDK）。这里直接跳到那个站内地址，未登录的再由下一个钩子带着它去登录。

    2026-10-15 用户实测：盘点在 LINE 里转 LIFF 后落到了首页（登录一圈把 `liff.state` 丢了）。
    ERP / Cowork 的 LIFF 落地地址是站点根（`pearnly_control/onboarding/line_liff.py`），所有 LIFF 深链都经这里。
    """
    if not is_business_site() or not frappe.request:
        return
    if (frappe.request.path.rstrip("/") or "/") not in {"/", "/desk", "/app"}:
        return
    target = liff_target(frappe.request.args.get("liff.state"))
    if target:
        raise _Redirect(target, 302)


def guest_gate(normalized: str, query: str = "") -> str:
    """访客送去登录口的地址。具体页面（深链）连查询参数一起带过去，登录完回到的是原样那一页。"""
    if query and normalized.startswith((APP_ENTRY_PREFIX, "/desk/")):
        normalized = f"{normalized}?{query}"
    return BEGIN_LOGIN + quote(normalized)


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
    if normalized not in GUEST_ENTRY_PATHS and not normalized.startswith((APP_ENTRY_PREFIX, "/desk/")):
        return

    if normalized == "/assistant":
        from pearnly_core.identity import assistant_entry_url

        location, mark = assistant_gate(frappe.request.cookies, assistant_entry_url())
        raise _Redirect(location, 302, mark=mark)

    # 把访客**本来要去的那个入口**带过去，登录成功后由首页规则决定最终落点
    # （现在是租户自己的产品工作台，见 pearnly_core/workspace.py）。
    # 以前这里写死 "/desk"，等于绕开首页规则，所有人登录后都要先过一屏桌面。
    query = frappe.request.query_string.decode("utf-8", "replace") if frappe.request.query_string else ""
    gate = guest_gate(normalized, query)
    # 必须用 302：301 会被浏览器**永久缓存**，以后即使登录了也会一直被弹回登录页
    # （踩过：表现为"清缓存才好"的假故障）
    raise _Redirect(gate, 302)


def send_root_to_home() -> None:
    """Product roots land on the business home; control-plane Desk stays native."""
    if frappe.session.user == "Guest":
        return
    if not is_business_site():
        return

    path = frappe.request.path if frappe.request else ""
    if (path.rstrip("/") or "/") not in {"/", "/desk", "/app"}:
        return

    from pearnly_core import workspace as workspace_mod

    target = workspace_mod.landing_url()
    # 目标必须是某个具体工作台才跳；否则（规则没解析出来）保持原样，不要自己跳自己
    if not (target.startswith("/desk/") or target == "/pearnly-app"):
        return
    raise _Redirect(target, 302)
