# -*- coding: utf-8 -*-
"""叫醒按需请求线程（1.12.22）：领单回复带 ``requests_waiting``（服务端刚建了这台能领的按需请求）时 :func:`ring`，
``request_worker`` 空闲等待时用 :func:`nap` 代替定时等，被叫醒就马上去问。

领单本来每 3 秒一次，这里不多发任何请求；空闲时按需请求线程照旧一次 ``POLL_SECONDS`` 地等（等的是同一个事件，CPU 不变）。
停线程时也 :func:`ring` 一下，免得停下要等满一次空闲。

写账线在做一张单时（:func:`writing`，1.12.25）领单停着，服务端的叫醒到不了：这期间 :func:`nap` 最多等 :data:`BUSY_SECONDS`，
按需请求回到 1.12.24 的 15 秒一问（空闲时 ``POLL_SECONDS`` 已放长到 30 秒）。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_EVENT = threading.Event()
_WRITING = threading.Event()
BUSY_SECONDS = 15.0


def ring() -> None:
    _EVENT.set()


@contextmanager
def writing():
    """写账线做一张单的这段时间（``multi_profile_worker``）。"""
    _WRITING.set()
    try:
        yield
    finally:
        _WRITING.clear()


def nap(seconds: float) -> bool:
    """最多等 ``seconds`` 秒（写账线在做单时最多 :data:`BUSY_SECONDS`）；被叫醒回 True（并清掉，下一次重新等）。"""
    start = time.monotonic()
    while True:
        limit = start + (min(seconds, BUSY_SECONDS) if _WRITING.is_set() else seconds)
        left = limit - time.monotonic()
        if left <= 0:
            _EVENT.clear()
            return False
        # 一次最多等 BUSY_SECONDS：等的中途写账线开始做单，也能按短的那个间隔醒
        if _EVENT.wait(min(left, BUSY_SECONDS)):
            _EVENT.clear()
            return True
