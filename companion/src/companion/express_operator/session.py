# -*- coding: utf-8 -*-
"""开 Express、自动登录、确认开的是这家公司的这本账套、录完正常退出（占一个授权名额，录完即释放）。

- 只启动配置里的服务器版 Express 程序（``express_exe``，与直写同一组授权）；
- 登录：配方里的登录窗口样子核过（``login.verified``）才自动填；没核过、或这台电脑还没授权登录 → 必须人工
  （login_missing，请人先在 CC 里授权登录 / 自己打开 Express）；自动登录只试一次，失败就停（login_failed，防锁账号）；
- 公司核对：Express 开着这本账套时独占着它的 ISRUN（``account_lock.is_account_busy``，和直写判断「Express 开着账套」同一条），
  再加上 Express 窗口文字里能找到公司名就更稳；对不上整批停（company_mismatch），不在别的公司里录；
- 退出：给主窗口发 WM_CLOSE（试点收尾实测正常退出），等窗口没了。
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path, PureWindowsPath
from typing import Callable, Dict, Iterable, Optional, Tuple

from . import credentials
from .engine import NeedHuman
from .recipe import Recipe


def _busy(account_dir) -> bool:
    from ..account_lock import is_account_busy

    return is_account_busy(account_dir)


OPEN_SECONDS = 90  # 开到这家公司的总时限（启动、登录、选公司、工作日期框）
# 状态栏：「公司名 (账套路径) by 用户」（10-26 实机，69EXP 服务器版）
# 状态栏「公司名 (账套路径) by 用户」：账套路径取**最后一对**括号（路径里没有括号）。公司名本身常带括号（「…(ไทยแลนด์) จำกัด」
# 「…(ประเทศไทย)…」），以前取第一对，把公司名里的括号当成路径，这类公司整批停在 company_mismatch（11-18 实机）
_STATUS = re.compile(r"^(.+)\s*\(([^()]+)\)\s*by\s+\S.*$")


def _plain(text) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def _bare(name: str) -> str:
    """COMPNAM 去掉公司列表排序用的前缀（「9.บริษัท Pearnly จำกัด」→「บริษัท Pearnly จำกัด」）。"""
    return re.sub(r"^\s*[0-9A-Za-z]{1,3}\.\s*", "", str(name or ""))


def _norm_path(path) -> str:
    return str(path or "").replace("/", "\\").rstrip("\\").strip().upper()


def _share_path(path: str) -> str:
    from ..device_facts import share_path

    return share_path(path)


class Session:
    def __init__(self, desk, recipe: Recipe, *, express_exe: str, account_dir: str, company_name: str,
                 credential_target: str, account_open: Callable[[str], bool] = _busy,
                 launch: Optional[Callable[[str], None]] = None, sleep: Callable[[float], None] = time.sleep,
                 registry: Optional[Callable[[], Tuple[int, str]]] = None,
                 on_claim: Callable[[int], None] = lambda _pid: None, on_gone: Callable[[int], None] = lambda _pid: None,
                 share: Optional[Callable[[str], str]] = None, own_pids: Callable[[], Iterable[int]] = lambda: ()):
        self.desk, self.recipe = desk, recipe
        self.share = share or _share_path
        self.registry = registry or self._registry_target
        self.picked: Dict = {}
        self.express_exe, self.account_dir, self.company_name = express_exe, account_dir, company_name
        self.credential_target = credential_target
        self.account_open = account_open
        self.launch = launch or self._launch
        self.sleep = sleep
        self.opened_by_us = False
        self.leave_open = False  # 停在公司列表请人选：收尾不退出 Express
        self.pid: Optional[int] = None
        self.attached = False  # 开录前核对过公司、接管了 Express：录完才由我们退出
        # 宿主在本机记下「代录开 / 接管的 Express」（进程号），正常退出后删掉；CC 重启后据此认出上次留下的（host.recover_orphans）
        self.on_claim, self.on_gone = on_claim, on_gone
        # CC 自己启动过的 Express 进程号（宿主本机记下的）：只认这些，人自己开的 Express 一律不碰（主会话 10-30）
        self.own_pids = own_pids

    @staticmethod
    def _launch(exe: str) -> int:
        return subprocess.Popen([exe], cwd=os.path.dirname(exe)).pid  # noqa: S603 — 只启动配置里的 Express 程序

    def _windows(self):
        if self.pid is None and hasattr(self.desk, "instances"):
            return []  # 还没绑定 CC 自己的 Express：这台电脑上开着的都是人的，当作没开（另开自己的）
        return self.desk.act([{"do": "windows"}], windows=False)["results"][0].get("windows", [])

    def _main(self) -> Optional[Dict]:
        mains = [w for w in self._windows() if not w.get("owned")]
        return max(mains, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1]), default=None)

    def seen_text(self) -> str:
        main = self._main()
        return " ".join([main.get("title", "")] + main.get("texts", [])) if main else ""

    def program_ok(self) -> bool:
        """开着的这个 Express 是不是配置里那套程序（同一个年度文件夹）：登录、选公司之前先核，对不上一个键都不按（10-29 实机：
        桌面上另一套 70EXP 的 Express 开着，公司列表是那套的，按这套登记表的行号选会进错账套）。没配置程序、窗口没带程序路径时
        不在这里拦，进公司后照样由 :meth:`company_ok` 逐字核对状态栏。"""
        want = _norm_path(PureWindowsPath(self.express_exe).parent.name) if self.express_exe else ""
        if not want:
            return True
        seen = {_norm_path(PureWindowsPath(w["exe"]).parent.name) for w in self._windows() if w.get("exe")}
        return not seen or seen == {want}

    def company_ok(self) -> bool:
        """进的是不是这家公司的这本账套：只认 Express 状态栏「公司名 (账套路径) by 用户」（10-26 实机），公司名与账套路径逐字核对
        （主会话 10-27：对不上立刻退出、一个字段都不写）。读不到状态栏、格式认不出都算对不上。"""
        found = _STATUS.match(_plain(self.status_line()))
        if not found:
            return False
        name, path = found.group(1).strip(), found.group(2).strip()
        if not self.same_place(path, self.account_dir):
            return False
        # 公司列表名去掉排序前缀（「9.」）比；名字本身以「J.P.」这类缩写打头时去前缀会去错，所以原样的也认
        picked = self.picked.get("name", "")
        names = {_plain(n) for n in (self.company_name, _bare(picked), picked) if _plain(n)}
        return not names or _plain(name) in names

    def same_place(self, a: str, b: str) -> bool:
        """两个写法是不是同一个账套目录：字面相同，或换成网络路径后相同（10-29 实机：从映射盘 P: 开的 Express，状态栏写
        「P:\\69EXP\\TEST」，任务配的是 \\\\accserver\\ACCOUNT\\69EXP\\test）。映射盘按本机的映射换算（同多台电脑认账套那一套），
        换不出来就只认字面。"""
        if _norm_path(a) == _norm_path(b):
            return True
        unc_a, unc_b = (_norm_path(self.share(str(x or ""))) for x in (a, b))
        return bool(unc_a) and unc_a == unc_b

    def status_line(self) -> str:
        """主窗口状态栏第一格（windows() 把状态栏的字放在最前面）。"""
        main = self._main()
        texts = main.get("texts", []) if main else []
        return next((t for t in texts if _STATUS.match(_plain(t))), "")

    # ── 开到这家公司：按画面所处的那一步走（登录窗 → 公司列表 → 工作日期框 → 主窗口）───────────────────────
    def stage(self) -> str:
        """现在停在哪一步：none（Express 没开）/ login / company / work_date / main。认法写在配方 ``session`` 里。"""
        windows = self._windows()
        if not windows:
            return "none"
        marks = self.recipe.data.get("session") or {}
        texts = [" ".join([w.get("title", "")] + w.get("texts", [])).replace("&", "") for w in windows]
        # 先认后面的步骤：登录窗正在关、公司列表已经弹出来的那一下两个都在（10-27 实机），这时已经登录过了
        if marks.get("work_date") and any(marks["work_date"] in text for text in texts):
            return "work_date"
        if marks.get("company_list") and any(all(m in text for m in marks["company_list"]) for text in texts):
            return "company"
        login = self.recipe.login.get("title_contains")
        if login and any(login in w.get("title", "") for w in windows):
            return "login"
        return "main"

    def _bind_existing(self) -> None:
        """多个代录会话并行（§15.1）：还没绑定进程时，从 CC 自己启动过、还开着的 Express 里认出这本账套的那一个（状态栏写着
        账套路径）绑定它；别的会话已经绑定的不认。人自己开的 Express（同一账套也一样）一律不认、不接管（主会话 10-30）：
        认不出就不绑，下面按「没开」启动 CC 自己的。"""
        if not hasattr(self.desk, "instances") or getattr(self.desk, "pid", None):
            return
        own = {int(pid) for pid in self.own_pids()}
        folder = str(self.account_dir).rstrip("\\/").upper()
        free = [w for w in self.desk.instances() if w["pid"] not in claimed() and int(w["pid"]) in own]
        for w in free:
            text = " ".join([w.get("title", "")] + w.get("texts", [])).replace("\xa0", " ").upper()
            shown = [m.group(2).strip() for m in (_STATUS.match(_plain(x)) for x in w.get("texts", [])[:1]) if m]
            if (folder and folder in text.replace("/", "\\")) or any(self.same_place(p, self.account_dir) for p in shown):
                self._claim(w["pid"])
                return

    def _claim(self, pid: int) -> None:
        with _CLAIM_LOCK:
            _CLAIMED.add(int(pid))
        self.desk.bind(pid)
        self.pid = int(pid)
        self.on_claim(self.pid)

    def release(self) -> None:
        if self.pid is not None:
            with _CLAIM_LOCK:
                _CLAIMED.discard(self.pid)
            self.pid = None

    def ensure_open(self) -> None:
        """把 Express 开到这家公司的这本账套：没开就启动；停在登录窗就自动登录（本人授权过才行）；停在公司列表就选这家
        （配方核过才行，没核过请人选）；工作日期框按「ตกลง」；最后核对公司。开着别的公司 → company_mismatch（不碰）。"""
        self._bind_existing()
        waited = 0  # 按等过的秒数算时限（测试里 sleep 是假的）；每一步本身的等待在各自方法里
        while waited < OPEN_SECONDS:
            stage = self.stage()
            if stage == "main":
                if self.company_ok():
                    self.attached = True
                    return
                if self._starting():
                    # 刚启动：主框架先出来、还没有状态栏字，登录窗约 1 秒后才弹（10-27 实机）——不是开着别的公司，接着等
                    self.sleep(1)
                    waited += 1
                    continue
                raise NeedHuman("company_mismatch", f"{self.status_line() or self.seen_text()} | {self.picked}"[:300])
            if stage == "none":
                self._start_express()
            elif stage in ("login", "company") and not self.program_ok():
                exe = next((w["exe"] for w in self._windows() if w.get("exe")), "")
                raise NeedHuman("company_mismatch", f"program {exe}"[:300])
            elif stage == "login":
                self._login(self.recipe.login)
            elif stage == "company":
                self._pick_company()
            elif stage == "work_date":
                # 进公司后 Express 问工作日期（默认今天）：按「ตกลง」（10-26 实机）
                self.desk.act([{"do": "dialog_button", "text": "ตกลง"}], windows=False)
                self.sleep(2)
            waited += 2
        # 一直没走到这家公司的主窗口（卡在某一步）：如实报认不出的画面，不说成「开着别的公司」
        raise NeedHuman("unknown_screen", f"{self.stage()}: {self.seen_text()}"[:300])

    def _starting(self) -> bool:
        """Express 还在启动：主框架是空的（没有状态栏、没有任何控件字；登录窗弹没弹都算）。开着某家公司时状态栏一定有字。"""
        mains = [w for w in self._windows() if not w.get("owned")]  # 只读一次：登录窗随时会弹出来
        return bool(mains) and not any(w.get("texts") for w in mains)

    def _start_express(self) -> None:
        login = self.recipe.login
        if not login.get("verified") or not credentials.exists(self.credential_target):
            raise NeedHuman("login_missing", "auto login not set up")
        if not (self.express_exe and Path(self.express_exe).is_file()):
            raise NeedHuman("login_missing", "Express program not found")
        pid = self.launch(self.express_exe)
        self.opened_by_us = True
        if pid and hasattr(self.desk, "bind"):
            self._claim(pid)  # 这个会话自己开的 Express：只认它
        if not self._wait(lambda: self.stage() != "none", 60):
            raise NeedHuman("login_failed", "Express did not start")

    def _pick_company(self) -> None:
        """公司列表里选这家（配方 ``session.company_pick``，主会话 10-27 定、10-30 改）：按账套路径在登记表里定的行号移过去
        （绝不在默认第一行上按确定），按确定；进去后由 :meth:`company_ok` 逐字核对状态栏的公司名与账套路径，对不上就退出。
        配方没核过、或登记表里找不到这本账套：Express 留在公司列表上请人选（不退出），人选好后点「继续」。"""
        pick = (self.recipe.data.get("session") or {}).get("company_pick") or {}
        row, name = self.registry() if pick.get("verified") else (0, "")
        if not row:
            self.leave_open = True
            raise NeedHuman("company_pick", "company list: pick by hand")
        # 只按行号选，不用 Express 的「ค้นหา」查名字：它从选中行往下找名字以它开头的下一行，公司名带同样前缀（分公司、新旧年度、
        # 复制出来的 TEST2）时会跳到别家（10-30 实机）。行号由账套路径在登记表里定，排法同 Express 的索引（account_probe）
        reply = self.desk.act([{"do": "company_pick", "row": row, "name": "",
                                "search_button": pick.get("search_button", "ค้นหา")}], windows=False)
        self.picked = {**(reply["results"][0] if reply.get("results") else {}), "name": name}
        self.sleep(2)

    def _registry_target(self) -> Tuple[int, str]:
        """这本账套在公司列表里的行号与显示名（Express 公司登记表 SCCOMP：表按 COMPNAM 升序，行号 = CC 的 account_set_row）。
        登记表在 Express 程序所在的年度文件夹（同 CC 选账套时读的那一份）；读不到、没登记回 (0, "")。"""
        from ..account_probe import probe_account_sets

        root = str(Path(self.express_exe).parent) if self.express_exe else ""
        try:
            sets = probe_account_sets(root, include_details=False) if root else []
        except Exception:  # noqa: BLE001 — 读不到登记表：请人选
            return 0, ""
        for item in sets:
            if self.same_place(item.path, self.account_dir):
                return int(item.row or 0), str(item.name or "")
        return 0, ""

    def _wait(self, ok: Callable[[], bool], seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if ok():
                return True
            self.sleep(1)
        return ok()

    def _login(self, login: Dict) -> None:
        """登录窗（「เข้าสู่ระบบ/LogIn」，10-27 实机）：按标签找到用户名格、密码格直接填，按「OK」。只试一次，失败就停（防锁账号）。
        用户名密码只从这台电脑的凭据管理器取，填完马上丢掉引用；回复里没有内容。"""
        found = credentials.read(self.credential_target)
        if found is None:
            raise NeedHuman("login_missing", "no stored login")
        user, password = found
        try:
            reply = self.desk.act([{"do": "login_fill", "title": login["title_contains"],
                                    "user_label": login["user_label"], "password_label": login["password_label"],
                                    "button": login.get("button", "OK"), "user": user, "password": password}],
                                  windows=False)
        finally:
            user = password = ""  # noqa: F841
        if not all(r.get("ok") for r in reply["results"]):
            raise NeedHuman("login_failed", "login window not as recorded")
        # 按了 OK：服务器版验密码要几秒（10-27 实机固定等 3 秒不够）——等登录窗关掉；Express 弹了提示（如密码不对）就停，
        # 记下提示的字（只记弹窗文字，不记任何输入框）。只试这一次，不重填（防锁账号）。
        # 只按「还在不在登录这一步」判：登录成功时公司列表会在登录窗关掉前后弹出来，那一下不能当成出错提示（10-27 实机）；
        # 等满了还在登录这一步才算失败，这时才去读 Express 弹的提示
        waited = 0
        while waited < int(login.get("wait_s", 20)):
            if self.stage() != "login":
                return
            self.sleep(1)
            waited += 1
        said = self._login_message(login["title_contains"])
        raise NeedHuman("login_failed", ("Express: " + said)[:300] if said else "login window still open")

    def _login_message(self, login_title: str) -> str:
        """登录窗之外 Express 弹出的提示框的字（按了 OK 之后，例如密码不对）。"""
        for w in self._windows():
            if w.get("owned") and login_title not in w.get("title", ""):
                text = " ".join(t for t in w.get("texts", []) if not t.startswith("&")).strip()
                if text:
                    return text
        return ""

    def close(self) -> None:
        """正常退出 Express，释放授权名额。只退核对过公司、由批次接管或启动的那个；公司对不上时那是别人在用的，不碰。"""
        if self.leave_open or not (self.attached or self.opened_by_us) or self._main() is None:
            self.release()
            return
        self.desk.act([{"do": "close_express"}], windows=False)
        self._wait(lambda: self._main() is None, 30)
        if self.pid is not None and self._main() is None:
            self.on_gone(self.pid)
        self.release()

    def leave_unopened(self) -> bool:
        """收拾上次 CC 留下、还没进公司的 Express（主会话 10-27）：登录窗、公司列表按取消关掉（这时什么都没写），空的主框架
        正常退出。回是否已退出。"""
        cancel = {"login": self.recipe.login.get("cancel", "Cancel"), "company": "ยกเลิก"}
        for _ in range(6):
            stage = self.stage()
            if stage not in cancel:
                break
            try:
                self.desk.act([{"do": "dialog_button", "text": cancel[stage]}], windows=False)
            except Exception:  # noqa: BLE001 — 按不到就照下面整个退出
                break
            self.sleep(1)
        if self._main() is not None and not self.status_line():  # 没进公司（状态栏没有公司）才直接退出
            self.desk.act([{"do": "close_express"}], windows=False)
            self._wait(lambda: self._main() is None, 30)
        gone = self._main() is None
        if gone and self.pid is not None:
            self.on_gone(self.pid)
        return gone


_CLAIMED: set = set()
_CLAIM_LOCK = threading.Lock()


def claimed() -> set:
    """各会话已经绑定的 ExpressI 进程号（同一台电脑上多个代录会话并行时，互不认对方的 Express）。"""
    with _CLAIM_LOCK:
        return set(_CLAIMED)
