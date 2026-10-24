# -*- coding: utf-8 -*-
"""账套清单后台扫描的节奏（2026-10-18：老电脑也不长时间占核，局域网服务器断开 / 很慢时不卡、不反复整扫）。

- 「低优先级」靠自己让：扫描线程不改 Windows 线程优先级（``THREAD_PRIORITY_BELOW_NORMAL`` / 后台模式都不用）。CC 的托盘、拉取任务、
  扫描都在一个 Python 进程里共用解释器锁（GIL）：降了优先级的扫描线程拿着锁时被别的程序抢走 CPU、长时间轮不到，CC 自己的托盘与
  拉取任务那条线就跟着等锁卡住（优先级反转）。所以由下面的 ``Pace`` 主动让出 CPU：扫描最多占 15% 个核，其余都留给别的程序。
- ``Pace``：每份账套读完调一次。按本线程累计用掉的 CPU 让出时间，使「CPU 时间 / 经过时间」不超过 ``duty``
  （平时 15% 个核：测试机原来首扫平均约 17%；网页「重新读取」有人在等，放宽到一半个核）；读网络盘等着的时间也算经过时间，
  所以以读盘为主的扫描几乎不额外变慢；Windows 的线程 CPU 计时 15.6 毫秒一跳，按累计算，跳得粗也不影响。某份账套光读文件就超过
  ``slow_seconds``（服务器中途断开、读一下要等系统超时）→ 抛 ``HostStalled``，这一轮这台服务器上的数据根全部跳过。
- ``HostGate``：数据根所在的服务器（UNC 的 ``\\\\服务器\\共享``、网络盘符）每轮先问一次它在不在：只读看一眼这一轮
  要读的第一个数据根（``os.stat``），限时 ``probe_seconds``，卡住也不等（放在守护线程里，卡着期间不再问第二次）。
  超时、或 Windows 报的是网络类错误（找不到网络路径、网络名不再可用、信号灯超时……）→ 这台的数据根这一轮全跳过，
  之后按 1、2、4、8 分钟退避（最多 15 分钟）再问，期间一次网络读都不做；问通了 30 秒内同一台不再问。
  只是这个文件夹不在 / 没权限（服务器是好的）不算服务器不在：照常往下读，登记表读不到的那个数据根照旧报空。
  本机盘（Windows 报固定盘 / 移动盘 / 光驱 / 内存盘的盘符）不设关卡：本机盘不会「断开」，一时读得慢（老硬盘、系统正忙）
  也不该把本机的账套（通常就是所选的那份）当成服务器不在、一分钟不报。

全部只读：只对数据根做 ``os.stat``，不建、不写任何文件。
"""

from __future__ import annotations

import ntpath
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .logger import get_logger

log = get_logger(__name__)

DUTY = 0.15  # 后台扫描最多用 15% 个核
FORCED_DUTY = 0.5  # 网页「重新读取」：有人在等，放宽到一半个核
MAX_YIELD_SECONDS = 1.0  # 一次最多让出这么久（累计 CPU 欠账很多时分几次还）
SLOW_ACCOUNT_SECONDS = 15.0  # 一份账套光读文件就超过这么久 → 这台服务器这一轮不再读
PROBE_SECONDS = 5.0  # 问服务器在不在最多等这么久
PROBE_FRESH_SECONDS = 30.0  # 问通了之后这么久内同一台不再问
BACKOFF_SECONDS = (60.0, 120.0, 240.0, 480.0, 900.0)  # 连续问不通时下一次再问的间隔

_kernel32 = None


def _win32():
    """自己的一份 kernel32（设函数签名不影响别处的 ``ctypes.windll``）；不是 Windows 回 None。"""
    global _kernel32
    if sys.platform != "win32":
        return None
    if _kernel32 is None:
        import ctypes
        from ctypes import wintypes

        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.GetDriveTypeW.restype = wintypes.UINT
        dll.GetDriveTypeW.argtypes = (wintypes.LPCWSTR,)
        _kernel32 = dll
    return _kernel32


class HostStalled(Exception):
    """数据根所在服务器这一轮读不动了（读一份账套超时）：这台上的数据根这一轮全部跳过。"""

    def __init__(self, host: str) -> None:
        super().__init__(host)
        self.host = host


