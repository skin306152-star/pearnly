# -*- coding: utf-8 -*-
"""CC 窗口进程与托盘进程之间的本地管道（2026-10-18，用户：「CC 桌面版必须要轻量设计并满足老机器能正常运行使用」）。

托盘进程（开机自启的 ``companion.exe``）只跑托盘、取任务、文件夹录入、按需请求这些后台线程，**不加载网页内核**；
CC 窗口（网页内核、对话页，gui_tomo.py）在另一个进程里：打开窗口时托盘拉起 ``companion.exe --window``（window_main.py），
关窗口这个进程就退出，网页内核（含 QtWebEngineProcess）随之释放；正在回复时等回复完再退，回复完成的通知照常弹。
窗口没开时整台电脑上没有网页内核在跑。

两边经本机同一用户的一条本地管道说话（QLocalServer，只本用户可连；托盘拉起窗口时经环境变量给一次性口令，第一句
``hello`` 带不对口令的连接直接断开），一行一条 JSON：

- 窗口 → 托盘：``hello``；``state`` / ``call``（对话页经桥调本机设置，tomo_bridge.py 在托盘这边照旧应答，带 ``id``
  的等 ``reply``）；``status``（窗口看得见没、在不在最前面、当前地址、对话页为什么停画）；``notify``（窗口里要弹的系统通知，
  托盘弹）；``bye``。
- 托盘 → 窗口：``show`` / ``ask`` / ``open_conversation`` / ``open_settings``（打开窗口做什么）；``changed`` / ``done``
  （桥的两个信号）；``pick``（弹选择文件夹框，带 ``id`` 等 ``reply``）；``click``（点了窗口转来的通知）；``lang``；``quit``。

收到的行先排队，回到事件循环再逐条处理（``_Channel``）：处理时可以弹框、等对方回复（嵌套事件循环），排在后面的照样能进来。
"""

from __future__ import annotations

import getpass
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .logger import get_logger

log = get_logger(__name__)

ENV_PIPE = "PEARNLY_CC_WINDOW_PIPE"
ENV_TOKEN = "PEARNLY_CC_WINDOW_TOKEN"
ENV_LANG = "PEARNLY_CC_WINDOW_LANG"
WINDOW_FLAG = "--window"
MAX_LINE = 32 * 1024 * 1024        # 一行最长（桥的状态 JSON 远小于此）；超长的整行丢掉
SPAWN_GRACE = 30.0                  # 拉起窗口进程后这么久还没连上，再点打开就重新拉起
MAX_PENDING = 8                     # 窗口进程连上前排着的打开命令最多留几条（只留最新的）
REQUEST_TIMEOUT_MS = 120_000        # 等对方回复的上限（弹选择文件夹框的不限时）
# ``status`` 里的 ``paused``：对话页为什么停画（空串 = 在画；visibility_watch.py）。托盘按它在 companion.log 记一行
PAUSE_HIDDEN, PAUSE_MINIMIZED = "hidden", "minimized"
PAUSE_LABELS = {PAUSE_HIDDEN: "窗口关着", PAUSE_MINIMIZED: "最小化", "locked": "锁屏", "display_off": "屏幕关了",
                "occluded": "被别的窗口完全挡住"}


def pipe_name(prefix: str = "PearnlyCCWindow-") -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return prefix + "".join(c for c in user if c.isalnum())[:40]


def encode(message: dict) -> bytes:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


class Lines:
    """把收到的字节切成一行一条 JSON；不合形状的行丢掉。"""

    def __init__(self, limit: int = MAX_LINE) -> None:
        self._buffer = bytearray()
        self._limit = limit
        self._skipping = False

    def feed(self, data: bytes) -> list:
        self._buffer.extend(data)
        out = []
        while True:
            end = self._buffer.find(b"\n")
            if end < 0:
                if len(self._buffer) > self._limit:   # 一行太长：丢掉到下一个换行为止
                    self._buffer.clear()
                    self._skipping = True
                return out
            line = bytes(self._buffer[:end])
            del self._buffer[:end + 1]
            if self._skipping:
                self._skipping = False
                continue
            try:
                message = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(message, dict) and isinstance(message.get("op"), str):
                out.append(message)


