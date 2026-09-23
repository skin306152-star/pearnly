# -*- coding: utf-8 -*-
"""MR.ERP DMS 适配器：**拥有浏览器会话**，并把"已登录的浏览器"交给业务客户端。

它负责三件事，业务逻辑一件都不在这里：

1. **登录**（时序很讲究，见 `_perform_login` 里那段注释，那是老产品线上 bug 换来的）；
2. **会话归属**：一个 adapter = 一个浏览器会话 + 一把账号锁，退出时显式登出，
   否则 DMS 那边登录槽还占着，下一次连会被当成"重复登录"；
3. **第二套凭据**（"有修改权限的账号"）：在同一个浏览器上另开隔离 context 登录，
   cookie 与主会话不互通。

照搬老产品 `services/erp/mrerp_dms_adapter.py`。两处**故意**与老产品不同：

· 凭据来源：老产品从 `from_encrypted`（Fernet 密文）解，我们从本站的
  `Pearnly ERP Endpoint` 文档取（密码在 `__Auth` 里，框架负责加密）；
· 会话锁：老产品用 Postgres advisory lock，我们用 Redis 锁
  （见 `pearnly_core/session_lock.py` 里"为什么换"的说明）。

**这一版只到"登录 + 会话"为止**。查重/建档/建单那些业务动作在业务客户端里，
是下一批要搬的东西 —— 所以这里先不假装有 `test_connection`。
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from pearnly_core.browser import BrowserSession
from pearnly_core.endpoints import read_password
from pearnly_core.session_lock import account_lock, dms_login_key
from pearnly_dms.config import pacing
from frappe import _

logger = logging.getLogger(__name__)

# DMS 的"单账号重复登录"提示原文（泰文）。照抄，别改成翻译版 —— 这是页面上真实的字。
_CONCURRENT_LOGIN_MARKER = "มีผู้เข้าใช้งานในระบบซ้ำ"


def is_concurrent_login_dialog(message: str) -> bool:
    """识别 DMS 的单账号重复登录提示。"""
    return _CONCURRENT_LOGIN_MARKER in str(message or "")


class MissingCredentials(ValueError):
    """端点没配全（地址/账号/密码/停用/适配器不对）—— 都算"没凭据"。

    单独一个类型，是为了让调用方**按类型**判，别去猜文案：`dms_push` 原来用
    `if "密码" in msg` 挑错误码，文案一翻译（2026-09-19 的 i18n 改造）这条判断就永远为假，
    错误码会从 ERR_NO_CREDS 变成 ERR_UNEXPECTED —— 界面上的提示跟着错。
    """


class DmsAuthError(RuntimeError):
    """DMS 拒了这次登录：账号密码不对、或账号没被批准。**重试没用。**"""


class DmsAdminAuthError(DmsAuthError):
    """"有修改权限的账号"那套凭据登录失败。

    单独分出来是为了让上层能说清"是管理员账号错了，不是用户的账号错了"。
    它是 `DmsAuthError` 的子类，catch 的时候要先接这个。
    """


class DmsTechnicalError(RuntimeError):
    """网络 / Playwright / 找不到元素之类的失败。**上游可以重试。**"""


class DmsUnreachableError(DmsTechnicalError):
    """**打不开对方的 DMS**（打开登录页超时）。是 `DmsTechnicalError` 的子类，
    catch 时先接这个 —— 上层要靠它区分"连不上"（该给重试按钮）与其它技术失败。

    为什么单独分出来：2026-09-23 生产上那台 DMS 的**全新 TLS 握手实测 24 秒**
    （会话复用 0.2 秒），而 Playwright 每次登录都是新连接，于是超时被兜成了
    `ERR_UNEXPECTED`，用户看到的是"我们服务器内部错误" —— 说反了。分开之后
    上层能按 `ERR_DMS_UNREACHABLE` 给重试、并在第三次说"连不上第三方 DMS 系统"。
    """


def is_timeout_error(exc: BaseException) -> bool:
    """Playwright 的超时异常。不 import playwright 也能认（它可能是可选依赖）：
    `playwright._impl._errors.TimeoutError` 的类名 + 文案里都带 Timeout。"""
    return type(exc).__name__ == "TimeoutError" and "Timeout" in str(exc)


class MrerpDmsAdapter:
    LOGIN_TIMEOUT_MS = 20000
    # 兜底预算也取自**共用口径**（`pearnly_dms.config.pacing`），不留一个比它更小的老常量
    # 当暗坑：哪怕有人绕开 `build_adapter` 直接 `MrerpDmsAdapter(...)`，也至少拿到第一档
    # 30 秒。老默认值是 15 秒 —— 那正是 2026-09-23 生产上 17 次超时、48 小时 2492 次
    # 后台刷新全失败的原因。
    DEFAULT_PAGE_TIMEOUT_MS = pacing.ATTEMPT_TIMEOUT_MS[0]
    # "打开登录页"的上限。`run_logged_in` 会按**这一次是第几次尝试**覆盖它
    # （见 `pearnly_dms.config.pacing.ATTEMPT_TIMEOUT_MS`）。None = 用默认值。
    page_timeout_ms: Optional[int] = None

    def __init__(
        self,
        *,
        system_url: str,
        username: str,
        password: str,
        admin_username: Optional[str] = None,
        admin_password: Optional[str] = None,
        headless: bool = True,
        screenshot_dir: Optional[Path] = None,
        slow_mo_ms: int = 0,
    ):
        if not system_url:
            raise MissingCredentials(_("system_url is required"))
        if not username or not password:
            raise MissingCredentials(_("username / password are required"))
        self.login_url, self.base_url = self._normalize_urls(system_url)
        self._username = username
        self._password = password
        # 两套凭据：配了 admin 且与主账号不同时，写操作走独立的 admin 会话。
        # 缺任一 = 没有 admin，行为与单凭据完全一致。
        self._admin_username = (admin_username or "").strip() or None
        self._admin_password = admin_password or None
        self.headless = headless
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self.slow_mo_ms = max(0, int(slow_mo_ms))
        self._session: Optional[BrowserSession] = None
        self._logged_in = False
        self._lock_cm = None
        self._lock_held: Optional[bool] = None
        # admin 会话不再另起浏览器：复用主会话的同一个 Browser，只多开一个隔离 context
        self._admin_ctx: Any = None
        self._admin_page: Any = None
        self._admin_logged_in = False

    # ----- 凭据来源 ---------------------------------------------------

    @classmethod
    def from_endpoint(cls, endpoint_name: str, **kwargs) -> "MrerpDmsAdapter":
        """从本站的 `Pearnly ERP Endpoint` 文档建 adapter。

        密码取的是 Frappe 的 `Password` 字段（真值在 `__Auth`），不是明文列。
        """
        import frappe

        doc = frappe.get_doc("Pearnly ERP Endpoint", endpoint_name)
        if doc.adapter != "mrerp_dms":
            raise MissingCredentials(
                _("Endpoint {0} uses adapter {1}, not mrerp_dms").format(endpoint_name, doc.adapter)
            )
        if not doc.enabled:
            raise MissingCredentials(_("Endpoint {0} is disabled").format(endpoint_name))
        # 用共用读取口：解不开密文时只回空串，不会把框架的加密技术细节留在消息队列里。
        password = read_password("Pearnly ERP Endpoint", endpoint_name, "password")
        if not password:
            raise MissingCredentials(_("Endpoint {0} has no password configured").format(endpoint_name))
        kwargs.setdefault("headless", True)
        if kwargs.get("screenshot_dir") is None:
            try:
                kwargs["screenshot_dir"] = Path(
                    frappe.get_site_path("private", "files", "dms-shots")
                )
            except Exception:
                kwargs["screenshot_dir"] = None
        return cls(
            system_url=doc.system_url,
            username=doc.username,
            password=password,
            admin_username=doc.admin_username,
            admin_password=read_password("Pearnly ERP Endpoint", endpoint_name, "admin_password") or None,
            **kwargs,
        )

    @property
    def has_admin_creds(self) -> bool:
        return bool(self._admin_username and self._admin_password)

    @property
    def has_distinct_admin_creds(self) -> bool:
        return self.has_admin_creds and (
            self._admin_username != self._username or self._admin_password != self._password
        )

    @staticmethod
    def _normalize_urls(system_url: str):
        """返回 `(login_url, base_url)`。两种写法都收：完整的 `index.php`
        地址，或者 `/dms/` 这样的基地址。`base_url` 一定以 `/` 结尾。"""
        u = system_url.strip().rstrip()
        parts = urlsplit(u)
        path = parts.path
        if path.endswith(".php"):
            login_url = u
            base_path = path.rsplit("/", 1)[0] + "/"
        else:
            base_path = path if path.endswith("/") else path + "/"
            login_url = urlunsplit((parts.scheme, parts.netloc, base_path + "index.php", "", ""))
        base_url = urlunsplit((parts.scheme, parts.netloc, base_path, "", ""))
        return login_url, base_url

    # ----- 会话生命周期 -------------------------------------------------

    def __enter__(self) -> "MrerpDmsAdapter":
        # 先抢账号锁：拿不到就不许开浏览器（并发登同一账号会把对方踢下线）
        # Lock both actual logins in stable order. Distinct employees can share
        # one admin reader; locking only the employee lets those readers eject
        # each other midway through a preflight or customer write.
        self._lock_cm = ExitStack()
        try:
            self._lock_cm.__enter__()
            accounts = {dms_login_key(self.login_url, self._username)}
            if self.has_distinct_admin_creds:
                accounts.add(dms_login_key(self.login_url, self._admin_username))
            self._lock_held = all([
                self._lock_cm.enter_context(account_lock(key)) for key in sorted(accounts)
            ])
            secrets = [self._password]
            if len(self._username) >= 3:
                secrets.append(self._username)
            self._session = BrowserSession(
                headless=self.headless,
                screenshot_dir=self.screenshot_dir,
                redact_strings=secrets,
                slow_mo_ms=self.slow_mo_ms,
            ).__enter__()
            return self
        except Exception as exc:
            self._release_lock(type(exc), exc, exc.__traceback__)
            raise

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._admin_page is not None:
            self._logout_page(self._admin_page)
        if self._admin_ctx is not None:
            try:
                self._admin_ctx.close()
            except Exception:
                pass
        self._admin_ctx = None
        self._admin_page = None
        self._admin_logged_in = False
        if self._session is not None:
            self._logout_page(self._session.page)
            self._session.__exit__(exc_type, exc, tb)
        self._session = None
        self._logged_in = False
        self._release_lock(exc_type, exc, tb)

    def _release_lock(self, exc_type=None, exc=None, tb=None) -> None:
        if self._lock_cm is None:
            return
        try:
            self._lock_cm.__exit__(exc_type, exc, tb)
        finally:
            self._lock_cm = None
            self._lock_held = None

    def _logout_page(self, page: Any) -> None:
        """显式结束 DMS 服务端会话 —— 光关浏览器，那边的登录槽还占着。"""
        try:
            page.goto(
                urljoin(self.base_url, "login/logout.php"),
                wait_until="domcontentloaded",
                timeout=5000,
            )
        except Exception:
            logger.debug("DMS 登出清理失败（不影响本次结果）", exc_info=True)

    @property
    def page(self):
        if self._session is None:
            raise RuntimeError("MrerpDmsAdapter 必须在 `with` 块里用")
        return self._session.page

    # ----- 诊断面（不暴露凭据） ------------------------------------------

    def session_cookies(self) -> list:
        """只给诊断看；cookie 不该活过这个 adapter。"""
        try:
            return self.page.context.cookies()
        except Exception:
            return []

    @property
    def concurrent_login_detected(self) -> bool:
        """当前会话有没有收到过 DMS 的"重复登录"提示。"""
        return bool(
            self._session and any(is_concurrent_login_dialog(d) for d in self._session.dialogs)
        )

    @property
    def last_dialog(self) -> str:
        """最近一条 DMS 弹窗文字（用于错误分类，不含凭据）。"""
        return self._session.last_dialog if self._session is not None else ""

    @property
    def current_url(self) -> str:
        try:
            return self.page.url
        except Exception:
            return ""

    # ----- 登录 ---------------------------------------------------------

    def login(self) -> None:
        """幂等。填凭据 → 点 JS 登录按钮 → 断言已经离开 `index.php`。

        ⚠️ **时序敏感**（老产品 2026-05-31 线上 bug 的修复）：`index.js` 是把
        `#btnlogin` 的点击处理绑在 `window.load` 上的，**不是** DOMContentLoaded。
        在 `load` 之前点，点击是**静默无操作**（formAjax 根本不发）→ 页面停在
        `index.php` → 看起来像"认证失败"，但连一句拒绝提示都没有。所以：

          1. `goto` 用 `wait_until="load"`，保证处理函数已经绑上；
          2. 点击重试几次，吸收残余的绑定竞态；
          3. **区分**"DMS 真拒绝"（有 dialog / checklogin 返回拒绝）与
             "时序没踩准"（什么都没发生）—— 只有前者是不可重试的。
        """
        if self._logged_in:
            return
        self._perform_login(self.page, self._username, self._password)
        self._logged_in = True

    def _perform_login(self, page, username: str, password: str) -> None:
        """在给定 page 上用给定凭据走一次登录。成功即返回（已离开 index.php）。

        不持有任何会话级状态，所以主会话和 admin 凭据组可以复用同一个方法。
        """
        # 直接抓 checklogin.php 的响应体：DMS 的拒绝是通过页面内自定义弹窗
        # （bshdlal）报的，不是原生 alert，读服务端响应是唯一可靠信号。响应体形如：
        #   lct::<status>::<grant>  → status 1/2 普通用户，4 是系统所有者；grant 2 = 未批准
        #   al::<msg> / err::<msg>  → 被拒（密码错 / 账号被停）
        captured: Dict[str, Optional[str]] = {"body": None}

        def _on_resp(resp):
            try:
                if "checklogin.php" in resp.url:
                    captured["body"] = (resp.text() or "").strip()
            except Exception:
                pass

        page.on("response", _on_resp)
        # 这一次给多长的"打开登录页"预算：由 `run_logged_in` 按重试次数设好。
        budget = int(self.page_timeout_ms or self.DEFAULT_PAGE_TIMEOUT_MS)
        try:
            page.goto(self.login_url, wait_until="load", timeout=budget)
            page.wait_for_selector("#btnlogin", state="visible", timeout=budget)
        except Exception as exc:  # noqa: BLE001
            # 打不开首页 = 连不上对方系统（不是我们坏了、也不是账号错）。单独报，
            # 上层才有机会给"重试"并把超时拉长。
            if is_timeout_error(exc):
                raise DmsUnreachableError(
                    f"打开 DMS 登录页超时（{budget}ms）：{self.login_url}"
                ) from exc
            raise
        try:
            page.fill("#txtusers", username)
            page.fill("#txtpasswords", password)

            attempts = 3
            # **点完之后的等待窗口也按这一次的预算给**：慢的不只是"打开页面"，也可能慢在
            # "点下去之后对方迟迟不回应"。老口径是写死的 20 秒总分片，于是重试给到 90 秒
            # 预算时，后半段其实还是只有 20 秒 —— 等于没放宽（2026-09-23 自查发现的缺口）。
            verify_ms = max(self.LOGIN_TIMEOUT_MS, int(self.page_timeout_ms or 0), budget)
            per_attempt_ms = max(5000, verify_ms // attempts)
            for _attempt in range(attempts):
                captured["body"] = None
                try:
                    # 点击超时给短的：处理函数没绑上时按钮照样可点、会立刻返回，
                    # 绝不能在这里挂满默认的 30 秒。
                    page.click("#btnlogin", timeout=5000)
                except Exception:
                    page.wait_for_timeout(600)
                    continue

                waited = 0
                while waited < per_attempt_ms:
                    if "index.php" not in page.url:
                        return  # 已经进认证区了
                    body = captured["body"]
                    if body:
                        self._classify_login_body(body)  # 被拒会抛
                        # 成功判定（lct ok）→ sdpt 导航正在飞，继续轮询 URL 等它落地
                    page.wait_for_timeout(300)
                    waited += 300
                # 这一轮压根没有 checklogin 响应 = 点击是空操作（处理函数绑晚了）。
                # 稍等一下再点一次。
                if not captured["body"]:
                    page.wait_for_timeout(600)
        finally:
            try:
                page.remove_listener("response", _on_resp)
            except Exception:
                pass

        # 走到这里说明**一次拒绝都没收到**：要么点了没回应（对方不答/页面没加载完导致点击
        # 是空操作），要么对方说成功了却没把我们送进去。两种都是"对方那边没成"，该给
        # 「重试」、也该计入熔断 —— 老口径在这里报 `DmsAuthError`（不可重试、不计熔断），
        # 于是"对方慢"被说成了"账号/密码有问题"，用户看到的话术也是错的。
        raise DmsUnreachableError(
            _("DMS sign-in did not finish (after retrying there was neither a successful "
              "checklogin response nor a redirect · the page may be slow to load or blocked)")
        )

    def _classify_login_body(self, body: str) -> None:
        """checklogin.php 响应体是拒绝就抛 `DmsAuthError`；是成功判定就正常返回
        （调用方接着等跳转）。"""
        parts = body.split("::")
        head = parts[0].strip().lower()
        if head == "lct":
            grant = parts[2].strip() if len(parts) > 2 else ""
            status = parts[1].strip() if len(parts) > 1 else ""
            if grant == "2":
                raise DmsAuthError(_("The DMS account is not approved to use the system"))
            if status == "3":
                raise DmsAuthError(_("The DMS account is marked as resigned"))
            return  # status 1/2/4 都算通过
        # al:: / err:: / 其它一律当拒绝
        raise DmsAuthError(_("DMS refused the sign-in: {0}").format(body[:160]))

    def ensure_admin_session(self) -> None:
        """在**同一个**已运行的浏览器上另开隔离 context 登录 admin 凭据组。

        绝不在同线程再起第二个 `sync_playwright()`（会死锁）。context 级隔离
        保证 admin 与主会话的登录态不会互串。
        """
        if self._admin_logged_in:
            return
        if not self.has_admin_creds:
            raise DmsTechnicalError(_("An admin session was requested but no admin credentials are configured"))
        if self._session is None:
            raise DmsTechnicalError(_("The admin session must be requested inside a `with` block"))
        secrets = [self._admin_password]
        if len(self._admin_username) >= 3:
            secrets.append(self._admin_username)
        self._admin_ctx, self._admin_page = self._session.new_isolated_page(extra_secrets=secrets)
        try:
            self._perform_login(self._admin_page, self._admin_username, self._admin_password)
        except DmsAuthError as exc:
            raise DmsAdminAuthError(_("DMS admin credentials failed to sign in: {0}").format(exc))
        self._admin_logged_in = True

    def admin_transport(self):
        """懒起 admin 会话，返回它的 transport（第一次真要用时才触发登录）。"""
        from pearnly_dms.client.transport import PlaywrightTransport

        self.ensure_admin_session()
        assert self._admin_page is not None
        return PlaywrightTransport(self._admin_page.context.request)

    def transport(self):
        """主会话的 transport（业务客户端拿它发 DMS 表单请求）。"""
        from pearnly_dms.client.transport import PlaywrightTransport

        return PlaywrightTransport(self.page.context.request)

    def client(self):
        """业务客户端（查重/建档/建单/主档都在它上面）。

        读操作走主会话；客户档**写**操作在配了 admin 且与主账号不同时懒切到 admin 会话
        —— 传的是**方法本身**（可调用），所以 admin 浏览器只在真用得着时才起。
        """
        from pearnly_dms.client.gateway import DmsClient

        admin = self.admin_transport if self.has_distinct_admin_creds else None
        return DmsClient(self.transport(), self.base_url, admin_transport=admin)

    def test_connection(self) -> Dict[str, Any]:
        """连接向导的健康检查：登录 + 抓一遍主档下拉。

        **从不抛异常** —— 回一个结构化 dict 让界面直接渲染。这样"连不上"和"连上了但
        主档是空的（`defaults_required=True`）"能分开说，而不是笼统报个失败。
        """
        import time as _time

        t0 = _time.time()
        masters = self.client().fetch_masters()
        return {
            "ok": True,
            "status": "ok",
            "message": "DMS connection ok",
            "adapter": "mrerp_dms",
            "elapsed_ms": int((_time.time() - t0) * 1000),
            "defaults_required": not bool(masters.get("cars")),
            "masters": {
                "advisors": [_ref_brief(r) for r in masters.get("advisors", [])],
                "car_models": [_ref_brief(r) for r in masters.get("cars", [])],
                "place_books": [_ref_brief(r) for r in masters.get("place_books", [])],
                "term_sales": [_ref_brief(r) for r in masters.get("term_sales", [])],
                "branches": [_ref_brief(r) for r in masters.get("branches", [])],
                "regis_behalfs": [_ref_brief(r) for r in masters.get("regis_behalfs", [])],
            },
        }


def _ref_brief(row) -> Dict[str, str]:
    """bshsd 行 `[id, code, name, ...]` → `{id, code, name}`（给连接向导用，不带动辄十几个尾列）。"""
    try:
        return {
            "id": str(row[0]),
            "code": str(row[1]) if len(row) > 1 else str(row[0]),
            "name": str(row[2]) if len(row) > 2 else (str(row[1]) if len(row) > 1 else ""),
        }
    except Exception:
        return {"id": "", "code": "", "name": ""}