# GetDriveTypeW：2 移动盘、3 固定盘、5 光驱、6 内存盘 = 本机盘；4 网络盘、0 / 1（未知、没有这个盘）照网络盘处理
_LOCAL_DRIVE_TYPES = frozenset({2, 3, 5, 6})


def drive_type(drive: str) -> int:
    """Windows ``GetDriveTypeW("S:\\")``（只查本机的盘符表，不碰网络）；别的系统 / 取不到回 0（未知）。"""
    try:
        kernel32 = _win32()
        if kernel32 is None:
            return 0
        return int(kernel32.GetDriveTypeW(drive.rstrip("\\") + "\\"))
    except Exception:  # noqa: BLE001 - 取不到就当未知（照网络盘设关卡）
        return 0


def host_of(path, *, drive_type: Callable[[str], int] = drive_type) -> str:
    """数据根所在的服务器 / 网络盘：``\\\\server\\share``、``s:``（不分大小写）；本机盘、相对路径、别的系统的路径为空串（不设关卡）。"""
    text = str(path or "").strip().replace("/", "\\")
    if not text:
        return ""
    drive = ntpath.splitdrive(text)[0].rstrip("\\")
    if not drive:
        return ""
    if not drive.startswith("\\\\") and drive_type(drive) in _LOCAL_DRIVE_TYPES:
        return ""
    return drive.casefold()


# Windows 网络类错误：网络路径 / 网络名找不到、网络名不再可用、意外的网络错误、信号灯超时、网络不可达、没连上……
# 文件夹不在（2、3）、没权限（5）不在里面：服务器是好的，只是这一个数据根读不到。
# 1201 = 记住的网络盘这会儿没连上、1203 = 网络路径不对或网络不通（断开的映射盘重连失败时常见）。
_NETWORK_ERRORS = frozenset({51, 53, 54, 55, 59, 64, 65, 67, 121, 1201, 1203, 1219, 1222, 1231, 1232, 2250})


def network_error(exc: BaseException) -> bool:
    return getattr(exc, "winerror", None) in _NETWORK_ERRORS


def _stat_probe(path: str) -> bool:
    """False = 服务器不在（网络类错误）；文件夹在、或只是这个文件夹不在 / 没权限 → True。"""
    try:
        os.stat(path)
    except OSError as exc:
        return not network_error(exc)
    return True


@dataclass
class _HostState:
    fails: int = 0
    retry_at: float = 0.0
    ok_at: float = -1e18
    probing: bool = False


