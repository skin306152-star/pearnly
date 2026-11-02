# -*- coding: utf-8 -*-
"""MR.ERP 会话（评估 §5、§14.3、§14.4；ADR-026 第 7、8 条）：开窗、登录、选账套、核对公司、识别被踢、录完退出登录并关窗。

- **按需登录**：批次开始时（``ensure_open``）只核对能不能开；真要用页面时（列表、表单、查询）才拉起 MR.ERP 桌面子进程、登录。
  这一种单还不能录（配方没核对）时根本不开页面、不登录；
- **登录只试一次**：先打开这个网址的登录页，确认当前页确实是这个网址、``login/login.php``、表单提交到 ``checklogin.php``，
  才从本机凭据（Windows 凭据管理器，目标名带网址与连接号）取出用户名和密码填进去；密码只交给页面那一格（不回读、不进日志、
  不进截图——登录页不截图、不进回执与批次文件）。MR.ERP 说用户名或密码不对 → 必须人工（``login_failed``），不再试（防锁账号）；
  这台电脑还没存登录 → ``login_missing``；
- **选账套**：在选账套页找这个账套（``mainmenu.php?comidyear=…&seldb=1`` 那条链接，文字就是账套名，11-02 真站点实测），
  核链接文字后点进去（页面原本的方式）；主菜单页正文是「Connect Database Server Error : Unknown database …」= 账套库不存在、
  打不开；再读页面底栏的公司全称与「Database Name」（待探路核对，读得到才核）。不符、这个账号看不到这个账套、账套打不开 →
  ``company_mismatch`` 整批停（账套打不开暂复用这个原因，细节写明）；
- **被踢**：MR.ERP 一个账号同时只能一处登录（``checkuseruse.php``：会话号对不上就 alert 后跳 ``logoutkick.php``；11-02 真站点实测
  旧会话下一次打开页面弹「"User" ดังกล่าวมีผู้ Login ซ้ำ…」后回到 ``login/login.php``，按这段文字加「回到登录页」判定）。录到一半
  被踢回登录页 → 这一批暂停、必须人工（暂用 ``login_failed``，细节写 ``kicked``），**不自动重新登录**（重登会把人踢掉，来回互踢）；
  被踢后关窗时也**不走退出登录**（``logout.php`` 会删掉这个账号的在用记录，把正在用的人也踢掉）；
- **录完**：正常退出登录（``login/logout.php``），关窗，子进程退出。
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from ..express_operator import credentials
from ..express_operator.engine import NeedHuman
from .recipe import Recipe
from .web_desk import DeskGone, PageError, ProcessDesk, WebPage

PREFIX = credentials.MRERP_PREFIX
KICKED = "kicked"


class Kicked(Exception):
    """被踢回登录页（账号在别处登录 / 会话没了）。"""


class NetworkDown(Exception):
    """页面没加载成功、超时、或子进程没了：交批次等网络恢复后续录（续录前先查这张存没存）。"""


def credential_target(url: str, connection: str) -> str:
    """一条 MR.ERP 连接一条本机凭据：与第 10 步 CC 授权存的同一个名字（``credentials.mrerp_target``：主机小写、不带端口，
    再加连接号），代录才读得到授权时存下的登录。网址或连接号不合规回空串（读不到凭据 = 没授权）。"""
    try:
        return credentials.mrerp_target(url, connection or "default")
    except ValueError:
        return ""


def footer_facts(text: str) -> Dict[str, str]:
    """业务页底栏（``component/php/footer.php``）：``Company : 全称``、``Database Name : 简称+年度``。"""
    flat = " ".join(str(text or "").split())
    company = re.search(r"Company :\s*(.*?)\s*(?:Database Name :|$)", flat)
    database = re.search(r"Database Name :\s*(.*?)\s*$", flat)
    return {"company": company.group(1).strip() if company else "", "database": database.group(1).strip() if database else ""}


def _same(a: str, b: str) -> bool:
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


class MrerpSession:
    def __init__(self, connection: Dict[str, str], recipe: Recipe, *,
                 desk_factory: Callable[[], object] = ProcessDesk,
                 read_secret: Callable[[str], Optional[Tuple[str, str]]] = credentials.read,
                 presence: Optional[Callable[[], bool]] = None, log: Callable[[str], None] = lambda _m: None,
                 load_s: Optional[float] = None):
        self.conn = dict(connection)
        self.base = str(self.conn.get("url") or "").rstrip("/")
        self.recipe = recipe
        self.desk_factory, self.read_secret = desk_factory, read_secret
        self.presence, self.log = presence, log
        self.load_s = load_s
        self.page: Optional[WebPage] = None
        self.ready = False
        self.kicked = False
        self.refused = False  # MR.ERP 说用户名或密码不对：这一批不再试
        self.where: Tuple[str, str] = ("", "")  # 当前停在哪个列表（模块、菜单号）：引擎接着用，不重开
        self.attached = False

    # ── 地址 ────────────────────────────────────────────────────────────
    @property
    def target(self) -> str:
        return credential_target(self.base, str(self.conn.get("connection") or ""))

    def url(self, path: str) -> str:
        return f"{self.base}/{str(path).lstrip('/')}"

    def _path(self, url: str) -> str:
        return urlsplit(str(url or "")).path.lower()

    def on_login_page(self, url: str) -> bool:
        path = self._path(url)
        return any(path.endswith("/" + p.strip("/").lower()) for p in self.recipe.login.get("login_paths", []))

    # ── 批次接口（``batch.Runner``）───────────────────────────────────────
    def ensure_open(self) -> None:
        """批次开始 / 网络恢复后：被踢过、登录被拒过的不再登录（必须人工）。真正登录等到要用页面时（``live``）。"""
        if self.kicked:
            raise NeedHuman("login_failed", f"{KICKED}: MR.ERP account is in use elsewhere; not logging in again")
        if self.refused:
            raise NeedHuman("login_failed", "MR.ERP refused the stored login")
        if not self.base:
            raise NeedHuman("unknown_screen", "no MR.ERP address in the job")

    def stage(self) -> str:
        return "work" if self.ready else "login"

    def close(self) -> None:
        """录完：退出登录（被踢过的不退，免得把正在用这个账号的人踢掉）、关窗、子进程退出。"""
        page, self.page = self.page, None
        if page is None:
            return
        try:
            if self.ready and not self.kicked:
                page.open(self.url(self.recipe.login["logout_page"]))
        except Exception:  # noqa: BLE001 — 退不掉也照样关窗
            pass
        finally:
            self.ready, self.where = False, ("", "")
            try:
                page.desk.close()
            except Exception:  # noqa: BLE001
                pass

    def release(self) -> None:
        self.close()

    def reachable(self) -> bool:
        """网络恢复没有：只读打开一次登录页（每分钟最多一次，由批次控制间隔）。"""
        try:
            with urllib.request.urlopen(self.url(self.recipe.login["page"]), timeout=10) as reply:
                return reply.status < 500
        except urllib.error.HTTPError as error:
            return error.code < 500
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def human(self) -> Dict:
        """人在 MR.ERP 窗口里的输入计数（``presence`` 用）；窗口没开回空。"""
        if self.page is None:
            return {}
        reply = self.page.desk.call("state", timeout=10, drain=False)
        return reply if reply.get("ok") else {}

    # ── 页面 ────────────────────────────────────────────────────────────
    def live(self) -> WebPage:
        """要用页面了：没登录就登录、选账套、核对公司。"""
        self.ensure_open()
        if self.page is None or not self.ready:
            self._login()
        return self.page

    def goto(self, path: str) -> Dict:
        """打开站内一页并检查：网络断 → ``NetworkDown``；被踢 → ``Kicked``。"""
        page = self.live()
        try:
            state = page.open(self.url(path))
        except DeskGone as error:
            raise NetworkDown(str(error)) from None
        self.inspect(state)
        return state

    def inspect(self, state: Dict) -> None:
        if not state.get("last_ok", True) or state.get("timeout"):
            raise NetworkDown(f"page did not load: {self._path(state.get('url'))[:80]}")
        for dialog in self.page.dialogs if self.page else []:
            rule = self.recipe.match_dialog(dialog.get("text", ""))
            if rule and rule.get("outcome") == "kicked":
                self.kicked, self.ready = True, False
                raise Kicked(dialog.get("text", "")[:200])
        if self.ready and self.on_login_page(state.get("url", "")):
            self.kicked, self.ready = True, False
            raise Kicked("back on the login page")

    def _start(self) -> WebPage:
        desk = self.desk_factory()
        try:
            desk.start()
        except DeskGone as error:
            raise NeedHuman("unknown_screen", f"MR.ERP window did not start: {error}") from None
        kwargs = {"load_s": self.load_s} if self.load_s else {}
        return WebPage(desk, presence=self.presence, **kwargs)

    def _login(self) -> None:
        login = self.recipe.login
        if self.page is None:
            self.page = self._start()
        page = self.page
        try:
            state = page.open(self.url(login["page"]), base=self.base)
        except DeskGone as error:
            raise NeedHuman("network_down", f"MR.ERP login page: {error}") from None
        if not state.get("last_ok", True) or state.get("timeout"):
            raise NeedHuman("network_down", "MR.ERP login page did not load")
        # 先确认这就是这个网址的登录页、表单提交到 MR.ERP 自己的 checklogin，才取凭据
        here = urlsplit(state.get("url", ""))
        form = page.form_of(login["password_id"]) or {}
        if (here.netloc.lower() != urlsplit(self.base).netloc.lower() or not self.on_login_page(state.get("url", ""))
                or not str(form.get("action") or "").endswith(login.get("form_action", "checklogin.php"))
                or any(v is None for v in page.read([login["user_id"], login["password_id"]]).values())):
            raise NeedHuman("unknown_screen", "not the MR.ERP login page")
        stored = self.read_secret(self.target)
        if not stored:
            raise NeedHuman("login_missing", "this computer has no MR.ERP login for this connection")
        user, secret = stored
        try:
            page.fill(login["user_id"], user)
            page.fill(login["password_id"], secret, secret=True)
        finally:
            secret = ""  # noqa: F841 — 用完不留
        before = page.state()["loads"]
        page.click(login["submit_id"])
        select = "/" + login["select_page"].strip("/").lower()

        def landed():
            st = page.state()
            if any((self.recipe.match_dialog(d.get("text", "")) or {}).get("outcome") == "login_failed"
                   for d in page.dialogs):
                return "refused"
            if st["loads"] > before and not st["loading"] and self._path(st["url"]).endswith(select):
                return "select"
            if not st.get("last_ok", True):
                return "down"
            return ""

        found = page.wait_for(landed, page.load_s)
        if found == "refused":
            page.dialogs.clear()
            self.refused = True
            raise NeedHuman("login_failed", "MR.ERP refused the stored login")
        if found == "down":
            raise NeedHuman("network_down", "MR.ERP did not answer the login")
        if found != "select":
            raise NeedHuman("login_failed", "no account-set list after login")
        self._select(page)

    def _select(self, page: WebPage) -> None:
        login = self.recipe.login
        link = login["account_link"].replace("{comidyear}", str(self.conn.get("comidyear") or ""))
        if not self.conn.get("comidyear") or page.count(link) < 1:
            raise NeedHuman("company_mismatch", "the account set is not offered to this MR.ERP user")
        # 链接文字就是账套名（11-02 真站点实测，例如 TEST2019）：先核这个，点进去再核底栏
        name = str(self.conn.get("name") or "")
        shown = page.text(link, limit=200).strip()
        if name and not _same(shown, name):
            raise NeedHuman("company_mismatch", f"account set link reads {shown[:40]!r}")
        before = page.state()["loads"]
        page.click(selector=link)
        main = "/" + login["main_page"].strip("/").lower()
        state = page.wait_for(lambda: (lambda st: st if st["loads"] > before and not st["loading"]
                                       and self._path(st["url"]).endswith(main) else None)(page.state()), page.load_s)
        if not state:
            raise NeedHuman("company_mismatch", "did not reach the main menu of the account set")
        self.ready = True
        self.inspect(state)
        body = page.text("", limit=4000)
        missing = str(login.get("account_set_missing") or "")
        if missing and missing in body:
            # 账套库不存在（11-02 真站点实测：主菜单页正文「Connect Database Server Error : Unknown database '<库名>'」）：
            # 这个账套打不开，整批停。暂用已有的 company_mismatch（整批停、要人看），细节写清楚
            self.ready = False
            found = re.search(r"Unknown database '([^']*)'", body)
            raise NeedHuman("company_mismatch", f"account set cannot be opened: unknown database {found.group(1)[:60] if found else ''}")
        facts = footer_facts(page.text(login["footer"]))
        company = str(self.conn.get("company_name") or "")
        # 底栏（待探路核对）：读得到才核；读不到不拦（账套名已按链接文字核过）
        if facts["database"] and ((name and not _same(facts["database"], name))
                                  or (company and facts["company"] and not _same(facts["company"], company))):
            self.ready = False
            raise NeedHuman("company_mismatch", f"page shows {facts['database'][:40]!r} / {facts['company'][:60]!r}")
        self.log(f"mrerp account set ready: {facts['database'][:40]}")
