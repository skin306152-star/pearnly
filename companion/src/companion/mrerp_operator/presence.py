# -*- coding: utf-8 -*-
"""录 MR.ERP 时会不会和人冲突（评估 §14.2，级别「自动」）：只有人在**这个 MR.ERP 窗口**里点、打字、滚轮才算「有人在用」，
停在原地等；人用别的程序、锁屏都不冲突（网页在自己的窗口里跑，不碰真键盘鼠标）。

计数在子进程的窗口事件过滤里（``web_driver.Driver.eventFilter``），这里每次要动页面前看一眼计数有没有涨：涨了就暂停
（``on_pause("user_active")``，证据进汇报），恢复条件同 Express 代录：连续 ``idle_s`` 秒（默认 30 秒）没人动，或人在 CC 里点
「继续」（``resume_now``）。等的时候人点停止就抛 ``Stopped``。接口与 ``express_operator.presence.Presence`` 一致
（``__call__`` / ``resume_now`` / ``close`` / ``evidence``），宿主照同一套用。
"""
from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from ..express_operator.engine import Stopped


class WebPresence:
    def __init__(self, human: Callable[[], Dict], *, stop_requested: Callable[[], bool],
                 on_pause: Callable[[str], None] = lambda _r: None, on_resume: Callable[[], None] = lambda: None,
                 idle_s: float = 30, poll_s: float = 3, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time):
        self.human = human  # → {"human": 窗口里的人工输入次数, "human_idle_ms": 距上次人工输入的毫秒}
        self.stop_requested, self.on_pause, self.on_resume = stop_requested, on_pause, on_resume
        self.idle_ms, self.poll_s, self.sleep, self.clock = int(idle_s * 1000), poll_s, sleep, clock
        self._seen: Optional[int] = None
        self._resume_now = False
        self.evidence: List[Dict] = []

    def resume_now(self) -> None:
        self._resume_now = True

    def close(self) -> None:
        self._seen = None

    def _read(self) -> Dict:
        try:
            return self.human() or {}
        except Exception:  # noqa: BLE001 — 窗口没开 / 已关：没有人在用
            return {}

    def __call__(self) -> bool:
        """有人在这个窗口里动过就在这里等到恢复。回这次等没等过。"""
        state = self._read()
        count = int(state.get("human") or 0)
        if self._seen is None or count < self._seen:
            self._seen = count  # 开局（或窗口换了一个）：之前的不算
            return False
        if count == self._seen:
            return False
        found = {"reason": "user_active", "at": round(self.clock(), 1), "source": "mrerp_window", "inputs": count - self._seen}
        self.evidence.append(found)
        self.on_pause("user_active")
        self._resume_now = False
        while True:
            if self.stop_requested():
                raise Stopped()
            state = self._read()
            idle = state.get("human_idle_ms")
            if self._resume_now or idle is None or int(idle) >= self.idle_ms:
                self._seen = int(state.get("human") or count)
                found["paused_s"] = round(self.clock() - found["at"], 1)
                self.on_resume()
                return True
            self.sleep(self.poll_s)
