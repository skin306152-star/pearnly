# -*- coding: utf-8 -*-
"""云端 Express 连接窗口（只在 Windows 上用）：CC 自己的一个**可见**窗口，任务栏有图标，标题写明是云端 Express 连接，
默认最小化；人随时可以还原查看、随时关掉——关掉就是断开，不自动重开（用户 11-16）。

窗口里放 Windows 自带的远程桌面 ActiveX 控件（``MsTscAx.MsTscAx``，PySide6 ``QtAxContainer.QAxWidget``）：
- 连接参数只经控件的正式属性给（``Server``、``UserName``、``Domain``、``AdvancedSettingsN`` 的 ``RDPPort`` /
  ``ClearTextPassword`` / 各项重定向 / ``AuthenticationLevel``），不写、不打开 .rdp 文件；
- 控件自己弹的任何确认框（重定向确认、证书、会话争用……）照常弹给人看，本模块不去点；
- 断开、登录失败、被挤只看控件自己的事件（``OnConnected`` / ``OnLoginComplete`` / ``OnDisconnected`` /
  ``OnLogonError`` / ``OnFatalError``，加 ``ExtendedDisconnectReason`` 与 ``GetErrorDescription``），判定见 ``reasons``，
  状态与重连见 ``session``；控件自带的自动重连关掉，只由 ``session`` 一处决定重连。
- 密码：每次起连接时向注入的 ``password_provider`` 要一次，只经 ``ClearTextPassword`` 属性交给控件，随即丢掉局部变量；
  不写日志、不存成员变量。

驱动器只映射隧道盘、开网络级身份验证（CredSSP）这两项在控件的**非脚本接口**上（``IMsRdpClientNonScriptable3``
的 ``DriveCollection``、``IMsRdpClientNonScriptable5`` 的 ``EnableCredSspSupport``），IDispatch 够不着，由注入的
``nonscriptable`` 函数做；没给就不连（绝不退回「映射全部盘」）。这一块要在 Windows 真机上写定、实测。

不是 Windows、没有 ``PySide6.QtAxContainer``：导入本模块不报错，构造窗口时抛 :class:`WindowUnavailable`。
现有代码还没有地方导入本模块。
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ..logger import get_logger
from . import rdp
from .session import CloudSession, Snapshot

log = get_logger(__name__)

CONTROL = "MsTscAx.MsTscAx"
# 窗口标题（还没有四语词条；接进界面前登记进 companion i18n，这里先只留常量）
WINDOW_TITLE = "CC · 云端 Express 连接"
# 依次试的高级设置子对象：要 RedirectClipboard（第 6 版起）与 AuthenticationLevel（第 4 版起）
ADVANCED_SETTINGS = ("AdvancedSettings9", "AdvancedSettings8", "AdvancedSettings7", "AdvancedSettings6")

try:  # pragma: no cover - 只在 Windows 的 PySide6 里有
    from PySide6.QtAxContainer import QAxWidget as _QAxWidget
    from PySide6.QtCore import SIGNAL as _SIGNAL
    from PySide6.QtCore import QTimer as _QTimer
    from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout
    from PySide6.QtWidgets import QWidget as _QWidget
except Exception:  # noqa: BLE001 - 非 Windows / 没装 Addons：只是不能用
    _QAxWidget = None
    _QWidget = object  # type: ignore[assignment,misc]


class WindowUnavailable(RuntimeError):
    """这台电脑上建不了云端连接窗口（不是 Windows、没有 QtAxContainer、控件建不出来）。"""


class ControlSettingError(RuntimeError):
    """控件某项属性设不进去（文本只带属性名，不带值）。设不全就不连。"""


class NonScriptableUnavailable(RuntimeError):
    """没有可用的非脚本接口设置（只映射隧道盘、CredSSP）。"""


def available() -> bool:
    return sys.platform == "win32" and _QAxWidget is not None


@dataclass(frozen=True)
class ConnectParams:
    """一次连接的参数（不含密码）。构造时按 ``rdp`` 的规则校验。"""

    host: str
    port: int
    user: str
    domain: str
    tunnel_drive: str
    width: int = rdp.DEFAULT_WIDTH
    height: int = rdp.DEFAULT_HEIGHT

    @classmethod
    def make(cls, *, host: str, port: int, user: str, domain: str, tunnel_drive: str,
             width: int = rdp.DEFAULT_WIDTH, height: int = rdp.DEFAULT_HEIGHT) -> "ConnectParams":
        user, domain = rdp.split_account(domain + "\\" + user if domain else user)
        if not rdp.MIN_SIZE <= int(width) <= rdp.MAX_WIDTH or not rdp.MIN_SIZE <= int(height) <= rdp.MAX_HEIGHT:
            raise rdp.RdpValueError("size")
        return cls(rdp.host(host), rdp.port(port), user, domain, rdp.drive(tunnel_drive), int(width), int(height))


@dataclass(frozen=True)
class NonScriptable:
    """交给 ``nonscriptable`` 函数做的两项：开 CredSSP；驱动器只映射 ``drive`` 这一个盘（其余全部关掉）。"""

    enable_credssp: bool
    drive: str


def _set(obj: Any, name: str, value: Any) -> None:
    if obj is None or not obj.setProperty(name, value):
        raise ControlSettingError(name)


def apply_settings(ax: Any, params: ConnectParams, password: str, *,
                   nonscriptable: Optional[Callable[[Any, NonScriptable], None]]) -> None:
    """把参数写进控件（``ax`` 需有 ``setProperty`` / ``querySubObject``，即 QAxBase）。任何一项设不进去就抛错、不连。

    密码只进 ``ClearTextPassword``；本函数不记日志、不留引用。"""
    if not isinstance(password, str) or not password:
        raise ValueError("password")
    if nonscriptable is None:
        raise NonScriptableUnavailable("drive redirection / CredSSP")
    _set(ax, "Server", params.host)
    _set(ax, "UserName", params.user)
    _set(ax, "Domain", params.domain)
    _set(ax, "DesktopWidth", params.width)
    _set(ax, "DesktopHeight", params.height)
    _set(ax, "ColorDepth", 16)
    adv = None
    for name in ADVANCED_SETTINGS:
        adv = ax.querySubObject(name)
        if adv is not None:
            break
    if adv is None:
        raise ControlSettingError("AdvancedSettings")
    _set(adv, "RDPPort", params.port)
    _set(adv, "AuthenticationLevel", 0)       # 同 .rdp 的 authentication level:i:0（11-15 实测，同 TSplus）
    _set(adv, "EnableAutoReconnect", False)   # 重连只由 session 决定
    _set(adv, "RedirectClipboard", False)
    _set(adv, "RedirectPrinters", False)
    _set(adv, "RedirectPorts", False)
    _set(adv, "RedirectSmartCards", False)
    _set(adv, "RedirectDrives", True)         # 总开关开着，具体只留隧道盘由 nonscriptable 做
    nonscriptable(ax, NonScriptable(enable_credssp=True, drive=params.tunnel_drive))
    _set(adv, "ClearTextPassword", password)


class EventRouter:
    """控件事件 → ``CloudSession``（不依赖 Qt，便于在非 Windows 上测）。

    ``read_extended``：当场读控件的 ``ExtendedDisconnectReason``；``describe``：控件的 ``GetErrorDescription``，
    结果只交给 ``on_change``（给界面显示），不进日志（系统文案里可能带主机名）。"""

    def __init__(self, session: CloudSession, *, read_extended: Callable[[], int],
                 describe: Callable[[int, int], str], on_change: Callable[[Snapshot, str], None]):
        self._session = session
        self._read_extended = read_extended
        self._describe = describe
        self._on_change = on_change

    def _changed(self, description: str = "") -> None:
        self._on_change(self._session.snapshot(), description)

    def on_connecting(self) -> None:
        self._changed()

    def on_connected(self) -> None:
        self._session.on_connected()
        self._changed()

    def on_login_complete(self) -> None:
        self._session.on_login_complete()
        log.info("云端 Express 已登录")
        self._changed()

    def on_disconnected(self, disc_reason: int) -> None:
        try:
            extended = int(self._read_extended() or 0)
        except Exception:  # noqa: BLE001 - 读不到扩展原因就按 0
            extended = 0
        outcome = self._session.on_disconnected(int(disc_reason), extended)
        try:
            description = str(self._describe(int(disc_reason), extended) or "")
        except Exception:  # noqa: BLE001
            description = ""
        log.info("云端 Express 断开：%s → %s", self._session.snapshot().last_reason or f"{disc_reason}/{extended}",
                 outcome.value)
        self._changed(description)

    def on_logon_error(self, code: int) -> None:
        self._session.on_logon_error(int(code))
        log.info("云端 Express 登录事件 %d", int(code))
        self._changed()

    def on_fatal_error(self, code: int) -> None:
        self._session.on_fatal_error(int(code))
        log.info("云端 Express 控件出错 %d", int(code))
        self._changed()


class CloudWindow(_QWidget):  # type: ignore[misc,valid-type]
    """云端 Express 连接窗口。构造后调 :meth:`start` 以最小化显示并开始连接。

    ``password_provider``：每次起连接时调一次，回这次要用的密码（例如请人输入，或由上层从它自己的保管处取）。
    ``nonscriptable``：见模块说明。``on_change(snapshot, description)``：状态变了时通知上层（托盘、对话页）。"""

    def __init__(self, params: ConnectParams, *, password_provider: Callable[[], str],
                 nonscriptable: Optional[Callable[[Any, NonScriptable], None]],
                 on_change: Optional[Callable[[Snapshot, str], None]] = None,
                 clock: Callable[[], float] = time.monotonic, icon: Any = None):
        if not available():
            raise WindowUnavailable("cloud Express window needs Windows with PySide6.QtAxContainer")
        super().__init__()
        self._params = params
        self._password_provider = password_provider
        self._nonscriptable = nonscriptable
        self._notify = on_change or (lambda snapshot, description: None)
        self.last_description = ""
        self.setWindowTitle(WINDOW_TITLE)
        if icon is None:
            from ..gui_icon import pearnly_icon
            icon = pearnly_icon()
        self.setWindowIcon(icon)
        self._ax = _QAxWidget(self)
        if not self._ax.setControl(CONTROL):
            raise WindowUnavailable("MsTscAx control")
        layout = _QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._ax)
        self.resize(params.width, params.height)
        self.session = CloudSession(self._open, self._close, clock)
        self.events = EventRouter(self.session, read_extended=lambda: self._ax.property("ExtendedDisconnectReason"),
                                  describe=self._error_description, on_change=self._on_change)
        self._timer = _QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._retry_due)
        for signature, handler in (
            ("OnConnecting()", self.events.on_connecting),
            ("OnConnected()", self.events.on_connected),
            ("OnLoginComplete()", self.events.on_login_complete),
            ("OnDisconnected(int)", self.events.on_disconnected),
            ("OnLogonError(int)", self.events.on_logon_error),
            ("OnFatalError(int)", self.events.on_fatal_error),
        ):
            self._ax.connect(self._ax, _SIGNAL(signature), handler)

    # ---- 对外 ----
    def start(self) -> None:
        """最小化显示（任务栏有图标）并开始连接。"""
        self.showMinimized()
        self.connect_now()

    def connect_now(self) -> bool:
        """人点「连接」：停住 / 等重连时也立刻连。"""
        return self.session.connect()

    def disconnect_now(self) -> None:
        """人点「断开」：断开、不重连，窗口留着。"""
        self.session.user_close()
        self._schedule()

    # ---- 注入 session 的两个动作 ----
    def _open(self) -> None:
        password = self._password_provider()
        try:
            apply_settings(self._ax, self._params, password, nonscriptable=self._nonscriptable)
        finally:
            del password
        self._ax.dynamicCall("Connect()")

    def _close(self) -> None:
        try:
            if int(self._ax.property("Connected") or 0):
                self._ax.dynamicCall("Disconnect()")
        except Exception:  # noqa: BLE001 - 控件已经不在了
            pass

    # ---- 内部 ----
    def _error_description(self, disc_reason: int, extended: int) -> str:
        return str(self._ax.dynamicCall("GetErrorDescription(uint,uint)", disc_reason, extended) or "")

    def _on_change(self, snapshot: Snapshot, description: str) -> None:
        if description:
            self.last_description = description
        self._schedule()
        self._notify(snapshot, description)

    def _schedule(self) -> None:
        wait = self.session.seconds_until_retry()
        if wait is None:
            self._timer.stop()
        else:
            self._timer.start(int(wait * 1000) + 50)

    def _retry_due(self) -> None:
        try:
            started = self.session.tick()
        except Exception as error:  # noqa: BLE001 - 设不进去：停在未连接，等人点「连接」
            log.info("云端 Express 重连没起来：%s", type(error).__name__)
            self._notify(self.session.snapshot(), "")
            return
        if not started:
            self._schedule()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        """关窗 = 断开，不重连。"""
        self._timer.stop()
        self.session.user_close()
        self._notify(self.session.snapshot(), "")
        super().closeEvent(event)
