# -*- coding: utf-8 -*-
"""叫醒按需请求线程（1.12.22）：领单回复带 ``requests_waiting``（服务端刚建了这台能领的按需请求）时 :func:`ring`，
``request_worker`` 空闲等待时用 :func:`nap` 代替定时等，被叫醒就马上去问。

领单本来每 3 秒一次，这里不多发任何请求；空闲时按需请求线程照旧一次 ``POLL_SECONDS`` 地等（等的是同一个事件，CPU 不变）。
停线程时也 :func:`ring` 一下，免得停下要等满一次空闲。
"""

from __future__ import annotations

import threading

_EVENT = threading.Event()


def ring() -> None:
    _EVENT.set()


def nap(seconds: float) -> bool:
    """最多等 ``seconds`` 秒；被叫醒回 True（并清掉，下一次重新等）。"""
    woken = _EVENT.wait(seconds)
    _EVENT.clear()
    return woken
