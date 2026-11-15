# -*- coding: utf-8 -*-
"""云端那一侧（评估第 4.3 节）：把 httpx 请求写进隧道文件夹，等桥写回回复。

``TunnelTransport(root, session)`` 装进 ``httpx.Client(transport=…)`` 即可，调用方照旧 ``client.post(path, json=…)``。

- 写 ``in\\<序号>-<编号>.part``，写完原子改名 ``.req``（桥不会读到半个文件）；
- 等 ``out\\<编号>.resp``：100 ms 起逐步放宽到 1 秒看一次，读完删；
- 等待时间 = 原请求超时 + 5 秒；同时最多 8 个在路上，多了在这里排队（排队也算在等待时间里）；
- 出错一律是 httpx 的网络错误：写不进 / 桥没取走 / 隧道拒绝 / 回复不对 → ``ConnectError``，桥取走了却没回 → ``ReadTimeout``，
  正文超过上限 → ``TunnelTooLarge``（``TransportError`` 的子类，不写文件）。**绝不伪造 401 / 403 / 404**：CC 把 401 当密钥吊销、
  403 / 404 当旧服务端（1.9.1），伪造会把成员停用。
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from . import tunnel_protocol as protocol

MAX_IN_FLIGHT = 8
GRACE_SECONDS = 5.0
POLL_FIRST = 0.1
POLL_MOST = 1.0


class TunnelTooLarge(httpx.TransportError):
    """请求正文超过隧道上限（``tunnel_protocol.MAX_BODY``），没有写进隧道。"""


def _wait_seconds(request: httpx.Request) -> float:
    """原请求的超时：取读 / 写 / 连接 / 取连接里最长的一项（httpx 把客户端与单次的超时合并后放在 extensions 里）。"""
    values = (request.extensions.get("timeout") or {}).values()
    numbers = [float(value) for value in values if value is not None]
    return protocol._timeout(max(numbers) if numbers else None)


class TunnelTransport(httpx.BaseTransport):
    def __init__(self, root, session: protocol.Session, *, max_in_flight: int = MAX_IN_FLIGHT,
                 grace: float = GRACE_SECONDS, poll_first: float = POLL_FIRST, poll_most: float = POLL_MOST,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.root = Path(root)
        self.inbox = self.root / "in"
        self.outbox = self.root / "out"
        self.session = session
        self._slots = threading.BoundedSemaphore(max_in_flight)
        self._seq = itertools.count(1)
        self._seq_lock = threading.Lock()
        self._grace, self._first, self._most = grace, poll_first, poll_most
        self._clock, self._sleep = clock, sleep

    def _next_seq(self) -> int:
        with self._seq_lock:
            return next(self._seq)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        if len(body) > protocol.MAX_BODY:
            raise TunnelTooLarge(f"request body {len(body)} bytes over the tunnel limit", request=request)
        timeout = _wait_seconds(request)
        deadline = self._clock() + timeout + self._grace
        if not self._slots.acquire(timeout=max(0.0, deadline - self._clock())):
            raise httpx.PoolTimeout("tunnel: too many requests in flight", request=request)
        try:
            return self._exchange(request, body, timeout, deadline)
        finally:
            self._slots.release()

    def _exchange(self, request: httpx.Request, body: bytes, timeout: float, deadline: float) -> httpx.Response:
        request_id = protocol.new_id()
        seq = self._next_seq()
        try:
            data = protocol.encode_request(
                self.session, seq=seq, request_id=request_id, method=request.method, path=request.url.path,
                query=request.url.query.decode("ascii", "replace"), headers=request.headers.multi_items(), body=body,
                timeout=timeout)
        except protocol.TunnelError as error:
            if error.code == protocol.TOO_LARGE:
                raise TunnelTooLarge(str(error), request=request) from None
            raise httpx.ConnectError(f"tunnel: {error.code}", request=request) from None
        stem = protocol.request_stem(seq, request_id)
        part, ready = self.inbox / (stem + ".part"), self.inbox / (stem + ".req")
        try:
            with open(part, "wb") as handle:
                handle.write(data)
            os.replace(part, ready)
        except OSError as error:
            _remove(part)
            raise httpx.ConnectError(f"tunnel: cannot write request ({type(error).__name__})", request=request) from None
        reply_path = self.outbox / protocol.reply_name(request_id)
        wait = self._first
        while True:
            raw = _read_reply(reply_path)
            if raw is not None:
                return self._response(request, raw, request_id)
            left = deadline - self._clock()
            if left <= 0:
                break
            self._sleep(min(wait, left))
            wait = min(self._most, wait * 2)
        # 桥一直没取走（文件还在）= 桥不在；取走了没回 = 桥或站点太慢。两种都先撤掉，免得桥过后又代发一次
        if _remove(ready):
            raise httpx.ConnectError("tunnel: the bridge did not pick up the request", request=request)
        raise httpx.ReadTimeout(f"tunnel: no reply within {timeout + self._grace:.0f} s", request=request)

    def _response(self, request: httpx.Request, raw: bytes, request_id: str) -> httpx.Response:
        try:
            reply = protocol.decode_reply(raw, self.session, request_id)
        except protocol.TunnelError as error:
            raise httpx.ConnectError(f"tunnel: {error.code}", request=request) from None
        if reply.refused:
            if reply.refused == protocol.SITE_TIMEOUT:
                raise httpx.ReadTimeout("tunnel: the site timed out", request=request)
            raise httpx.ConnectError(f"tunnel refused: {reply.refused}", request=request)
        return httpx.Response(reply.status, headers=reply.headers, content=reply.body, request=request)


def _read_reply(path: Path) -> Optional[bytes]:
    """有回复就读出并删掉；还没有回 None。"""
    try:
        with open(path, "rb") as handle:
            data = handle.read(protocol.MAX_HEAD + 2 + protocol.MAX_BODY)
    except FileNotFoundError:
        return None
    except OSError:
        return None   # 映射盘一时读不了：下一轮再看，到点按超时处理
    _remove(path)
    return data


def _remove(path: Path) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False