def window_command(*, frozen: bool, executable: str) -> list:
    """拉起窗口进程的命令：打包后是同一个 companion.exe 加 --window；开发时用同一个 Python 跑本包。"""
    if frozen:
        return [executable, WINDOW_FLAG]
    return [executable, "-m", __package__ + ".main", WINDOW_FLAG]


def window_cwd(*, frozen: bool, executable: str) -> str:
    if frozen:
        return os.path.dirname(executable)
    return str(Path(__file__).resolve().parents[2])   # companion/（-m src.companion.main 从这里找包）


class _Channel(QObject):
    """一条连接（两边共用）：发一行；收到的排队、回到事件循环逐条处理。带 ``id`` 的是请求，处理函数的返回值作 ``reply`` 回去；
    自己发出的请求在嵌套事件循环里等 ``reply``（等的时候别的消息照样处理）。"""

    def __init__(self, socket: QLocalSocket, handle: Callable[[dict], object], on_gone: Callable[[], None]) -> None:
        super().__init__()
        self._socket = socket
        self._handle = handle
        self._on_gone = on_gone
        self._lines = Lines()
        self._queue: deque = deque()
        self._scheduled = False
        self._waiting: dict = {}
        self.alive = True
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._gone)
        if socket.bytesAvailable():
            self._read()

    def send(self, op: str, **payload) -> bool:
        if not self.alive:
            return False
        try:
            self._socket.write(encode({"op": op, **payload}))
            self._socket.flush()
            return True
        except Exception:
            log.exception("本地管道发送失败：%s", op)
            return False

    def request(self, op: str, *, timeout_ms: Optional[int] = REQUEST_TIMEOUT_MS, **payload):
        """发请求，等对方回 ``reply``（返回其中的 result）；断开或超时返回 None。"""
        rid = uuid.uuid4().hex
        box: dict = {}
        loop = QEventLoop()
        self._waiting[rid] = (box, loop)
        try:
            if not self.send(op, id=rid, **payload):
                return None
            timer = None
            if timeout_ms:
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(loop.quit)
                timer.start(timeout_ms)
            if "result" not in box and self.alive:
                loop.exec()
            if timer is not None:
                timer.stop()
            return box.get("result")
        finally:
            self._waiting.pop(rid, None)

    def close(self) -> None:
        try:
            self._socket.disconnectFromServer()
        except Exception:
            pass

    def _read(self) -> None:
        try:
            data = bytes(self._socket.readAll().data())
        except Exception:
            return
        self._queue.extend(self._lines.feed(data))
        if self._queue and not self._scheduled:
            self._scheduled = True
            QTimer.singleShot(0, self._drain)

    def _drain(self) -> None:
        self._scheduled = False
        while self._queue:
            message = self._queue.popleft()
            if message["op"] == "reply":
                waiting = self._waiting.get(str(message.get("id") or ""))
                if waiting is not None:
                    waiting[0]["result"] = message.get("result")
                    waiting[1].quit()
                continue
            try:
                result = self._handle(message)
            except Exception:
                log.exception("本地管道消息处理失败：%s", message.get("op"))
                result = None
            rid = message.get("id")
            if isinstance(rid, str) and rid:
                self.send("reply", id=rid, result=result)

    def _gone(self) -> None:
        if not self.alive:
            return
        self.alive = False
        for _box, loop in list(self._waiting.values()):
            loop.quit()
        try:
            self._on_gone()
        except Exception:
            log.exception("本地管道断开处理失败")
        self._socket.deleteLater()   # 每开一次窗口一条连接：断了就放掉，托盘里不越攒越多


