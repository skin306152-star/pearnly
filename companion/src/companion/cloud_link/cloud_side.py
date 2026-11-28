# -*- coding: utf-8 -*-
r"""云端那份 CC 自己的接线（``companion.exe --cloud``，runtime_mode）：找隧道盘、配对、读会话、装隧道 transport、代录凭据。

只在云端模式用（``main.py`` 的 ``--cloud`` 分支与 ``express_operator.host`` 在云端模式下才导入本模块）；本地模式什么都不碰。
本模块不导入 Qt（云端精简包里没有，``tests/test_cloud_package.py`` 守住）。

- 隧道根目录（:func:`find_tunnel_root`）：云端会话里桥只映射了一个盘，路径 ``\\tsclient\<盘符>``。按桥会用的盘符（Z 往 D）逐个
  直接探（``\\tsclient`` 这一级在会话里列不出来），根下 ``in``、``out``、``ctl`` 三个子目录都在的才算；恰好一个才用，零个或多个回 None（不猜）。
- 配对（:func:`run_pair`，``--cloud --pair <令牌>``，只在安装命令里带一次）：主机名从桥放进隧道盘的 ``cc\cloud.json`` 的 ``host``
  读。桥写入的必须是 ``rdp.host(连接主机)``——桥 ``BridgePairing(link.host)`` 用的同一个规范化主机地址（去首尾空白与末尾点、转小写），
  两侧都按它派生认证密钥，差一个字符配对就会被拒（``tampered``）。调 ``device_pairing.pair``；成功后设备密钥在云端本用户凭据管理器
  ``Pearnly CC/cloud-device/<主机>``。以后要主机名时（代录凭据、撤销）从凭据管理器那一条的名字取（:func:`paired_host`，只读名字）。
  令牌不进日志、文件、异常文本。
- 会话（:class:`CloudLink`）：读 ``ctl\session.json``（``tunnel_protocol.decode_session``），读后删除并回读；没有就等，
  没有隧道盘退避 1 秒起翻倍、最多 30 秒一次；有隧道盘盯 ``ctl``，桥写新会话即接；不退出。会话只在内存。
- transport（:class:`ReloadingTransport`）：包一个 ``TunnelTransport``，经 ``net.set_transport_factory`` 装上，云端 CC 所有
  ``net.client(...)`` 建的客户端都走隧道。遇到网络错误（会话旧了桥回的拒绝核不过签名、桥没取走、回复超时）先看 ``ctl`` 有没有
  新会话文件：有就换上；桥没取走或核不过签名的请求桥没代发，换完重发一次；回复超时的不重发（桥可能已代发，免得发两次）。
  ``Authorization`` 照 ``TunnelTransport`` 的约定由桥换真令牌。
- 成员（D3，:func:`take_members`）：桥下发的 ``ctl\members.json`` 用当前会话解开，交 ``cloud_members.sync`` 建 / 改 / 删
  ``cloud-<站点>`` 成员；改了就 :meth:`CloudLink.bump`，``main.step_cloud_loop`` 停下直写循环重读成员。没有成员时等着、不退出。
  认电脑用配对存下的设备密钥（:func:`paired_device_key`，``device_link.device_key`` 在云端模式下取它）。
- ``ctl`` 变化（:class:`CtlWatch`，``dir_watch`` 监视 ``ctl``，另 60 秒兜底看一次）：有新会话就换上；桥发来撤销就
  ``cred_cloud.handle_forget``。
- 代录凭据（:func:`ensure_express_login`）：代录宿主要 Express 登录时，本用户凭据管理器（``credentials.target(连接)``）里有就用，
  没有才经 ``ctl`` 向桥要（``cred_cloud.fetch``，设备密钥从本机凭据管理器取）。

日志只有事件名、原因码与退出码；不记令牌、设备密钥、会话密钥、用户名、密码。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

import httpx

from ..logger import get_logger
from . import tunnel_protocol as protocol

log = get_logger(__name__)

CLIENT_SHARE = "\\\\tsclient"
TUNNEL_DIRS = ("in", "out", "ctl")
CC_DIR = "cc"
MANIFEST = "cloud.json"
CONNECTION = "default"          # 代录宿主用的那条（``express_operator.host``：``credentials.target("default")``）
BACKOFF_FIRST = 1.0
BACKOFF_MOST = 30.0
# 监视线程出错后隔多久重开（秒）
REOPEN_DELAY = 2.0
CTL_FALLBACK = 60.0
PAIR_TIMEOUT = 90.0

# --pair 的退出码（安装脚本按它打一行结果）
EXIT_OK = 0
EXIT_NO_TUNNEL = 3
EXIT_NO_HOST = 4
EXIT_TYPO = 5
EXIT_TIMEOUT = 6
EXIT_REFUSED = 7

OK = "ok"
NO_TUNNEL = "no_tunnel"
NO_HOST = "no_host"
UNPAIRED = "unpaired"


# ── 隧道盘与主机名 ────────────────────────────────────────────────────────────

# 桥映射隧道盘时可选的盘符（同 ``host.LETTERS``，桥从 Z 往 D 找第一个空的；tests 守两边一致）
TUNNEL_LETTERS = "ZYXWVUTSRQPONMLKJIHGFED"


def find_tunnel_root(*, base: str = CLIENT_SHARE, letters: str = TUNNEL_LETTERS,
                     is_dir: Callable[[str], bool] = os.path.isdir) -> Optional[Path]:
    r"""``\\tsclient\<盘符>`` 里根下有 ``in`` / ``out`` / ``ctl`` 的恰好一个时回它，否则 None。

    逐个盘符直接探，不列 ``\\tsclient`` 这一级：远程桌面会话里它列不出来（11-24 云端实测 ``dir \\tsclient`` 报路径语法不正确、
    ``net view`` 报 1707，``Test-Path \\tsclient\Z\ctl`` 却为真），先列再挑就永远找不到。"""
    found = []
    for letter in letters:
        root = os.path.join(base, letter)
        try:
            if all(is_dir(os.path.join(root, sub)) for sub in TUNNEL_DIRS):
                found.append(root)
        except OSError:
            continue
    return Path(found[0]) if len(found) == 1 else None


def given_root(path) -> Optional[Path]:
    r"""安装脚本交来的隧道根（它自己所在的 ``\\tsclient\<盘>\cc`` 的上一级）：根下 ``in`` / ``out`` / ``ctl`` 都在才用，否则 None。"""
    text = str(path or "").strip()
    if not text:
        return None
    try:
        return Path(text) if all(os.path.isdir(os.path.join(text, sub)) for sub in TUNNEL_DIRS) else None
    except OSError:
        return None


def read_host(root: Path) -> Optional[str]:
    r"""桥放进隧道盘的 ``cc\cloud.json`` 里的 ``host``（规范化同 ``rdp.host``）；没有、读不了、不合规回 None。"""
    from . import rdp

    try:
        with open(Path(root) / CC_DIR / MANIFEST, "rb") as handle:
            data = json.loads(handle.read(64 * 1024).decode("utf-8-sig"))
        return rdp.host(data.get("host", "")) if isinstance(data, dict) else None
    except (OSError, ValueError, rdp.RdpValueError):
        return None


def paired_device_key(*, host: Optional[Callable[[], str]] = None, load=None) -> str:
    """配对存下的设备密钥（同桥 ``LinkStore.device_key``，对话页登记这台云端电脑用的就是它）；没配对回空串。
    云端那份 CC 认电脑（``device_link.device_key``）用它，不另生成一把。"""
    paired = (host or paired_host)()
    if not paired:
        return ""
    if load is None:
        from .device_pairing import load_device_key as load
    return load(paired) or ""


def paired_host(*, entries: Optional[Callable[[str], list]] = None) -> str:
    """本用户凭据管理器里配对存下的设备密钥那一条（``Pearnly CC/cloud-device/<主机>``）的主机；没有或不止一条回空串。"""
    from .device_pairing import DEVICE_PREFIX

    if entries is None:
        from ..express_operator import credentials

        entries = credentials.entries
    names = [str(item[0]) for item in entries(DEVICE_PREFIX)]
    hosts = sorted({name[len(DEVICE_PREFIX):] for name in names
                    if name.startswith(DEVICE_PREFIX) and len(name) > len(DEVICE_PREFIX)})
    return hosts[0] if len(hosts) == 1 else ""


# ── 配对 ──────────────────────────────────────────────────────────────────────

def _exit_code(code: str) -> int:
    from . import device_pairing as pairing

    if code == pairing.OK:
        return EXIT_OK
    if code in (pairing.CHECKSUM, pairing.MALFORMED):
        return EXIT_TYPO
    if code == pairing.TIMEOUT:
        return EXIT_TIMEOUT
    return EXIT_REFUSED


def run_pair(token: str, *, root=None, find: Callable[[], Optional[Path]] = find_tunnel_root,
             host_of: Callable[[Path], Optional[str]] = read_host, pair=None,
             timeout: float = PAIR_TIMEOUT) -> int:
    """``--cloud --pair <令牌> [--tunnel <根>]``：用一次性令牌向桥要设备密钥，存进本用户凭据管理器。回退出码（0 成功）。只记原因码。
    ``root``：安装脚本交来的隧道根，合规就用；没给或不合规再按盘符探（:func:`find_tunnel_root`）。"""
    root = (given_root(root) if root else None) or find()
    if root is None:
        log.error("云端配对 · 结果=%s 退出码=%d", NO_TUNNEL, EXIT_NO_TUNNEL)
        return EXIT_NO_TUNNEL
    host = host_of(root)
    if not host:
        log.error("云端配对 · 结果=%s 退出码=%d", NO_HOST, EXIT_NO_HOST)
        return EXIT_NO_HOST
    if pair is None:
        from .device_pairing import pair
    try:
        code = pair(Path(root) / "ctl", token, host, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 写不进隧道盘等：只记类型
        log.error("云端配对 · 结果=error:%s 退出码=%d", type(exc).__name__, EXIT_REFUSED)
        return EXIT_REFUSED
    finally:
        token = ""  # noqa: F841
    exit_code = _exit_code(code)
    (log.info if exit_code == EXIT_OK else log.error)("云端配对 · 结果=%s 退出码=%d", code, exit_code)
    return exit_code


# ── 会话 ──────────────────────────────────────────────────────────────────────

def take_session(root: Path) -> Optional[protocol.Session]:
    r"""读 ``ctl\session.json``，读后删除并回读；没有或不合规回 None（不合规的也删掉，等桥下一次写）。"""
    from . import cred_handoff as handoff

    data = handoff.take(Path(root) / "ctl" / protocol.SESSION_FILE)
    if data is None:
        return None
    try:
        return protocol.decode_session(data)
    except protocol.TunnelError as error:
        log.warning("云端会话文件不合规 · 原因=%s", error.code)
        return None


class CloudLink:
    """云端这一侧的隧道：根目录与当前会话（只在内存）。``refresh`` 可从任何线程调。"""

    def __init__(self, root: Path, session: protocol.Session, *,
                 take: Callable[[Path], Optional[protocol.Session]] = take_session) -> None:
        self.root = Path(root)
        self._session = session
        self._take = take
        self._lock = threading.Lock()
        self._listeners: list = []
        self.forget_seen: set = set()
        # 成员（D3）：桥下发的 members.json 改了本机成员就 +1，直写循环据此重载；结果码给状态文件
        self._changed = threading.Condition()
        self.generation = 0
        self.members_code = ""
        self.check_lock = threading.Lock()
        # D4：最近一次下发的站点（重新查找 / 添加后没有成员时按它再建）、查找与添加结果、状态文件（内容没变不重写）、已做过的命令编号
        self.last_members: list = []
        self.scan_code = ""
        self.scanned_at = 0
        self.add_result: dict = {}
        self.status_key = None
        self.next_status = 0.0
        self.commands_seen: set = set()
        self.command_lock = threading.Lock()
        self.status_lock = threading.Lock()   # 监视线程与命令线程都会写状态文件：同一时刻只一个写（共用 .part 名）
        # 段 ③：自升级备好了 / 断开要删我们装的东西 → 本进程退出（main.step_cloud_loop 的 stop 看它）；试过失败的版本不再试
        self.quit = threading.Event()
        self.upgrade_failed: set = set()

    def __repr__(self) -> str:
        return f"CloudLink(root={str(self.root)!r})"

    @property
    def ctl_dir(self) -> Path:
        return self.root / "ctl"

    @property
    def session(self) -> protocol.Session:
        return self._session

    def on_session(self, listener: Callable[[protocol.Session], None]) -> None:
        self._listeners.append(listener)

    def bump(self) -> None:
        """本机成员变了：直写循环停下重载。"""
        with self._changed:
            self.generation += 1
            self._changed.notify_all()

    def wait_change(self, generation: int, stop: Callable[[], bool], step: float = 1.0) -> None:
        """等到 ``generation`` 变了或 ``stop()`` 为真（每秒看一次要不要停）。"""
        with self._changed:
            while self.generation == generation and not stop():
                self._changed.wait(step)

    def refresh(self) -> Optional[protocol.Session]:
        """``ctl`` 里有新会话文件就换上并回它；没有回 None。"""
        with self._lock:
            fresh = self._take(self.root)
            if fresh is None:
                return None
            self._session = fresh
            for listener in list(self._listeners):
                listener(fresh)
        log.info("云端会话已换新")
        return fresh


def connect(stop: Callable[[], bool], *, find: Callable[[], Optional[Path]] = find_tunnel_root,
            take: Callable[[Path], Optional[protocol.Session]] = take_session,
            sleep: Callable[[float], None] = time.sleep, first: float = BACKOFF_FIRST,
            most: float = BACKOFF_MOST, watch_factory=None) -> Optional[CloudLink]:
    """等到隧道盘与会话文件都在：回 :class:`CloudLink`；等的时候 ``stop()`` 为真回 None。

    没有隧道盘：1 秒起翻倍、最多 30 秒探一次（探不到的盘符立即失败，不费事）。有隧道盘没会话：盯着 ``ctl``，桥一写新会话就接，
    最多等 ``most`` 秒再看一遍兜底。11-28 真机：以前一律退避到 15 分钟，桥重连后云端要等 3.5 分钟以上才接上。"""
    wait, said = first, ""
    while not stop():
        root = find()
        session = take(root) if root is not None else None
        if session is not None:
            log.info("云端会话已就绪")
            return CloudLink(root, session, take=take)
        state = NO_TUNNEL if root is None else "no_session"
        if state != said:
            log.info("云端等待隧道 · 状态=%s", state)
            said = state
        if root is not None and watch_factory is not None:
            _wait_for_ctl(root, watch_factory, most, stop)
        else:
            _sleep_until(wait, stop, sleep)
            wait = min(most, wait * 2)
    return None


def _wait_for_ctl(root: Path, watch_factory, timeout: float, stop: Callable[[], bool]) -> None:
    """盯 ``<隧道根>\\ctl`` 到有变化、``timeout`` 秒或要停为止（每秒看一次要不要停）。打不开监视就按 1 秒睡一次。"""
    try:
        watcher = watch_factory(Path(root) / "ctl")
    except Exception as exc:  # noqa: BLE001 - 盘刚断开：下一轮再探
        log.info("云端等会话时监视打不开：%s", type(exc).__name__)
        _sleep_until(1.0, stop, time.sleep)
        return
    try:
        left = float(timeout)
        while left > 0 and not stop():
            step = min(1.0, left)
            if watcher.wait(step):
                return
            left -= step
    except Exception as exc:  # noqa: BLE001 - 句柄失效：回外层重探
        log.info("云端等会话时监视出错：%s", type(exc).__name__)
    finally:
        try:
            watcher.close()
        except Exception:
            log.exception("关闭 ctl 监视失败")


def _sleep_until(seconds: float, stop: Callable[[], bool], sleep: Callable[[float], None]) -> None:
    """睡 ``seconds`` 秒，每秒看一次要不要停（SIGTERM 后不必等满一轮）。"""
    left = float(seconds)
    while left > 0 and not stop():
        step = min(1.0, left)
        sleep(step)
        left -= step


# ── transport ────────────────────────────────────────────────────────────────

class ReloadingTransport(httpx.BaseTransport):
    """包一个 ``TunnelTransport``；会话失效时从 ``ctl`` 换新会话（见模块说明）。所有客户端共用一个，``close`` 什么都不做。"""

    def __init__(self, link: CloudLink, **options) -> None:
        from .tunnel_transport import TunnelTransport

        self.link = link
        self.inner = TunnelTransport(link.root, link.session, **options)
        link.on_session(self.inner.set_session)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return self.inner.handle_request(request)
        except (httpx.ConnectError, httpx.ReadTimeout) as error:
            if self.link.refresh() is None:
                raise
            if isinstance(error, httpx.ReadTimeout):
                raise   # 桥取走了：可能已代发，不重发
        return self.inner.handle_request(request)

    def close(self) -> None:
        return None


# ── ctl 变化 ──────────────────────────────────────────────────────────────────

def open_ctl(link: CloudLink, data: bytes, kind: str) -> tuple:
    """用当前会话解开 ``ctl`` 里桥写的文件；会话号对不上时先看有没有新会话（桥在我们换上会话之后又换了一次，新 session.json 与
    这份文件都已写好），换上再解一次，别把它丢掉。解不开抛 ``ctl_files.CtlError``。"""
    from . import ctl_files

    try:
        return ctl_files.open_(data, link.session, kind)
    except ctl_files.CtlError as error:
        if error.code != ctl_files.WRONG_EPOCH or link.refresh() is None:
            raise
        return ctl_files.open_(data, link.session, kind)


def take_commands(link: CloudLink, *, run=None) -> list:
    r"""``ctl\rescan.json`` / ``ctl\add-place.json`` 在就读（读后删除）、解开、核编号没做过，交 ``run(kind, payload, 编号)``。
    回交出去的种类。默认在一条后台线程里一件一件做（找账套可能要几十秒，不挡监视线程换会话），做完写状态文件。"""
    from . import cred_handoff as handoff
    from . import ctl_files

    taken = []
    for kind in ("rescan", "add_place"):
        data = handoff.take(link.ctl_dir / ctl_files.KINDS[kind], 16 * 1024)
        if data is None:
            continue
        try:
            item_id, payload = open_ctl(link, data, kind)
        except ctl_files.CtlError as error:
            log.warning("云端命令文件不对 · 种类=%s 原因=%s", kind, error.code)
            continue
        if item_id in link.commands_seen:
            log.info("云端命令已做过 · 种类=%s", kind)
            continue
        link.commands_seen.add(item_id)
        taken.append(kind)
        (run or _run_command)(link, kind, payload, item_id)
    return taken


def _run_command(link: CloudLink, kind: str, payload: dict, item_id: str) -> None:
    def work() -> None:
        from . import cloud_books

        with link.command_lock:
            try:
                if kind == "rescan":
                    cloud_books.rescan(link)
                else:
                    cloud_books.add_place(link, payload.get("path"), item_id)
            except Exception:
                log.exception("云端命令失败 · 种类=%s", kind)
            report_status(link, force=True)

    threading.Thread(target=work, name="cloud-command", daemon=True).start()


def report_status(link: CloudLink, *, force: bool = False, write=None, clock=time.monotonic) -> bool:
    """写状态文件（``cloud_books.write_status``）：``force`` 立刻写；否则到 5 分钟才看一次、内容变了才写。从不抛。"""
    with link.status_lock:
        return _report_status(link, force, write, clock)


def _report_status(link: CloudLink, force: bool, write, clock) -> bool:
    now = clock()
    if not force and now < link.next_status:
        return False
    from .cloud_books import STATUS_EVERY

    if write is None:
        from .cloud_books import write_status as write
    link.next_status = now + STATUS_EVERY
    try:
        return bool(write(link, force=force))
    except Exception as exc:  # noqa: BLE001 - 写不进隧道盘：下次再写
        log.warning("云端状态写入失败 · 原因=%s", type(exc).__name__)
        return False


def take_members(link: CloudLink, *, sync=None) -> Optional[str]:
    r"""``ctl\members.json`` 在就读（读后删除）、用当前会话解开、交 ``cloud_members.sync`` 改本机成员；改了就 :meth:`CloudLink.bump`。
    回结果码（``ready`` / ``no_books`` / ``none``；文件不对回 ``ctl_files`` 的原因码）；没有文件回 None。"""
    from . import cred_handoff as handoff
    from . import ctl_files

    data = handoff.take(link.ctl_dir / ctl_files.MEMBERS_FILE, 64 * 1024)
    if data is None:
        return None
    try:
        _id, payload = open_ctl(link, data, "members")
        members = ctl_files.members_of(payload)
    except ctl_files.CtlError as error:
        log.warning("云端成员文件不对 · 原因=%s", error.code)
        return error.code
    if sync is None:
        from .cloud_members import sync
    try:
        code, changed = sync(members)
    except Exception as exc:  # noqa: BLE001 - 本机配置写不进：下次桥再下发时重试
        log.warning("云端成员更新失败 · 原因=%s", type(exc).__name__)
        return "error"
    link.members_code = code
    link.last_members = members
    log.info("云端成员 · 结果=%s 站点数=%d 有改动=%s", code, len(members), changed)
    if changed:
        link.bump()
    return code


def _uninstall(link: CloudLink) -> Optional[str]:
    from .cloud_self import handle_uninstall

    return handle_uninstall(link)


def _upgrade(link: CloudLink) -> str:
    from .cloud_self import upgrade

    return upgrade(link)


def check_ctl(link: CloudLink, *, host: Optional[Callable[[], str]] = None, handle_forget=None,
              members=take_members, commands=take_commands, status=report_status,
              uninstall=_uninstall, upgrade=_upgrade) -> Optional[str]:
    r"""看一遍 ``ctl``：有新会话就换上；有成员下发就改本机成员；桥请删安装就删（段 ③，删了就不再往下看）；隧道盘 ``cc\`` 有更新的
    云端包就自升级；有撤销就交 ``cred_cloud.handle_forget``（回它的原因码，没有撤销回 None）。
    监视线程与直写循环启动时都会调，同一时刻只一个在看。"""
    from . import cred_handoff as handoff

    with link.check_lock:
        fresh = link.refresh()
        if link.quit.is_set():
            return None
        code = members(link)
        commands(link)
        if uninstall(link) is not None and link.quit.is_set():
            return None
        if not link.quit.is_set():
            try:
                upgrade(link)
            except Exception:
                log.exception("云端自升级检查失败")
        # 换了会话（桥那边是新的一次连接，旧状态解不开）或成员刚处理过：马上报；平时 5 分钟看一次、变了才写
        status(link, force=fresh is not None or code is not None)
        if not (link.ctl_dir / handoff.FORGET_FILE).exists():
            return None
        return _forget(link, host, handle_forget)


def _forget(link: CloudLink, host, handle_forget) -> Optional[str]:
    if handle_forget is None:
        from .cred_cloud import handle_forget
    code = handle_forget(link.ctl_dir, epoch=link.session.epoch, seen_ids=link.forget_seen,
                         host=(host or paired_host)())
    log.info("云端凭据撤销 · 原因=%s", code)
    return code


class CtlWatch:
    """后台线程：``ctl`` 有变化（或每 60 秒兜底）时 :func:`check_ctl`。"""

    def __init__(self, link: CloudLink, *, watch_factory=None, fallback: float = CTL_FALLBACK,
                 check: Callable[[CloudLink], object] = check_ctl) -> None:
        from . import dir_watch

        self.link = link
        self._watch_factory = watch_factory or dir_watch.open_watcher
        self._fallback = fallback
        self._check = check
        self._halt = threading.Event()
        self._watcher = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._watcher = self._watch_factory(self.link.ctl_dir)
        self._thread = threading.Thread(target=self._run, name="cloud-ctl-watch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._halt.is_set():
                try:
                    self._watcher.wait(self._fallback)
                except Exception as exc:  # noqa: BLE001 - 句柄失效（桥重启后隧道盘重新映射）：重开，线程不能死
                    log.warning("云端 ctl 监视出错，重开：%s", type(exc).__name__)
                    self._reopen()
                if self._halt.is_set():
                    break
                try:
                    self._check(self.link)
                except Exception:
                    log.exception("云端 ctl 检查失败")
        finally:
            self._close(self._watcher)

    def _reopen(self) -> None:
        """关掉坏了的监视、隔一会儿再开（11-28 真机：桥重启后旧句柄失效，监视线程抛错退出，之后 ctl 再有变化与
        60 秒兜底都不看了——自升级、卸载、成员都收不到）。开不了 ``open_watcher`` 自己会退回定时查看。"""
        self._close(self._watcher)
        self._halt.wait(REOPEN_DELAY)
        if self._halt.is_set():
            return
        try:
            self._watcher = self._watch_factory(self.link.ctl_dir)
        except Exception as exc:  # noqa: BLE001 - 目录暂时不在：用一个只靠兜底超时的监视，下次出错再重开
            from . import dir_watch

            log.warning("云端 ctl 监视重开失败，改为定时查看：%s", type(exc).__name__)
            self._watcher = dir_watch.PollingWatcher()

    @staticmethod
    def _close(watcher) -> None:
        try:
            watcher.close()
        except Exception:
            log.exception("关闭 ctl 监视失败")

    def stop(self) -> None:
        self._halt.set()
        if self._watcher is not None:
            self._watcher.interrupt()
        if self._thread is not None:
            self._thread.join(5)


# ── 装上与卸下 ────────────────────────────────────────────────────────────────

_current: Optional[CloudLink] = None


def current() -> Optional[CloudLink]:
    return _current


class Running:
    """:func:`install` 的结果：``stop()`` 卸下 transport、停 ``ctl`` 监视。"""

    def __init__(self, link: CloudLink, watch: Optional[CtlWatch]) -> None:
        self.link, self.watch = link, watch

    def stop(self) -> None:
        global _current
        from .. import net

        if self.watch is not None:
            self.watch.stop()
        net.set_transport_factory(None)
        _current = None


def install(link: CloudLink, *, watch: bool = True, watch_factory=None, **options) -> Running:
    """装上隧道 transport（``net.set_transport_factory``）并起 ``ctl`` 监视。"""
    global _current
    from .. import net

    transport = ReloadingTransport(link, **options)
    net.set_transport_factory(lambda _kwargs: transport)
    _current = link
    ctl_watch = CtlWatch(link, watch_factory=watch_factory) if watch else None
    if ctl_watch is not None:
        ctl_watch.start()
    log.info("云端隧道已接上")
    return Running(link, ctl_watch)


def start(stop: Callable[[], bool], **kwargs) -> Optional[Running]:
    """云端模式启动时调：等隧道与会话（:func:`connect`）、装上 transport。等的时候要停回 None。"""
    from . import dir_watch

    kwargs.setdefault("watch_factory", dir_watch.open_watcher)
    link = connect(stop, **kwargs)
    return install(link) if link is not None else None


# ── 代录凭据 ──────────────────────────────────────────────────────────────────

def ensure_express_login(connection: str = CONNECTION, *, link: Optional[CloudLink] = None,
                         exists: Optional[Callable[[str], bool]] = None, fetch=None,
                         host: Optional[Callable[[], str]] = None) -> str:
    """代录要 Express 登录时：本用户凭据管理器里有就回 ``ok``；没有才经 ``ctl`` 向桥要（``cred_cloud.fetch``）。回原因码。"""
    from ..express_operator import credentials

    if exists is None:
        exists = credentials.exists
    if exists(credentials.target(connection)):
        return OK
    link = link or current()
    if link is None:
        log.info("云端凭据交接 · 事件=skip 原因=%s 连接=%s", NO_TUNNEL, connection)
        return NO_TUNNEL
    paired = (host or paired_host)()
    if not paired:
        log.info("云端凭据交接 · 事件=skip 原因=%s 连接=%s", UNPAIRED, connection)
        return UNPAIRED
    if fetch is None:
        from .cred_cloud import fetch
    try:
        return fetch(link.ctl_dir, epoch=link.session.epoch, connection=connection, host=paired)
    except Exception as exc:  # noqa: BLE001 - 隧道盘写不进等：代录照常按「没有登录」交人
        log.warning("云端凭据交接 · 事件=error 原因=%s 连接=%s", type(exc).__name__, connection)
        return "error"


__all__ = ["CloudLink", "CtlWatch", "ReloadingTransport", "Running", "check_ctl", "connect", "current",
           "ensure_express_login", "find_tunnel_root", "given_root", "install", "paired_device_key", "paired_host", "read_host", "run_pair", "start",
           "take_members", "take_session"]
