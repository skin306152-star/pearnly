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
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from pearnly_core.browser import BrowserSession
from pearnly_core.endpoints import read_password
from pearnly_core.session_lock import account_lock, dms_login_key, SessionLockUnavailable
from pearnly_dms.adapter.read_priority import (
    foreground, yield_to_foreground, wait_for_foreground, ReadDeferred,
)

#: 后台读取最多等交互操作多久（秒）。等不到才退回 `busy` —— 以前是"一点都不等"，
#: 于是必然 busy，而 busy 之后没人重排，缓存再也不更新（2026-09-25 实测）。
READ_DEFER_TIMEOUT_SECONDS = 30.0
#: 后台读取拿账号锁最多等多久（秒）。同一笔操作刚收尾时锁可能还没放。
READ_LOCK_WAIT_SECONDS = 10.0
#: 操作员那一笔拿账号锁最多等多久（秒），与改动前一致。
FOREGROUND_LOCK_WAIT_SECONDS = 180.0
#: 等锁时每隔多久回调一次（让 LINE 的"正在输入"不断掉；LINE 最长 60 秒）。
LOCK_HEARTBEAT_SECONDS = 20.0
#: 账号池里"试一下这个账号空不空"最多等多久（秒）。
POOL_PROBE_SECONDS = 0.3


def _lock_wait_heartbeat() -> None:
    """等锁期间的回调：由调用方（例如 LINE 后台任务）挂在 `frappe.local` 上。失败不影响等锁。"""
    try:
        import frappe

        hook = getattr(frappe.local, "dms_lock_wait_hook", None)
        if callable(hook):
            hook()
    except Exception:
        logger.debug("DMS lock-wait hook failed", exc_info=True)
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


