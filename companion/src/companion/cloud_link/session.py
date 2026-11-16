# -*- coding: utf-8 -*-
"""云端 Express 连接的状态机（纯逻辑）。时钟、「起连接」「关连接」由调用方注入；窗口（``window``）把控件事件喂进来，
并按 :meth:`CloudSession.seconds_until_retry` 定时调 :meth:`CloudSession.tick`。

规则（用户 11-16）：
- 未连接 → 正在连接（人点「连接」或到了重连时间）→ 已连接（``OnLoginComplete``；``OnConnected`` 只是网络通了）；
- 登录失败、被挤：停住、记下标记，**不自动重连**，等人再点「连接」（点了清标记）；
- 普通断线：退避重连 1、2、4、8 分钟，之后每 15 分钟；连上并稳定 10 分钟后才把退避清零（防止连上就掉反复每分钟重连）；
- 人关窗 / 点「断开」：断开，不重连；之后控件再报的断开事件不改结果。

只认当前这一次连接的事件：未连接 / 停住时收到的断开、登录事件一律忽略。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from . import reasons
from .reasons import Outcome

RETRY_DELAYS = (60, 120, 240, 480)
RETRY_MAX = 15 * 60
STABLE_SECONDS = 10 * 60


class State(str, Enum):
    IDLE = "idle"                # 未连接（没连过、人断开）
    CONNECTING = "connecting"    # 正在连接
    CONNECTED = "connected"      # 已连接（已登录）
    RETRY_WAIT = "retry_wait"    # 普通断线，等重连
    HALTED = "halted"            # 登录失败 / 被挤：停住等人


def retry_delay(attempt: int) -> int:
    """第 ``attempt`` 次重连（从 0 起）前等几秒：60、120、240、480，之后 900。"""
    if attempt < 0:
        attempt = 0
    return RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else RETRY_MAX


@dataclass(frozen=True)
class Snapshot:
    state: State
    halt: Optional[Outcome]          # 停住的原因（LOGIN_FAILED / REPLACED），其余为 None
    last_outcome: Optional[Outcome]  # 最近一次断开的结果
    last_reason: str                 # 最近一次断开的原因名（reasons.reason_name），给日志
    attempt: int                     # 已经连续重连了几次
    retry_at: Optional[float]        # 下次重连的时刻（时钟同注入的 clock）


class CloudSession:
    def __init__(self, open_connection: Callable[[], None], close_connection: Callable[[], None],
                 clock: Callable[[], float]):
        self._open = open_connection
        self._close = close_connection
        self._clock = clock
        self._state = State.IDLE
        self._halt: Optional[Outcome] = None
        self._last_outcome: Optional[Outcome] = None
        self._last_reason = ""
        self._attempt = 0
        self._retry_at: Optional[float] = None
        self._connected_since: Optional[float] = None
        self._login_failed = False   # 这次连接里 OnLogonError 报过登录真失败

    # ---- 读状态 ----
    @property
    def state(self) -> State:
        return self._state

    def snapshot(self) -> Snapshot:
        return Snapshot(self._state, self._halt, self._last_outcome, self._last_reason, self._attempt, self._retry_at)

    def seconds_until_retry(self) -> Optional[float]:
        """等重连时还差几秒（不会是负数）；不在等重连回 None。"""
        if self._state is not State.RETRY_WAIT or self._retry_at is None:
            return None
        return max(0.0, self._retry_at - self._clock())

    # ---- 人的操作 ----
    def connect(self) -> bool:
        """人点「连接」。未连接、停住、等重连时都立刻连（清停住标记、退避清零）；已在连 / 已连上不动。"""
        if self._state in (State.CONNECTING, State.CONNECTED):
            return False
        self._halt = None
        self._attempt = 0
        self._start()
        return True

    def user_close(self) -> None:
        """人关窗或点「断开」：断开，不重连。"""
        was_live = self._state in (State.CONNECTING, State.CONNECTED)
        self._enter_idle(Outcome.USER_CLOSED, "user_close")
        if was_live:
            self._close()

    # ---- 定时 ----
    def tick(self) -> bool:
        """到了重连时间就起连接；起了回 True。"""
        if self._state is State.RETRY_WAIT and self._retry_at is not None and self._clock() >= self._retry_at:
            self._start()
            return True
        return False

    # ---- 控件事件 ----
    def on_connected(self) -> None:
        """``OnConnected``：网络与协议通了，还没登录完，状态不变。"""

    def on_login_complete(self) -> None:
        """``OnLoginComplete``：已连接。"""
        if self._state is not State.CONNECTING or self._login_failed:
            return
        self._state = State.CONNECTED
        self._connected_since = self._clock()
        self._retry_at = None

    def on_logon_error(self, code: int) -> None:
        """``OnLogonError``：登录真失败就停住并关掉这次连接（远端可能停在登录界面）；其余只是提示，不改状态。"""
        if self._state not in (State.CONNECTING, State.CONNECTED):
            return
        if not reasons.logon_error_is_failure(code):
            return
        self._login_failed = True
        self._enter_halt(Outcome.LOGIN_FAILED, f"logon_error_{reasons.signed32(code)}")
        self._close()

    def on_disconnected(self, disc_reason: int, extended_reason: Optional[int] = None) -> Outcome:
        """``OnDisconnected``（扩展原因由窗口当场读 ``ExtendedDisconnectReason`` 交来）。回本次判定的结果。"""
        name = reasons.reason_name(disc_reason, extended_reason)
        if self._state not in (State.CONNECTING, State.CONNECTED):
            # 人已断开 / 已停住 / 本来就在等：这条是上一次连接的尾巴，不改结果
            return self._last_outcome or Outcome.USER_CLOSED
        outcome = reasons.classify_disconnect(disc_reason, extended_reason)
        if self._login_failed:
            outcome = Outcome.LOGIN_FAILED
        self._apply(outcome, name)
        return outcome

    def on_fatal_error(self, code: int) -> None:
        """``OnFatalError``：控件自己出错，按普通断线退避。"""
        if self._state not in (State.CONNECTING, State.CONNECTED):
            return
        self._apply(reasons.classify_fatal(code), f"fatal_{int(code)}")
        self._close()

    # ---- 内部 ----
    def _start(self) -> None:
        self._state = State.CONNECTING
        self._retry_at = None
        self._connected_since = None
        self._login_failed = False
        try:
            self._open()
        except Exception as error:
            # 参数 / 控件设不进去：回到未连接、不排重连（重试也一样设不进去），异常交给调用方
            self._enter_idle(None, f"open_failed_{type(error).__name__}")
            raise

    def _apply(self, outcome: Outcome, name: str) -> None:
        if outcome in (Outcome.LOGIN_FAILED, Outcome.REPLACED):
            self._enter_halt(outcome, name)
        elif outcome is Outcome.USER_CLOSED:
            self._enter_idle(outcome, name)
        else:
            now = self._clock()
            if self._connected_since is not None and now - self._connected_since >= STABLE_SECONDS:
                self._attempt = 0
            self._retry_at = now + retry_delay(self._attempt)
            self._attempt += 1
            self._state = State.RETRY_WAIT
            self._connected_since = None
            self._last_outcome, self._last_reason = outcome, name

    def _enter_halt(self, outcome: Outcome, name: str) -> None:
        self._state = State.HALTED
        self._halt = outcome
        self._retry_at = None
        self._connected_since = None
        self._last_outcome, self._last_reason = outcome, name

    def _enter_idle(self, outcome: Optional[Outcome], name: str) -> None:
        self._state = State.IDLE
        self._halt = None
        self._retry_at = None
        self._connected_since = None
        self._attempt = 0
        self._last_outcome, self._last_reason = outcome, name
