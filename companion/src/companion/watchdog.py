# -*- coding: utf-8 -*-
"""看门狗:侦测 poll loop 卡死 → 自愈(重启 worker),带退避防崩溃回环。

worker 每拍 `Liveness.tick()` 刷新存活时标;主线程定时读 `stall_seconds()`,超过阈值判定
卡死(账套文件 I/O 僵在网络路径、DBF 锁、Qt 线程死锁等任意成因都覆盖)。自愈=重启 worker
线程(见 gui_tray)。退避:窗口内自愈次数封顶,超限则停手改报错——避免"目标路径永久不可达"
时无限重启空转。纯逻辑无 Qt 依赖,便于单测。
"""
from __future__ import annotations

import time
from typing import Callable, List


def stall_limit_seconds(heartbeat_interval_s: int) -> float:
    """卡死阈值 = max(90s, 3×心跳间隔)。连 3 拍心跳都没盖时标 = 真僵,而非瞬时慢。"""
    return max(90.0, 3.0 * max(1, heartbeat_interval_s))


def is_stalled(stall_seconds: float, limit_seconds: float) -> bool:
    return stall_seconds > limit_seconds


class Liveness:
    """poll loop 存活信标:循环每拍 tick(),旁路(主)线程读 stall_seconds() 判是否僵。

    用 monotonic 时钟,不受系统改时间影响。写 tick / 读 stall 之间无锁——单个 float 赋值在
    CPython 下原子,读到的要么旧值要么新值,都不会误判(旧值只会略微高估停顿,偏保守安全)。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last = clock()

    def tick(self) -> None:
        self._last = self._clock()

    def stall_seconds(self) -> float:
        return max(0.0, self._clock() - self._last)


class HealBackoff:
    """自愈退避:滑动窗口 window_s 内最多 max_heals 次;超限 allowed() 返 False(停手改报错)。"""

    def __init__(
        self,
        max_heals: int = 3,
        window_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_heals = max_heals
        self.window_s = window_s
        self._clock = clock
        self._events: List[float] = []

    def allowed(self) -> bool:
        now = self._clock()
        self._events = [t for t in self._events if now - t < self.window_s]
        return len(self._events) < self.max_heals

    def record(self) -> None:
        self._events.append(self._clock())
