# -*- coding: utf-8 -*-
"""云端连接宿主（桥这一侧，评估第 4.2–4.3 节）：桥进程里每条云端连接一个，把连接窗口、隧道盘、会话交接、中转线程串起来。

``tomo_bridge`` 在人点「设置 → 连接 → 云端 Express → 连接」并存好连接后 :meth:`CloudHost.start`；「断开连接」先
:meth:`CloudHost.stop` 再删连接；人把连接窗口关掉 = 断开，宿主随之 stop。**CC 启动时就算已存有云端连接也不自动连**，
等人在设置里点「连接」（存着的只是连接配置，连不连由人定）。

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
  后台线程每 0.5 秒看一遍 ``in\\``，请求交给几条工作线程并发处理（站点慢的请求不挡别的）；每 60 秒清理一次没人取的文件。
- 连接窗口：``window.CloudWindow`` + ``ConnectParams.make(..., tunnel_drive=盘符)`` + ``link_store.password_provider`` +
  ``nonscriptable.for_widget()``，状态变化转给上层的 ``on_change``。

不是 Windows、没有 ``QtAxContainer``（``window.available()`` 为假）时 :meth:`start` 不做任何事，抛 :class:`HostUnavailable`。
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
from . import rdp
from . import tunnel_protocol as protocol
from .session import State

log = get_logger(__name__)

# 隧道盘从 Z 往 D 找
LETTERS = "ZYXWVUTSRQPONMLKJIHGFED"
POLL_SECONDS = 0.5
CLEANUP_SECONDS = 60.0
WORKERS = 4
SUBST_TIMEOUT = 15
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
# 会话结束：不在连、不在已连上（断开、等重连、停住）
_LIVE = (State.CONNECTING, State.CONNECTED)


class HostUnavailable(RuntimeError):
    """这台电脑起不了云端连接（不是 Windows、没有 ``QtAxContainer``）。start 前抛，没有任何副作用。"""


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
    import httpx

    from .. import http_tls
    from .tunnel_relay import httpx_forward

    client = httpx.Client(verify=http_tls.context(), follow_redirects=False)
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
        self.drive = ""

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
                raise HostUnavailable("cloud Express connection needs Windows with PySide6.QtAxContainer")
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
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            _step("join relay thread", lambda: thread.join(timeout=5))
        pool, self._pool = self._pool, None
        if pool is not None:
            _step("stop relay workers", lambda: pool.shutdown(wait=False, cancel_futures=True))
        client, self._client, self._forward = self._client, None, None
        if client is not None and hasattr(client, "close"):
            _step("close client", client.close)
        self._relay = None
        self._fresh = False
        _step("remove session file", self._remove_ctl)
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
        log.info("云端连接换了新会话 · 主机=%s", self.host)

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
        if not self._run_thread:
            return
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="cloud-relay")
        self._thread = threading.Thread(target=self._loop, args=(self._halt,), name="cloud-tunnel", daemon=True)
        self._thread.start()

    def _loop(self, halt: threading.Event) -> None:
        while not halt.wait(POLL_SECONDS):
            self.pump()

    def pump(self) -> int:
        """看一遍 ``in\\``：新请求交给工作线程（没起线程时就地处理）；到点清理一次。回交出去几个。"""
        relay = self._relay
        if relay is None:
            return 0
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


def _link_password_provider(host: str) -> Callable[[], str]:
    from .link_store import password_provider

    return password_provider(host)


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


__all__ = ["CLEANUP_SECONDS", "CloudHost", "HostError", "HostUnavailable", "LETTERS", "POLL_SECONDS", "TunnelDriveError",
           "WindowsDrives", "candidates", "channel_of_path", "run_subst", "same_path", "tunnel_root"]
