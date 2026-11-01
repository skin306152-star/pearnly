# -*- coding: utf-8 -*-
"""多个代录会话并行时，这台电脑现在还能多开几个（评估 §15.1：不设硬上限，按本机实时负载自动调节）。

不常驻、不轮询：只在有会话要开始时问一次 ``room()``。看三样：
- CPU：系统最近一段的忙闲（``GetSystemTimes`` 两次采样之差，约 0.5 秒）；
- 可用内存（``GlobalMemoryStatusEx``）：每多开一个 Express 要留出 ``per_session_mb``（实测后定，先按保守值）；
- Express 响应：已在录的各会话的 Express 回一个空消息要多久（会话自己报来的最近值）；有一个慢得过线就先不开新的。
都够才放一个新会话进来；不够就排队（界面写「等电脑空出来」），等有会话录完再问一次。
"""
from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass
class Limits:
    cpu_busy_max: float = 0.80  # 系统 CPU 忙到八成就不再开新的
    free_mb_floor: int = 600  # 至少给系统和人留这么多可用内存
    per_session_mb: int = 250  # 每个会话（一个 ExpressI + 一条线程）预留；实机实测后改
    express_slow_ms: int = 3000  # 有会话的 Express 回一个空消息超过这么久：电脑已经吃力


def _windows_cpu_busy(sample_s: float = 0.5) -> float:
    from ctypes import wintypes as W

    kernel32 = ctypes.WinDLL("kernel32")

    def times():
        idle, kernel, user = W.FILETIME(), W.FILETIME(), W.FILETIME()
        kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        value = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
        return value(idle), value(kernel) + value(user)  # kernel 时间里含 idle

    idle0, total0 = times()
    time.sleep(sample_s)
    idle1, total1 = times()
    total = total1 - total0
    return 0.0 if total <= 0 else max(0.0, min(1.0, 1 - (idle1 - idle0) / total))


def _windows_free_mb() -> int:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
    ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status))
    return int(status.ullAvailPhys // (1024 * 1024))


class Scheduler:
    def __init__(self, limits: Optional[Limits] = None, *, cpu_busy: Optional[Callable[[], float]] = None,
                 free_mb: Optional[Callable[[], int]] = None):
        self.limits = limits or Limits()
        windows = sys.platform == "win32"
        self.cpu_busy = cpu_busy or (_windows_cpu_busy if windows else (lambda: 0.0))
        self.free_mb = free_mb or (_windows_free_mb if windows else (lambda: 8192))
        self.response_ms: Dict[str, int] = {}  # 会话 → 它的 Express 最近一次回空消息用了多久
        self.last: Dict = {}  # 最近一次判断的依据（进汇报 / 界面「为什么在排队」）

    def report(self, session: str, ms: int) -> None:
        self.response_ms[session] = int(ms)

    def forget(self, session: str) -> None:
        self.response_ms.pop(session, None)

    def room(self, running: int) -> bool:
        """现在能不能再开一个会话（``running`` 是正在录的个数）。第一个会话总是放行（不能让人一个都录不了）。"""
        l = self.limits
        cpu, free = self.cpu_busy(), self.free_mb()
        slow = max(self.response_ms.values(), default=0)
        self.last = {"running": running, "cpu_busy": round(cpu, 2), "free_mb": free, "express_ms": slow}
        if running == 0:
            self.last["reason"] = ""
            return True
        reason = ""
        if cpu >= l.cpu_busy_max:
            reason = "cpu"
        elif free - l.per_session_mb < l.free_mb_floor:
            reason = "memory"
        elif slow >= l.express_slow_ms:
            reason = "express_slow"
        self.last["reason"] = reason
        return not reason