class DmsLoginPageUnreachableError(DmsUnreachableError):
    """**登录页都没打开**（打开登录页超时）：账号密码还没提交，对方那边不会留下会话。

    与"点了登录、对方没回应"（同样报 `DmsUnreachableError`）分开：那时对方可能已经留下一个
    会话，马上再登可能被判成重复登录。只有这一种，LINE 写入任务才会在同一个任务里自动用
    下一档再试一次（`adapter.operations.run_logged_in(line_write=True)`，2026-10-13）。
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
        # 账号池：懒解析；实际选中的管理员账号及其锁（用到时才拿）
        self._admin_pool = None
        self._admin_account = None
        self._admin_lock_held: Optional[bool] = None
        # 本次是否复用了缓存的销售会话（诊断用）
        self.session_reused = False
        # True = 本次是在验证账号密码：不复用缓存会话，一定真登一次
        self.verify_password = False
        # True = 用完立即登出、不存会话缓存（只试登录的场合：操作员本人的账号不该被我们占着）
        self.sign_out_on_exit = False

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
        # 先抢账号锁：拿不到就不许开浏览器（并发登同一账号会把对方踢下线）。
        #
        # 2026-09-27 起**管理员账号的锁用到时才拿**（`_acquire_admin`）：以前只要配了管理员，
        # 每次操作都同时锁销售与管理员两个账号，全体操作员因此在管理员这一把锁上排队，
        # 哪怕这次根本用不到管理员。加锁顺序固定为"先销售、后管理员"，不会互相等死；
        # 只用管理员的后台读取（`skip_sales_session`）只锁管理员。
        self._lock_cm = ExitStack()
        try:
            self._lock_cm.__enter__()
            read_batch = bool(getattr(self, "read_only_batch", False))
            admin_only = bool(getattr(self, "skip_sales_session", False)) and self.has_distinct_admin_creds
            # "交互优先"的标记仍覆盖本次可能用到的全部账号：后台读取据此让路。
            priority = {self._sales_key()} | {self._key(a.username) for a in self._admin_members()}
            if read_batch:
                # 撞上操作员的会话就**等它做完**（有界），不要立刻退回 busy。
                wait_for_foreground(priority, READ_DEFER_TIMEOUT_SECONDS)
            else:
                self._lock_cm.enter_context(foreground(priority))
            if admin_only:
                self._acquire_admin()
                self._lock_held = self._admin_lock_held
            else:
                self._lock_held = self._take_lock(self._sales_key())
            if read_batch:
                yield_to_foreground(priority)
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

    # ----- 账号锁与账号池 -----------------------------------------------

    def _key(self, username: str) -> str:
        return dms_login_key(self.login_url, username)

    def _sales_key(self) -> str:
        return self._key(self._username)

    def use_account(self, username: str, password: str) -> None:
        """整次只用这一个账号（读档账号）：不登销售账号、不借管理员。必须在 `with` 之前调用。"""
        if self._session is not None:
            raise DmsTechnicalError(_("The DMS account can only be switched before the session starts"))
        self._username, self._password = username, password
        self._admin_username = self._admin_password = None
        self._admin_pool = []
        self.skip_sales_session = False

    def _admin_members(self):
        if self._admin_pool is None:
            if not self.has_distinct_admin_creds:
                self._admin_pool = []
            else:
                from pearnly_dms.adapter import admin_pool

                self._admin_pool = admin_pool.members(
                    self._admin_username, self._admin_password, exclude=self._username)
        return self._admin_pool

    def _take_lock(self, key: str, *, wait: Optional[float] = None) -> bool:
        """在本次的 ExitStack 里拿一把账号锁。等待期间定时回调（让 LINE 保持"正在输入"）。"""
        read_batch = bool(getattr(self, "read_only_batch", False))
        total = READ_LOCK_WAIT_SECONDS if read_batch else FOREGROUND_LOCK_WAIT_SECONDS
        total = total if wait is None else wait
        deadline = time.monotonic() + total
        while True:
            chunk = max(0.1, min(LOCK_HEARTBEAT_SECONDS, deadline - time.monotonic()))
            try:
                return bool(self._lock_cm.enter_context(account_lock(key, timeout_sec=chunk)))
            except SessionLockUnavailable as exc:
                if time.monotonic() >= deadline - 0.05:
                    if read_batch:
                        raise ReadDeferred("The DMS account is busy") from exc
                    raise
                _lock_wait_heartbeat()

    def _acquire_admin(self) -> None:
        """挑一个空闲的管理员账号并锁住它：先按轮转顺序各试一下，都忙才排队等一个。"""
        if self._admin_lock_held is not None:
            return
        from pearnly_dms.adapter import admin_pool

        candidates = admin_pool.rotated(self._admin_members())
        if not candidates:
            raise DmsTechnicalError(_("An admin session was requested but no admin credentials are configured"))
        chosen = None
        if len(candidates) > 1:
            for account in candidates:
                try:
                    held = self._take_lock(self._key(account.username), wait=POOL_PROBE_SECONDS)
                except (SessionLockUnavailable, ReadDeferred):
                    continue
                chosen = account
                break
        if chosen is None:
            chosen = candidates[0]
            held = self._take_lock(self._key(chosen.username))
        self._admin_username, self._admin_password = chosen.username, chosen.password
        self._admin_account = chosen
        self._admin_lock_held = held

    def __exit__(self, exc_type, exc, tb) -> None:
        # 能复用就不登出：会话连同 cookie 存进缓存，下一次同账号操作直接接着用
        # （见 `adapter/session_cache`）。出了技术故障、或被别处登录顶掉时照旧登出并丢缓存。
        from pearnly_dms.adapter import session_cache
        from pearnly_dms.contracts.client_base import DmsClientError

        keep = (session_cache.enabled() and not getattr(self, "sign_out_on_exit", False)
                and not self.concurrent_login_detected
                and (exc_type is None or issubclass(exc_type, DmsClientError)))
        if self._admin_page is not None:
            self._finish_session(self._admin_ctx, self._admin_page, self._admin_username,
                                 self._admin_logged_in, keep, idle=session_cache.idle_seconds())
        if self._admin_ctx is not None:
            try:
                self._admin_ctx.close()
            except Exception:
                pass
        self._admin_ctx = None
        self._admin_page = None
        self._admin_logged_in = False
        if self._session is not None:
            # 只用管理员的后台读取不会登销售；主会话若就是连接自己的账号（单凭据），也按短时长留。
            self._finish_session(self._session.page.context, self._session.page, self._username,
                                 self._logged_in, keep, idle=session_cache.sales_idle_seconds())
            self._session.__exit__(exc_type, exc, tb)
        self._session = None
        self._logged_in = False
        self._release_lock(exc_type, exc, tb)

    def _finish_session(self, context, page, username, logged_in, keep, *, idle) -> None:
        from pearnly_dms.adapter import session_cache

        if not logged_in:
            return
        key = self._key(username)
        if keep:
            try:
                if session_cache.save(key, context.cookies(), self.base_url, idle=idle):
                    return
            except Exception:
                logger.debug("DMS session cache save failed; signing out instead", exc_info=True)
        session_cache.forget(key)
        self._logout_page(page)

    def _release_lock(self, exc_type=None, exc=None, tb=None) -> None:
        if self._lock_cm is None:
            return
        try:
            self._lock_cm.__exit__(exc_type, exc, tb)
        finally:
            self._lock_cm = None
            self._lock_held = None
            self._admin_lock_held = None

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
        from pearnly_dms.adapter import session_cache

        if self.verify_password:
            # 验证账号密码时（连接向导、登记系统账号）必须真登：复用缓存的会话等于没验密码 ——
            # 2026-09-27 测试机实测，输错密码也"测试通过"了。缓存里还活着的旧会话先正常登出，
            # 否则这次真登会被 DMS 当成同账号重复登录。
            if session_cache.restore(self.page.context, self._sales_key(), self.base_url):
                self._logout_page(self.page)
                self.page.context.clear_cookies()
            session_cache.forget(self._sales_key())
            self._perform_login(self.page, self._username, self._password)
        elif session_cache.restore(self.page.context, self._sales_key(), self.base_url):
            self.session_reused = True
        else:
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
                raise DmsLoginPageUnreachableError(
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
        self._acquire_admin()
        secrets = [self._admin_password]
        if len(self._admin_username) >= 3:
            secrets.append(self._admin_username)
        self._admin_ctx, self._admin_page = self._session.new_isolated_page(extra_secrets=secrets)
        from pearnly_dms.adapter import admin_pool, session_cache

        if session_cache.restore(self._admin_ctx, self._key(self._admin_username), self.base_url):
            self._admin_logged_in = True
            return
        try:
            self._perform_login(self._admin_page, self._admin_username, self._admin_password)
        except DmsAuthError as exc:
            if self._admin_account is not None and self._admin_account.pooled:
                admin_pool.note_status(self._admin_username, "login refused")
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