def _spawn(command: list, cwd: str, env: dict):
    return subprocess.Popen(command, cwd=cwd, env=env, close_fds=True, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _exited(process) -> bool:
    """拉起的窗口进程已经退出（还没连上就退了：崩溃、连不上托盘）。测试里换成假的、没有 poll 的当作还活着。"""
    poll = getattr(process, "poll", None)
    try:
        return callable(poll) and poll() is not None
    except Exception:
        return False


def allow_foreground(pid) -> None:
    """让窗口进程能把 CC 窗口拿到最前面（Windows 只许前台进程抢前台：托盘刚被点、快捷键刚按下时托盘有这个权利，
    交给窗口进程）。原来窗口在托盘进程里，不需要这一步。"""
    if sys.platform != "win32" or not isinstance(pid, int) or pid <= 0:
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(pid)
    except Exception:
        pass


class WindowHost(QObject):
    """（托盘进程）CC 窗口进程的管家：要打开窗口时没在跑就拉起它，连上后把排着的命令交过去；记着它报来的状态。"""

    gone = Signal()

    def __init__(self, *, on_request: Callable[[dict], object], on_notify: Callable[[dict], None],
                 lang: Callable[[], str] = lambda: "", spawn: Callable[[list, str, dict], object] = _spawn,
                 name: Optional[str] = None, clock: Callable[[], float] = time.monotonic,
                 foreground: Callable[[object], None] = allow_foreground) -> None:
        super().__init__()
        self._on_request = on_request
        self._on_notify = on_notify
        self._lang = lang
        self._spawn_process = spawn
        self._clock = clock
        self._foreground = foreground
        self._pid = None   # 窗口进程（hello 里报的）
        self._process = None   # 刚拉起、还没连上的窗口进程
        self._quitting = False
        self.name = name or pipe_name()
        self._token = secrets.token_urlsafe(24)
        self._channel: Optional[_Channel] = None
        self._pending: list = []
        self._spawned_at: Optional[float] = None
        self.visible = self.active = False
        self.url = ""
        self.paused = PAUSE_HIDDEN
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self._server.listen(self.name):
            QLocalServer.removeServer(self.name)   # 上次异常退出留下的
            if not self._server.listen(self.name):
                log.warning("CC 窗口管道启动失败：%s", self._server.errorString())
        self._server.newConnection.connect(self._accept)

    @property
    def connected(self) -> bool:
        return self._channel is not None and self._channel.alive

    def open(self, command: dict) -> None:
        """要窗口做一件事（show / ask / open_conversation / open_settings）：连着就交过去，没连着就排着并拉起窗口进程。"""
        if self.connected:
            assert self._channel is not None
            self._foreground(self._pid)
            self._channel.send(**command)
            return
        if self._quitting:
            return
        died = self._spawned_at is not None and _exited(self._process)
        if died:
            # 上次拉起的没连上就退了：排着的旧命令（例如早先的快捷提问）不再重放，只做这一次点的
            log.warning("CC 窗口进程没连上就退出了，重新拉起")
            self._pending = []
        self._pending = (self._pending + [dict(command)])[-MAX_PENDING:]
        now = self._clock()
        if died or self._spawned_at is None or now - self._spawned_at > SPAWN_GRACE:
            self._spawned_at = now
            self._start()

    def send(self, op: str, **payload) -> bool:
        return bool(self.connected and self._channel is not None and self._channel.send(op, **payload))

    def pick(self, title: str, start: str) -> str:
        """请窗口弹选择文件夹框（以窗口为父、模态），等它选好；没选、窗口没了都是空串。"""
        if not self.connected:
            return ""
        assert self._channel is not None
        result = self._channel.request("pick", timeout_ms=None, title=str(title or ""), start=str(start or ""))
        return result if isinstance(result, str) else ""

    def quit(self) -> None:
        """退出 CC：让窗口进程也退出，不再接新连接（已经接上、还没打招呼的那个也叫它退出）。"""
        self._quitting = True
        self._pending = []
        self.send("quit")
        self._server.close()

    def _start(self) -> None:
        frozen = bool(getattr(sys, "frozen", False))
        env = dict(os.environ)
        env.update({ENV_PIPE: self.name, ENV_TOKEN: self._token, ENV_LANG: str(self._lang() or "")})
        if frozen:
            env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # 打包程序拉起自己：当作独立的新实例，不沿用本进程的解包环境
        try:
            self._process = self._spawn_process(window_command(frozen=frozen, executable=sys.executable),
                                                window_cwd(frozen=frozen, executable=sys.executable), env)
            self._foreground(getattr(self._process, "pid", self._process))
            log.info("打开 CC 窗口（窗口进程）")
        except Exception:
            self._spawned_at = None
            log.exception("拉起 CC 窗口进程失败")

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            holder: dict = {}

            def first(message: dict, socket=socket, holder=holder):
                if holder.get("ok"):
                    # 只听当前那个窗口进程的（后连上、被叫退出的那个说什么都不算）
                    return self._handle(message) if holder["channel"] is self._channel else None
                if message.get("op") != "hello" or not secrets.compare_digest(str(message.get("token") or ""),
                                                                              self._token):
                    log.warning("CC 窗口管道：口令不对，断开")
                    holder["channel"].alive = False
                    socket.disconnectFromServer()
                    return None
                holder["ok"] = True
                self._adopt(holder["channel"], message.get("pid"))
                return None

            holder["channel"] = _Channel(socket, first, lambda holder=holder: self._lost(holder.get("channel")))

    def _adopt(self, channel: _Channel, pid=None) -> None:
        if self._quitting or (self.connected and self._channel is not channel):
            # 已经有一个窗口进程（两次快速点开）、或 CC 正在退出：后来的这个退出（它还没建窗口，见 window_main.run）
            channel.send("quit")
            return
        self._channel = channel
        self._pid = pid if isinstance(pid, int) else None
        self._spawned_at = None
        self._process = None
        pending, self._pending = self._pending, []
        for command in pending or [{"op": "show"}]:
            channel.send(**command)

    def _lost(self, channel) -> None:
        if channel is not self._channel:
            return
        self._channel = None
        self._pid = None
        self.visible = self.active = False
        self.url = ""
        self.paused = PAUSE_HIDDEN
        self.gone.emit()

    def _paused(self, paused: str) -> None:
        """对话页停画 / 恢复记一行（窗口开着时锁屏、关屏、最小化、被挡住；开 / 关窗口另有日志）。"""
        previous, self.paused = self.paused, paused
        if paused == previous or PAUSE_HIDDEN in (paused, previous):
            return
        if paused:
            log.info("CC 窗口看不见（%s）：页面已暂停", PAUSE_LABELS.get(paused, paused))
        else:
            log.info("CC 窗口看得见：页面已恢复")

    def _handle(self, message: dict):
        op = message["op"]
        if isinstance(message.get("url"), str):
            self.url = message["url"]
        if op == "status":
            self.visible = bool(message.get("visible"))
            self.active = bool(message.get("active"))
            self._paused(str(message.get("paused") or ""))
            return None
        if op == "notify":
            self._on_notify(message)
            return None
        if op == "bye":
            self._lost(self._channel)   # 窗口进程正在退出：之后再打开就重新拉起，不往要退的进程里发命令
            return None
        if op in ("state", "call"):
            return self._on_request(message)
        return None


class TrayLink(QObject):
    """（窗口进程）连到托盘：拿托盘给的口令打招呼；托盘发来的命令交给 ``set_handler`` 设的处理函数；断了发 ``lost``。"""

    lost = Signal()

    def __init__(self, name: str, token: str) -> None:
        super().__init__()
        self._name = name
        self._token = token
        self._socket = QLocalSocket(self)
        self._channel: Optional[_Channel] = None
        self._handler: Callable[[dict], object] = lambda message: None

    def set_handler(self, handler: Callable[[dict], object]) -> None:
        """托盘的请求（pick）要同步回结果：处理函数的返回值作 reply；没有 id 的命令也交给它。"""
        self._handler = handler

    def connect_to_tray(self, timeout_ms: int = 5000) -> bool:
        self._socket.connectToServer(self._name)
        if not self._socket.waitForConnected(timeout_ms):
            return False
        self._channel = _Channel(self._socket, lambda message: self._handler(message), self.lost.emit)
        return self._channel.send("hello", token=self._token, pid=os.getpid())

    @property
    def alive(self) -> bool:
        return self._channel is not None and self._channel.alive

    def send(self, op: str, **payload) -> bool:
        return bool(self._channel is not None and self._channel.send(op, **payload))

    def request(self, op: str, **payload):
        if self._channel is None:
            return None
        return self._channel.request(op, **payload)

    def close(self) -> None:
        """窗口进程要退出了：发完的话发出去再断开，不再当作「托盘断了」处理。"""
        if self._channel is not None:
            self._channel.alive = False
        self._socket.disconnectFromServer()
