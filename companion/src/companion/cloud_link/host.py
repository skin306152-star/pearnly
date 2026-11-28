# -*- coding: utf-8 -*-
"""云端连接宿主（桥这一侧，评估第 4.2–4.3 节）：桥进程里每条云端连接一个，把连接窗口、隧道盘、会话交接、中转线程串起来。

``tomo_bridge`` 在人点「设置 → 连接 → 云端 Express → 连接」并存好连接后 :meth:`CloudHost.start`；「断开连接」先
:meth:`CloudHost.stop` 再删连接；人把连接窗口关掉 = 断开，宿主随之 stop。CC 启动时存有连接与密码就由 ``tomo_bridge.cloud_autoconnect`` 自动起（D5）。

- 隧道根目录 ``config.CLOUD_TUNNEL_DIR\\<主机>\\``（``%LOCALAPPDATA%\\Pearnly\\cloud-tunnel\\<主机>``），下有 ``in``、``out``、
  ``ctl``；只在 start 时建。
- 隧道盘：``subst <盘符>: <根目录>``（``%SystemRoot%\\System32\\subst.exe``，参数列表、不经 shell、``CREATE_NO_WINDOW``）。
  盘符先用 ``LinkStore`` 里记下的那个（``CloudLink.tunnel_drive``），被占了就从 Z 往 D 找第一个空的并记下；记下的盘符已经
  映射到本隧道根目录（上次 CC 没正常退出）就接着用。stop 时 ``subst <盘符>: /D``——先用 ``QueryDosDevice`` 核对这个盘
  确实映射到本隧道根目录才撤，不是自己的一概不碰。连接窗口只把这一个盘交给云端（``nonscriptable.for_widget``）。
- 会话交接：每次起会话（窗口每次 ``Connect`` 前）桥生成新的 ``tunnel_protocol.Session``（epoch + 32 字节密钥），写
  ``ctl\\session.json``（先写 ``.part`` 再改名，``encode_session``）。云端读完删除；会话结束（断开、等重连、停住）与 stop 时
  桥也删。连接窗口没有「Connect 前」的回调，这里借 ``password_provider``：窗口每次起连接只调它一次，取到密码后、交给控件前
  换会话（start 时已换过一次，第一次连接沿用）。日志里只有主机与盘符，绝不出现密钥。
- 中转：一个 ``TunnelRelay`` 处理 ``in\\`` 里的所有请求，按请求的方法路径选站点（``route``）。协议里没有「站点」这一项，
  但两种通道的方法路径前缀不同（``channel.PATHS``：Cowork ``pearnly_cowork.companion.*``、ERP
  ``pearnly_core.cc_desktop.machine.*``），桥按这台云端电脑在各站点登记的令牌所属通道对上站点；令牌每个请求现取（start 之后
  才登记的站点也能用）。**限制**：同一通道登记了不止一个站点时（例如两个 Cowork 站点）只用第一个，其余站点的请求发不到——
  要分站点得改协议，本次不改。多个 ``TunnelRelay`` 同读一个 ``in\\`` 会抢同一个请求再发错站点，所以不按站点各起一个。
  后台线程平时阻塞在目录监视上（``dir_watch``：Windows 用 ``ReadDirectoryChangesW`` 监视隧道根目录含子目录的文件名变化），
  有变化才看一遍 ``in\\`` 与 ``ctl\\``，空闲时不做任何处理（CC 桌面版要轻量，空闲 CPU 近 0）；另每 60 秒兜底扫描一次（防漏通知，
  远程桌面重定向盘 / subst 盘上的通知要 Windows 真机实测）并清理没人取的文件；兜底删、配对过期这类定时事到点才醒。
  监视打不开时退回每 0.5 秒看一遍。请求交给几条工作线程并发处理（站点慢的请求不挡别的）。
- 代录凭据交接（``cred_handoff``，做法页 11-16 第二版）：中转每看一遍顺带看 ``ctl\\``——``cred-req.json`` 用当前会话号与
  ``LinkStore.device_key`` 校验，本机存有 ``Pearnly CC/cloud-express/<主机>/<连接>`` 才加密写 ``cred-resp.json``，10 秒后兜底再删；
  ``request_forget`` 写 ``cred-forget.json``，收到云端回执才算完，没收到的在下次换会话时按新会话号补发（只在内存，桥重启即丢）。
  已见的请求编号放内存。
- 成员下发（D3，``ctl_files``）：每次换会话后、对话页交回新站点令牌后（``tomo_bridge._cloud_token``），把这台云端电脑登记了的
  站点、通道、端点号（不含令牌）用当前会话密钥加密写 ``ctl\\members.json``（:meth:`CloudHost.publish_members`）；云端据此建成员。
- 设备密钥的一次性配对（``device_pairing``）：人在设置里点「配对」→ :meth:`CloudHost.begin_pairing` 生成一次性令牌，只回给可见
  界面显示（不进日志）；配对进行中连接窗口标题改成「窗口标题常量 · 配对」（``i18n`` 已有词条 ``btn_pair``），结束（成功、失败、
  过期）改回。中转循环每一遍顺带看 ``ctl\\pair-req.json`` / ``pair-ack.json``；回应写 ``pair-resp.json``，10 秒后兜底再删。
  成功 / 失败都写本地日志（事件、原因码；时间由日志带）。
- 连接窗口：``window.CloudWindow`` + ``ConnectParams.make(..., tunnel_drive=盘符)`` + ``link_store.password_provider`` +
  ``nonscriptable.for_widget()``，状态变化转给上层的 ``on_change``。

不是 Windows、没有 PySide6 或 comtypes（``window.available()`` 为假）时 :meth:`start` 不做任何事，抛 :class:`HostUnavailable`。
所有 Windows 专属调用（subst、盘符、窗口、代发客户端、线程）都可注入，Mac 上离线测。还没在 Windows 真机上实测。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from .. import channel as channels
from ..logger import get_logger
from . import cred_handoff as handoff
from . import device_pairing as pairing
from . import dir_watch
from . import rdp
from . import tunnel_protocol as protocol
from .session import State

log = get_logger(__name__)

# 隧道盘从 Z 往 D 找
LETTERS = "ZYXWVUTSRQPONMLKJIHGFED"
FALLBACK_SECONDS = 60.0   # 兜底扫描（防漏通知）的间隔
CLEANUP_SECONDS = 60.0
UNINSTALL_SECONDS = 20.0   # 「断开」时等云端删安装的回执（云端靠目录通知看到，兜底 60 秒；超了照常断开）
WORKERS = 4
SUBST_TIMEOUT = 15
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
# 会话结束：不在连、不在已连上（断开、等重连、停住）
_LIVE = (State.CONNECTING, State.CONNECTED)


class HostUnavailable(RuntimeError):
    """这台电脑起不了云端连接（不是 Windows、没有 PySide6 或 comtypes）。start 前抛，没有任何副作用。"""


class HostError(RuntimeError):
    """起云端连接失败（找不到连接配置等）。文本不带密码、令牌、密钥。"""


class TunnelDriveError(HostError):
    """隧道盘映射不上（没有空盘符、subst 失败）。"""


# ── 纯函数 ──────────────────────────────────────────────────────────────────

def tunnel_root(host: str, base: Optional[Path] = None) -> Path:
    """这条云端连接的隧道根目录。"""
    if base is None:
        from ..config import CLOUD_TUNNEL_DIR

        base = CLOUD_TUNNEL_DIR
    return Path(base) / rdp.host(host)


def release_stale_drive(host: str, letter: str, *, drives=None, base: Optional[Path] = None) -> bool:
    """CC 上次被强行结束（任务管理器、崩溃、安装覆盖）时 subst 盘没撤、一直留到注销：这次启动不连（没存密码 / 被挤下线）或
    没连着就断开时撤掉。只撤指向这条连接隧道根目录的那个盘符；不是 Windows、读不到、指向别处都不碰。回撤没撤。"""
    letter = str(letter or "").strip().upper()
    if len(letter) != 1 or letter not in LETTERS:
        return False
    try:
        drives = drives if drives is not None else WindowsDrives()
        if not same_path(drives.target(letter), tunnel_root(host, base)):
            return False
        drives.remove(letter)
    except Exception as exc:  # noqa: BLE001 - 撤不掉只是盘符留到注销
        log.info("撤残留隧道盘失败 · %s: %s", letter, type(exc).__name__)
        return False
    log.info("撤掉上次残留的隧道盘 · 主机=%s 隧道盘=%s:", host, letter)
    return True


def same_path(a, b) -> bool:
    """Windows 路径比对（不分大小写、``/`` 当 ``\\``、去掉 ``\\\\?\\`` 前缀与末尾分隔符）。"""
    def norm(value) -> str:
        text = str(value or "").replace("/", "\\")
        for prefix in ("\\??\\", "\\\\?\\"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        return text.rstrip("\\").lower()

    return bool(norm(a)) and norm(a) == norm(b)


def candidates(remembered: str, used) -> list:
    """可试的盘符：记下的那个没被占就排第一，其余从 Z 往 D 没被占的。"""
    taken = {str(letter).upper() for letter in used}
    order = ([remembered] if remembered else []) + [letter for letter in LETTERS if letter != remembered]
    return [letter for letter in order if letter not in taken]


def channel_of_path(path: str) -> str:
    """方法路径属于哪种通道（``channel.PATHS``）；不认识回空串。"""
    return next((name for name, paths in channels.PATHS.items() if path in paths.values()), "")


# ── 盘符与 subst（Windows） ───────────────────────────────────────────────────

def _subst_exe() -> str:
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "subst.exe")


def run_subst(args: list) -> None:
    """跑一次 subst：参数列表、不经 shell、不闪控制台窗；非零退出抛 :class:`TunnelDriveError`（文本只带退出码）。"""
    completed = subprocess.run([_subst_exe(), *args], shell=False, capture_output=True, timeout=SUBST_TIMEOUT,
                               creationflags=_NO_WINDOW if sys.platform == "win32" else 0, check=False)
    if completed.returncode != 0:
        raise TunnelDriveError(f"subst exit {completed.returncode}")


class WindowsDrives:
    """真实的盘符操作。``run``：执行 subst 的函数（收参数列表，不含 subst.exe 本身），测试注入。"""

    def __init__(self, run: Callable[[list], None] = run_subst) -> None:
        self._run = run

    def used(self) -> set:
        """已占用的盘符（``GetLogicalDrives``，含网络盘与别的 subst 盘）。"""
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
        return {chr(ord("A") + bit) for bit in range(26) if mask & (1 << bit)}

    def target(self, letter: str) -> Optional[str]:
        """这个盘符 subst 到哪个目录（``QueryDosDevice`` 回 ``\\??\\<路径>`` 的才是 subst）；不是 subst 盘回 None。"""
        import ctypes

        buffer = ctypes.create_unicode_buffer(1024)
        if not ctypes.windll.kernel32.QueryDosDeviceW(f"{letter}:", buffer, len(buffer)):  # type: ignore[attr-defined]
            return None
        value = buffer.value
        return value[4:] if value.startswith("\\??\\") else None

    def add(self, letter: str, root: Path) -> None:
        self._run([f"{rdp.drive(letter)}:", str(root)])

    def remove(self, letter: str) -> None:
        self._run([f"{rdp.drive(letter)}:", "/D"])


# ── 默认的窗口与代发客户端 ───────────────────────────────────────────────────

def _default_available() -> bool:
    from . import window

    return sys.platform == "win32" and window.available()


def _default_forward():
    """桥上的代发客户端：保持连接、不跟随重定向；超时按每个请求自己带的（``tunnel_relay.httpx_forward``）。"""
    from .. import http_tls, net
    from .tunnel_relay import httpx_forward

    # 桥本身从不设隧道 transport（只有云端模式才设），这里经 net.client 照常直连站点
    client = net.client(verify=http_tls.context(), limits=http_tls.limits(), follow_redirects=False)
    return client, httpx_forward(client)


def _qt_window(params, *, password_provider, nonscriptable, on_change, on_closed):
    """真的连接窗口；另装一个关窗监视，窗口关掉（``closeEvent`` 做完断开）后回到事件循环再叫 ``on_closed``。"""
    from PySide6.QtCore import QEvent, QObject, QTimer

    from . import window as cloud_window

    class _CloseWatch(QObject):
        def eventFilter(self, watched, event):  # noqa: N802 - Qt 命名
            if event.type() == QEvent.Type.Close:
                QTimer.singleShot(0, on_closed)
            return False

    win = cloud_window.CloudWindow(params, password_provider=password_provider, nonscriptable=nonscriptable,
                                   on_change=on_change)
    watch = _CloseWatch(win)
    win.installEventFilter(watch)
    return win


def _default_nonscriptable():
    from . import nonscriptable

    return nonscriptable.for_widget()


# ── 宿主 ───────────────────────────────────────────────────────────────────

class CloudHost:
    """一条云端连接的宿主。``links()`` 每次现读本机的云端连接（``LinkStore``，与对话页那边各读各的，不留旧副本）。"""

    def __init__(self, host: str, *, links: Callable[[], Any],
                 on_change: Optional[Callable[[Any, str], None]] = None,
                 base_dir: Optional[Path] = None,
                 drives: Any = None,
                 window_factory: Optional[Callable[..., Any]] = None,
                 available: Optional[Callable[[], bool]] = None,
                 forward_factory: Optional[Callable[[], tuple]] = None,
                 password_provider: Optional[Callable[[str], Callable[[], str]]] = None,
                 nonscriptable: Optional[Callable[[], Any]] = None,
                 new_session: Callable[[], protocol.Session] = protocol.new_session,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time,
                 read_credential: Optional[Callable[[str, str], Any]] = None,
                 set_title: Optional[Callable[[Any, str], None]] = None,
                 watch_factory: Optional[Callable[[Path], Any]] = None,
                 fallback_seconds: float = FALLBACK_SECONDS,
                 run_thread: bool = True) -> None:
        self.host = rdp.host(host)
        self._links = links
        self._on_change = on_change or (lambda snapshot, description: None)
        self.root = tunnel_root(self.host, base_dir)
        self._drives = drives
        self._window_factory = window_factory or _qt_window
        self._available = available or _default_available
        self._forward_factory = forward_factory or _default_forward
        self._password_provider = password_provider
        self._nonscriptable = nonscriptable or _default_nonscriptable
        self._new_session = new_session
        self._clock = clock
        self._wall = wall
        self._read_credential = read_credential or _read_bridge_credential
        self._run_thread = run_thread
        self._lock = threading.RLock()
        self._running = False
        self._stopping = False
        self._window: Any = None
        self._relay: Any = None
        self._client: Any = None
        self._forward: Any = None
        self._thread: Optional[threading.Thread] = None
        self._halt = threading.Event()
        self._pool: Optional[ThreadPoolExecutor] = None
        self._inflight: set = set()
        self._inflight_lock = threading.Lock()
        self._fresh = False
        self._next_cleanup = 0.0
        # 目录监视（dir_watch）：有变化才处理；兜底扫描间隔
        self._watch_factory = watch_factory or dir_watch.open_watcher
        self._fallback = float(fallback_seconds)
        self._watcher: Any = None
        self._next_scan = 0.0
        self.drive = ""
        # 凭据交接（cred_handoff）：当前会话、已见的请求编号、10 秒兜底删、待云端回执的撤销（连接号 → PendingForget / None）
        self._session: Optional[protocol.Session] = None
        self._cred_seen: set = set()
        self._sweeper = handoff.Sweeper()
        self._forgets: dict = {}
        self._cred_lock = threading.Lock()
        # 设备密钥的一次性配对（device_pairing）；窗口标题怎么改（默认排进界面线程，测试注入）
        self._pairing: Optional[pairing.BridgePairing] = None
        # 云端那份 CC 报的状态（ctl\\status.json，D4）：最近一份，只在内存
        self.cloud_state: Optional[dict] = None
        self._set_title = set_title or _queued_title
        self._title_lang = ""

    # ---- 路径 ----
    @property
    def ctl_file(self) -> Path:
        return self.root / "ctl" / protocol.SESSION_FILE

    @property
    def running(self) -> bool:
        return self._running

    @property
    def session(self):
        """给 ``settings_link.status`` / ``disconnect`` 的 ``CloudSession``；窗口没起回 None。"""
        window = self._window
        return getattr(window, "session", None) if window is not None else None

    # ---- 对外 ----
    def start(self) -> None:
        """建目录、映射隧道盘、写会话交接、起中转、建窗口并开始连接。任何一步失败都撤掉已做的再抛。"""
        with self._lock:
            if self._running:
                return
            if not self._available():
                raise HostUnavailable("cloud Express connection needs Windows with PySide6 and comtypes")
            store = self._links()
            link = store.get(self.host) if store is not None else None
            if link is None:
                raise HostError("cloud link not found")
            if self._drives is None:
                self._drives = WindowsDrives()
            self._running = True
            self._halt = threading.Event()
            try:
                for name in ("in", "out", "ctl"):
                    (self.root / name).mkdir(parents=True, exist_ok=True)
                self.drive = self._mount(link.tunnel_drive)
                self._client, self._forward = self._forward_factory()
                self._rotate()
                self._fresh = True
                self._start_relay_thread()
                from .window import ConnectParams

                params = ConnectParams.make(host=link.host, port=link.port, user=link.user, domain=link.domain,
                                            tunnel_drive=self.drive)
                provider = (self._password_provider or _link_password_provider)(self.host)
                self._window = self._window_factory(params, password_provider=self._provide(provider),
                                                    nonscriptable=self._nonscriptable(), on_change=self._changed,
                                                    on_closed=self._closed)
                self._window.start()
            except BaseException:
                self.stop()
                raise
            log.info("云端连接已起 · 主机=%s 隧道盘=%s:", self.host, self.drive)

    def connect_now(self) -> bool:
        """已起着、窗口还在时人再点「连接」：立刻连（停住 / 等重连 / 未连接都算）。"""
        window = self._window
        return bool(window is not None and window.connect_now())

    def stop(self) -> None:
        """关窗（= 断开）、停中转、删会话交接、撤隧道盘。每一步失败都记下并接着做下一步；可重复调。"""
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            try:
                self._teardown()
            finally:
                self._stopping = False
                self._running = False

    # ---- 内部：起停 ----
    def _teardown(self) -> None:
        window, self._window = self._window, None
        if window is not None:
            _step("close window", window.close)
            if hasattr(window, "deleteLater"):
                _step("delete window", window.deleteLater)
        self._halt.set()
        watcher = self._watcher
        if watcher is not None:
            _step("wake relay thread", watcher.interrupt)
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            _step("join relay thread", lambda: thread.join(timeout=5))
        pool, self._pool = self._pool, None
        if pool is not None:
            _step("stop relay workers", lambda: pool.shutdown(wait=False, cancel_futures=True))
        client, self._client, self._forward = self._client, None, None
        if client is not None and hasattr(client, "close"):
            _step("close client", client.close)
        if watcher is not None and (thread is None or not thread.is_alive()):
            _step("close directory watch", watcher.close)   # 线程没起 / 已退出：这里收；否则线程退出时自己收
        self._watcher = None
        self._relay = None
        self._fresh = False
        _step("remove session file", self._remove_ctl)
        self._session = None
        self.cloud_state = None
        _step("remove credential handoff files", lambda: handoff.sweep(self.root / "ctl"))
        current, self._pairing = self._pairing, None
        if current is not None:
            _step("cancel pairing", current.cancel)
        _step("remove pairing files", lambda: handoff.sweep(self.root / "ctl", pairing.FILES))
        _step("remove state files", lambda: handoff.sweep(self.root / "ctl", _ctl_files()))
        drive, self.drive = self.drive, ""
        if drive:
            _step("remove tunnel drive", lambda: self._unmount(drive))
        log.info("云端连接已停 · 主机=%s", self.host)

    def _closed(self) -> None:
        """人把连接窗口关掉了（窗口已断开、不重连）：停中转、撤隧道盘。"""
        if self._running and not self._stopping:
            self.stop()

    def _changed(self, snapshot, description: str) -> None:
        if snapshot is not None and snapshot.state not in _LIVE:
            _step("remove session file", self._remove_ctl)
        try:
            self._on_change(snapshot, description)
        except Exception:
            log.exception("云端连接状态通知失败")

    # ---- 内部：隧道盘 ----
    def _ours(self, letter: str) -> bool:
        try:
            return same_path(self._drives.target(letter), self.root)
        except Exception as exc:  # noqa: BLE001 - 读不到就当不是自己的，不碰
            log.info("读隧道盘映射失败 · %s: %s", letter, type(exc).__name__)
            return False

    def _mount(self, remembered: str) -> str:
        remembered = remembered.upper() if remembered else ""
        used = {str(letter).upper() for letter in self._drives.used()}
        if remembered and remembered in used and self._ours(remembered):
            return remembered   # 上次没撤掉的、就是本隧道根目录：接着用
        for letter in candidates(remembered, used):
            try:
                self._drives.add(letter, self.root)
            except Exception as exc:  # noqa: BLE001 - 这个盘符映射不上：试下一个
                log.info("隧道盘 %s: 映射失败（试下一个）：%s", letter, type(exc).__name__)
                continue
            if letter != remembered:
                self._remember(letter)
            return letter
        raise TunnelDriveError("no free drive letter")

    def _remember(self, letter: str) -> None:
        try:
            store = self._links()   # 现读一份再改，免得拿旧副本盖掉刚登记的令牌
            if store is not None and store.get(self.host) is not None:
                store.set_tunnel_drive(self.host, letter)
        except Exception as exc:  # noqa: BLE001 - 记不下只是下次重新找
            log.info("记隧道盘符失败 · 主机=%s: %s", self.host, type(exc).__name__)

    def _unmount(self, letter: str) -> None:
        if self._ours(letter):
            self._drives.remove(letter)
        else:
            log.info("隧道盘 %s: 已不是本隧道的映射，不撤", letter)

    # ---- 内部：会话交接 ----
    def _provide(self, provider: Callable[[], str]) -> Callable[[], str]:
        """包一层 ``password_provider``：窗口每次起连接调一次，取到密码后换会话（start 后第一次沿用 start 换的）。"""
        def provide() -> str:
            password = provider()
            with self._lock:
                if self._fresh:
                    self._fresh = False
                elif self._running:
                    self._rotate()
            return password

        return provide

    def _rotate(self) -> None:
        """新会话：写 ``ctl\\session.json``（.part → 改名），换上用新密钥的中转。"""
        from .tunnel_relay import TunnelRelay

        session = self._new_session()
        relay = TunnelRelay(self.root, session, "", "", self._forward, route=self._route)
        relay.ensure_dirs()
        ctl = self.ctl_file
        ctl.parent.mkdir(parents=True, exist_ok=True)
        part = ctl.with_name(ctl.name + ".part")
        try:
            with open(part, "wb") as handle:
                handle.write(protocol.encode_session(session))
            os.replace(part, ctl)
        except BaseException:
            _step("remove session part", lambda: _remove(part))
            raise
        self._relay = relay
        self._session = session
        log.info("云端连接换了新会话 · 主机=%s", self.host)
        _step("publish members", self.publish_members)
        with self._cred_lock:
            # 换了会话号：没收到回执的撤销按新会话补发（云端会话不在时，下次连上先补发）
            for connection in list(self._forgets):
                self._forgets[connection] = None
        _step("resend credential forget", self._send_forgets)

    def members(self) -> list:
        """这台云端电脑现在登记了的站点（``ctl_files.member_entry``）：站点、通道（按令牌前缀）、端点号；不含令牌。"""
        from . import ctl_files

        store = self._links()
        link = store.get(self.host) if store is not None else None
        if link is None:
            return []
        found = []
        for member in link.members:
            token = store.token(self.host, member.site)
            if not token:
                continue
            try:
                found.append(ctl_files.member_entry(member.site, channels.of_token(token),
                                                    ctl_files.endpoint_of(token)))
            except ctl_files.CtlError:
                log.info("云端成员不合规，不下发 · 站点=%s", member.site)
        return found

    def publish_members(self) -> bool:
        r"""把 :meth:`members` 用当前会话加密写进 ``ctl\members.json``（整份当前状态，后写的盖前写的）。
        换会话后、对话页交回新站点令牌后调；没在跑、没有会话时不写，回 False。云端读后删除。"""
        from . import ctl_files

        session = self._session
        if session is None or not self._running:
            return False
        members = self.members()
        handoff.write_atomic(self.ctl_dir / ctl_files.MEMBERS_FILE,
                             ctl_files.seal(session, "members", {"members": members}))
        log.info("云端成员已下发 · 主机=%s 站点数=%d", self.host, len(members))
        self._poke()
        return True

    def pump_state(self) -> bool:
        r"""``ctl\\status.json`` 在就读（读后删）、用当前会话解开，存成 :attr:`cloud_state` 并通知界面。回有没有新状态。"""
        from . import ctl_files

        session = self._session
        if session is None:
            return False
        data = handoff.take(self.ctl_dir / ctl_files.STATUS_FILE, 256 * 1024)
        if data is None:
            return False
        try:
            _id, payload = ctl_files.open_(data, session, "status")
        except ctl_files.CtlError as error:
            log.info("云端状态文件不对 · 原因=%s", error.code)
            return False
        self.cloud_state = payload
        try:
            self._on_change(None, "")
        except Exception:
            log.exception("云端连接状态通知失败")
        return True

    def request(self, kind: str, payload: Optional[dict] = None) -> str:
        r"""请云端那份 CC 做一件事（``rescan`` 重新查找 / ``add_place`` 添加数据目录）：加密写进 ``ctl``，回编号（结果在下一份状态里，
        ``add`` 带同一编号）。没在跑、没有会话抛 :class:`HostError`。"""
        from . import ctl_files

        session = self._session
        if session is None or not self._running:
            raise HostError("cloud link is not running")
        item_id = ctl_files.new_id()
        handoff.write_atomic(self.ctl_dir / ctl_files.KINDS[kind],
                             ctl_files.seal(session, kind, dict(payload or {}), item_id=item_id))
        log.info("云端命令已发 · 种类=%s", kind)
        return item_id

    def uninstall_cloud(self, *, timeout: float = UNINSTALL_SECONDS, poll: float = 0.5,
                        sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> str:
        r"""「断开」前请云端那份 CC 删掉我们装的东西（段 ③，``cloud_self.handle_uninstall``）：加密写 ``ctl\uninstall.json``，等同一编号的
        回执 ``ctl\uninstalled.json`` 最多 ``timeout`` 秒；回回执的结果码（``scheduled`` / ``not_ours`` / ``failed``），没回执回
        ``timeout``，没在跑回 ``not_running``。在后台线程调（不挡界面）；之后照常 :meth:`stop`。从不抛。"""
        from . import ctl_files

        session = self._session
        if session is None or not self._running:
            return "not_running"
        item_id = ctl_files.new_id()
        try:
            handoff.write_atomic(self.ctl_dir / ctl_files.UNINSTALL_FILE,
                                 ctl_files.seal(session, "uninstall", {}, item_id=item_id))
        except OSError as exc:
            log.warning("云端卸载请求写入失败 · 原因=%s", type(exc).__name__)
            return "failed"
        log.info("云端卸载请求已发 · 主机=%s", self.host)
        deadline = clock() + timeout
        while True:
            data = handoff.take(self.ctl_dir / ctl_files.UNINSTALLED_FILE, 16 * 1024)
            if data is not None:
                try:
                    got, payload = ctl_files.open_(data, session, "uninstalled")
                except ctl_files.CtlError as error:
                    log.warning("云端卸载回执不对 · 原因=%s", error.code)
                    got, payload = "", {}
                if got == item_id:
                    code = str(payload.get("code") or "failed")
                    log.info("云端卸载回执 · 主机=%s 结果=%s 目录在=%s 快捷方式=%s", self.host, code, payload.get("dir"),
                             payload.get("link"))
                    return code
            if clock() >= deadline:
                break
            sleep(poll)
        _step("remove uninstall request", lambda: handoff.sweep(self.ctl_dir, (ctl_files.UNINSTALL_FILE,)))
        log.info("云端卸载没有回执（云端没在跑或没看到） · 主机=%s 等了=%.0f 秒", self.host, timeout)
        return "timeout"

    def _remove_ctl(self) -> None:
        _remove(self.ctl_file)
        _remove(self.ctl_file.with_name(self.ctl_file.name + ".part"))

    # ---- 内部：中转 ----
    def _route(self, path: str) -> Optional[tuple]:
        """请求路径 → 这台云端电脑在那种通道登记的第一个站点与令牌；没有回 None（中转按路径不允许拒绝）。"""
        wanted = channel_of_path(path)
        if not wanted:
            return None
        store = self._links()
        link = store.get(self.host) if store is not None else None
        if link is None:
            return None
        for member in link.members:
            token = store.token(self.host, member.site)
            if token and channels.of_token(token) == wanted:
                return "https://" + member.site, token
        return None

    def _start_relay_thread(self) -> None:
        self._next_cleanup = self._clock()
        self._next_scan = self._clock() + self._fallback
        if not self._run_thread:
            return
        self._watcher = self._watch_factory(self.root)
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="cloud-relay")
        self._thread = threading.Thread(target=self._loop, args=(self._halt, self._watcher), name="cloud-tunnel",
                                        daemon=True)
        self._thread.start()

    def _poke(self) -> None:
        watcher = self._watcher
        if watcher is not None:
            try:
                watcher.poke()
            except Exception as exc:  # noqa: BLE001
                log.info("叫醒隧道中转失败：%s", type(exc).__name__)

    def _next_wake(self) -> float:
        """下一个到点要醒的时刻（单调时钟）：兜底扫描、凭据交接兜底删、配对过期 / 等回执 / 兜底删，取最早。"""
        times = [self._next_scan]
        due = self._sweeper.next_due
        if due is not None:
            times.append(due)
        current = self._pairing
        if current is not None:
            due = current.next_due()
            if due is not None:
                times.append(due)
        return min(times)

    def _loop(self, halt: threading.Event, watcher) -> None:
        """有变化才 :meth:`pump`；到兜底扫描时刻也 pump 一次；只有定时事到点时只做定时事（不看 ``in\\``）。"""
        try:
            while not halt.is_set():
                timeout = max(0.0, min(self._next_wake() - self._clock(), self._fallback))
                try:
                    changed = watcher.wait(timeout)
                except Exception:
                    log.exception("隧道目录监视出错，改为定时查看")
                    watcher.close()
                    watcher = dir_watch.PollingWatcher()
                    self._watcher = watcher
                    if halt.is_set():
                        break
                    continue
                if halt.is_set():
                    break
                now = self._clock()
                if changed or now >= self._next_scan:
                    self._next_scan = now + self._fallback
                    self.pump()
                else:
                    self._timers()
        finally:
            _step("close directory watch", watcher.close)

    def _timers(self) -> None:
        """到点的定时事：凭据交接的兜底删、配对的过期 / 等回执超时 / 兜底删。"""
        try:
            self._sweeper.run_due(self.ctl_dir, self._clock())
        except Exception:
            log.exception("凭据交接兜底删失败")
        self.pump_pairing()

    def pump(self) -> int:
        """看一遍 ``in\\``：新请求交给工作线程（没起线程时就地处理）；到点清理一次。回交出去几个。"""
        relay = self._relay
        if relay is None:
            return 0
        try:
            self.pump_credentials()
        except Exception:
            log.exception("凭据交接处理失败")
        try:
            self.pump_state()
        except Exception:
            log.exception("读云端状态失败")
        handed = 0
        for path in relay.pending():
            with self._inflight_lock:
                if path in self._inflight:
                    continue
                self._inflight.add(path)
            handed += 1
            pool = self._pool
            if pool is None:
                self._process(relay, path)
                continue
            try:
                pool.submit(self._process, relay, path)
            except RuntimeError:   # 正在停
                self._done(path)
                break
        now = self._clock()
        if now >= self._next_cleanup:
            self._next_cleanup = now + CLEANUP_SECONDS
            try:
                relay.cleanup()
            except Exception:
                log.exception("隧道清理失败")
        return handed

    # ---- 内部：凭据交接（cred_handoff；只在 ctl\\ 里，与中转的 in\\ / out\\ 无关） ----
    @property
    def ctl_dir(self) -> Path:
        return self.root / "ctl"

    def request_forget(self, connection: str) -> None:
        """请云端删掉这条连接的凭据：记下，有会话就写 ``cred-forget.json``，没有就等下次会话补发；收到回执才算完。"""
        connection = handoff.connection_of(connection)
        with self._cred_lock:
            self._forgets[connection] = None
        self._send_forgets()

    @property
    def pending_forgets(self) -> tuple:
        with self._cred_lock:
            return tuple(sorted(self._forgets))

    def _device_key(self) -> str:
        store = self._links()
        if store is None or store.get(self.host) is None:
            raise handoff.Refused(handoff.UNPAIRED)
        return store.device_key(self.host)

    def _send_forgets(self) -> None:
        """把没发过、或发过已过期的撤销写进 ``ctl``（一次一份：上一份云端还没取走就先不写）。"""
        session = self._session
        if session is None or not self._running:
            return
        now = self._wall()
        with self._cred_lock:
            wanted = [connection for connection, sent in self._forgets.items() if sent is None or now > sent.expires]
            if not wanted or (self.ctl_dir / handoff.FORGET_FILE).exists():
                return
            connection = wanted[0]
            pending, data = handoff.make_forget(self._device_key(), session.epoch, connection, now)
            handoff.write_atomic(self.ctl_dir / handoff.FORGET_FILE, data)
            self._forgets[connection] = pending

    def pump_credentials(self) -> None:
        """看一遍 ``ctl``：回应凭据请求、收撤销回执、补发撤销、到点兜底删。"""
        session = self._session
        if session is None:
            return
        ctl = self.ctl_dir
        request = handoff.take(ctl / handoff.REQUEST_FILE)
        if request is not None:
            try:
                device_key = self._device_key()
            except Exception as exc:  # noqa: BLE001 - 没有设备密钥：不回应
                log.info("云端凭据交接 · 事件=request_refused 原因=%s 连接=-", type(exc).__name__)
                device_key = None
            if device_key is not None:
                result = handoff.answer(request, device_key, session.epoch, self._wall(), self._cred_seen,
                                        lambda connection: self._read_credential(self.host, connection))
                if result.response is not None:
                    handoff.write_atomic(ctl / handoff.RESPONSE_FILE, result.response)
                    self._sweeper.schedule(self._clock() + handoff.SWEEP_DELAY,
                                           (handoff.REQUEST_FILE, handoff.RESPONSE_FILE))
        ack = handoff.take(ctl / handoff.FORGET_ACK_FILE)
        if ack is not None:
            self._forget_acked(ack)
        self._send_forgets()
        self._sweeper.run_due(ctl, self._clock())
        self.pump_pairing()

    # ---- 内部：设备密钥的一次性配对（device_pairing；只在 ctl\\） ----
    def begin_pairing(self, lang: str = "") -> pairing.Ticket:
        """生成一次性令牌（2 分钟、一次、失败即作废；再点一次旧的作废）。只回给可见界面显示，不进日志。没起连接抛 HostError。"""
        if not self._running or self._session is None:
            raise HostError("cloud link is not running")
        with self._lock:
            if self._pairing is None:
                self._pairing = pairing.BridgePairing(self.host, self._device_key, clock=self._clock, wall=self._wall)
            ticket = self._pairing.begin()
        self._title(paired=False, lang=lang)
        self._poke()   # 中转线程按新的过期时刻重算等多久
        return ticket

    def window_handle(self) -> int:
        """连接窗口（顶层窗口）的句柄，给首次自动安装发键用；没有窗口回 0。"""
        window = self._window
        return int(window.winId()) if window is not None else 0

    def minimize_window(self) -> None:
        """自动安装配好后把连接窗口收回最小化（默认样子）。"""
        window = self._window
        if window is not None:
            window.showMinimized()

    def pairing_status(self) -> dict:
        """``{state, code, expires}``：idle / waiting / answered / paired / failed；不含令牌。"""
        current = self._pairing
        if current is None:
            return {"state": pairing.IDLE, "code": "", "expires": 0}
        return current.status()

    def pump_pairing(self) -> None:
        current = self._pairing
        if current is None:
            return
        was_active = current.active
        try:
            changed = current.pump(self.ctl_dir)
        except Exception:
            log.exception("云端设备配对处理失败")
            return
        if was_active and not current.active:
            self._title(paired=True)
        if changed:
            try:
                self._on_change(None, "")
            except Exception:
                log.exception("云端连接状态通知失败")

    def _title(self, *, paired: bool, lang: str = "") -> None:
        """连接窗口标题：配对进行中 = 「窗口标题常量 · 配对」（已有词条），结束改回窗口标题常量。"""
        window = self._window
        if window is None:
            return
        from ..i18n import normalize, t
        from .window import WINDOW_TITLE

        if not paired:
            self._title_lang = normalize(lang)
        text = WINDOW_TITLE if paired else WINDOW_TITLE + " · " + t("btn_pair", self._title_lang)
        try:
            self._set_title(window, text)
        except Exception as exc:  # noqa: BLE001 - 改不了标题不挡配对
            log.info("改连接窗口标题失败：%s", type(exc).__name__)

    def _forget_acked(self, ack: bytes) -> None:
        try:
            device_key = self._device_key()
        except Exception:  # noqa: BLE001
            return
        with self._cred_lock:
            for connection, sent in list(self._forgets.items()):
                if sent is not None and handoff.open_forget_ack(ack, sent, device_key) == handoff.OK:
                    del self._forgets[connection]
                    return

    def _process(self, relay, path) -> None:
        try:
            relay.process(path)
        except Exception:
            log.exception("隧道处理请求失败")
        finally:
            self._done(path)

    def _done(self, path) -> None:
        with self._inflight_lock:
            self._inflight.discard(path)


def _queued_title(window, text: str) -> None:
    """改连接窗口标题：中转线程里调也安全（排进窗口所在的界面线程）。"""
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    QMetaObject.invokeMethod(window, "setWindowTitle", Qt.ConnectionType.QueuedConnection, Q_ARG(str, text))


def _read_bridge_credential(host: str, connection: str):
    """桥本机为这条云端连接存的代录凭据（``Pearnly CC/cloud-express/<主机>/<连接>``）；没有 / 不是 Windows 回 None。"""
    from ..express_operator import credentials

    return credentials.read(handoff.bridge_target(host, connection))


def _link_password_provider(host: str) -> Callable[[], str]:
    from .link_store import password_provider

    return password_provider(host)


def _ctl_files() -> tuple:
    from . import ctl_files

    return ctl_files.FILES


def _step(name: str, action: Callable[[], Any]) -> None:
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - 停的每一步都尽力做完
        log.warning("停云端连接时 %s 失败: %s", name, type(exc).__name__)


def _remove(path: Path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


__all__ = ["CLEANUP_SECONDS", "UNINSTALL_SECONDS", "CloudHost", "HostError", "HostUnavailable", "FALLBACK_SECONDS", "LETTERS", "TunnelDriveError",
           "WindowsDrives", "candidates", "channel_of_path", "release_stale_drive", "run_subst", "same_path", "tunnel_root"]