class HostGate:
    """按服务器记「在不在」与退避（见文件头）。线程安全；整个程序共用一个（``GATE``）。"""

    def __init__(
        self,
        probe: Callable[[str], bool] = _stat_probe,
        *,
        probe_seconds: float = PROBE_SECONDS,
        backoff: tuple = BACKOFF_SECONDS,
        fresh_seconds: float = PROBE_FRESH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        drive_type: Callable[[str], int] = drive_type,
    ) -> None:
        self._drive_type = drive_type
        self._probe = probe
        self._probe_seconds = probe_seconds
        self._backoff = backoff
        self._fresh = fresh_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # 探测结束（放开 probing）时叫醒在等同一台服务器结果的线程
        self._probed = threading.Condition(self._lock)
        self._hosts: Dict[str, _HostState] = {}

    def reachable(self, path, *, retry: bool = False) -> bool:
        """这个数据根的服务器现在能不能读：退避期内直接回 False（不做任何网络读）；到点了限时问一次。
        ``retry``（网页「重新读取」，有人在等）：不等退避到点，马上再问一次（上一次卡住还没回来的仍不重复问）。"""
        host = host_of(path, drive_type=self._drive_type)
        if not host:
            return True
        with self._lock:
            state = self._hosts.setdefault(host, _HostState())
            if state.probing:
                # 别的线程正在问同一台：等它的结果（最多一次探测的时限），不当成「不在」。原来直接回 False、不留日志，
                # 两个成员同时扫时后来的那个把这台服务器上的账套全当空、按完整清单报上去（10-24 验收问题 21：Cowork 685 → 461）。
                self._probed.wait_for(lambda: not state.probing, self._probe_seconds)
                if state.probing:
                    return False  # 那一下卡住了：按不在算，它返回前不再问第二次
                return not state.fails and self._clock() - state.ok_at < self._fresh
            now = self._clock()
            if now < state.retry_at and not retry:
                return False
            if not state.fails and now - state.ok_at < self._fresh:
                return True
            state.probing = True
        ok, finished = self._probe_limited(host, str(path))
        with self._lock:
            if ok:
                state.fails, state.retry_at, state.ok_at = 0, 0.0, self._clock()
            else:
                self._failed(host, state)
            if finished:  # 结果记好了才放开、叫醒在等的线程（卡住的那一下由它自己返回时放开）
                state.probing = False
                self._probed.notify_all()
        return ok

    def host(self, path) -> str:
        """这个数据根归哪台服务器（本机盘为空串：不设关卡）。"""
        return host_of(path, drive_type=self._drive_type)

    def fail(self, path) -> None:
        """扫描中途发现这台服务器读不动（HostStalled）：记一次失败，按退避等下一次。"""
        host = self.host(path)
        if not host:
            return
        with self._lock:
            self._failed(host, self._hosts.setdefault(host, _HostState()))

    def _failed(self, host: str, state: _HostState) -> None:
        state.fails += 1
        delay = self._backoff[min(state.fails, len(self._backoff)) - 1]
        state.retry_at = self._clock() + delay
        state.ok_at = -1e18
        log.info("[accounts] 服务器 %s 读不到，%d 秒后再试（连续第 %d 次）", _label(host), int(delay), state.fails)

    def _probe_limited(self, host: str, path: str) -> tuple:
        """在守护线程里问 → ``(在不在, 按时问完了没有)``；超过 probe_seconds 不等它（它卡着期间 probing 一直为真，不会再问第二次）。"""
        result: dict = {}
        done = threading.Event()

        def run() -> None:
            ok = False
            try:
                ok = bool(self._probe(path))
            except Exception:  # noqa: BLE001 - 问不通就是不在
                ok = False
            finally:
                with self._lock:
                    result["ok"], result["finished"] = ok, True
                    state = self._hosts.get(host)
                    if state is not None and result.get("late"):
                        state.probing = False
                        self._probed.notify_all()
                done.set()

        threading.Thread(target=run, name="pearnly-host-probe", daemon=True).start()
        done.wait(self._probe_seconds)
        with self._lock:
            state = self._hosts.get(host)
            if result.get("finished"):
                return bool(result.get("ok")), True
            result["late"] = True  # 卡住的那一下返回时才放开 probing（两边都在锁里判，不会漏放）
        return False, False

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._hosts.clear()


def _label(host: str) -> str:
    """日志里只留共享名 / 盘符，不写服务器地址。"""
    return ntpath.basename(host.rstrip("\\")) or host


GATE = HostGate()


class Pace:
    """一遍后台扫描用的节拍器（见文件头）。``clock`` / ``cpu`` / ``sleep`` 可换成假的（测试）。"""

    def __init__(
        self,
        *,
        duty: float = DUTY,
        slow_seconds: float = SLOW_ACCOUNT_SECONDS,
        gate: Optional[HostGate] = None,
        clock: Callable[[], float] = time.monotonic,
        cpu: Callable[[], float] = time.thread_time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._duty = max(0.05, min(1.0, float(duty)))
        self._slow = slow_seconds
        self._gate = gate if gate is not None else GATE
        self._clock, self._cpu, self._sleep = clock, cpu, sleep
        self._start_wall = clock()
        self._start_cpu = cpu()
        self.slept = 0.0

    def account(self, root, started: float) -> None:
        """一份账套读完：在网络上的、光读文件就超过 slow_seconds → 这台服务器记一次失败并抛 HostStalled；
        本机盘慢只是慢，照常读完。之后按 CPU 占用让出。"""
        if self._clock() - started > self._slow:
            host = self._gate.host(root)
            if host:
                self._gate.fail(root)
                raise HostStalled(host)
        self.yield_cpu()

    def yield_cpu(self) -> None:
        used = self._cpu() - self._start_cpu
        elapsed = self._clock() - self._start_wall
        owed = used / self._duty - elapsed
        if owed > 0:
            pause = min(owed, MAX_YIELD_SECONDS)
            self._sleep(pause)
            self.slept += pause


__all__ = ["GATE", "HostGate", "HostStalled", "Pace", "drive_type", "host_of", "network_error"]
