# -*- coding: utf-8 -*-
"""按需请求的后台线程（2026-10 CC 闭环）：轮流问每条已配对、没暂停的连接「有没有要我读的」，领到一件就做、分块交回，
做完马上再问一次；空闲时每 ``POLL_SECONDS`` 问一次。

与写账套的推送线完全分开（同文件夹自动录入的做法）：自己一条守护线程、自己的 httpx 客户端，不进 PollWorker、
不碰看门狗的存活时标——读得再慢也不挡推送，也不会被当成「卡死」重启。一次只做一件；线程里任何错误都不抛出去。

每条连接一直用同一个客户端（连接保持，2026-10-18 轻量化）：原来每 15 秒新建一个，Windows 上每次新建要约 250 ms CPU
（见 http_tls.py）、还要重新握手；出错、被拒、连接不再在用、线程停下时才关掉，下一轮再新建。

加一种请求：服务端与 ``request_contract`` 的 KINDS 等常量各加一项，写处理函数 ``fn(cfg, params) -> Result``（做不了
抛 ``RequestFailed(code)``），登记进 ``HANDLERS``。领请求时声明的 kinds 就是 ``HANDLERS`` 的键，旧服务端不认的种类
它自己会略过。
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterable, Optional

import httpx

from . import account_history, express_masters, express_read, folder_fetch
from .config import member_active
from .logger import get_logger
from .queue_client import _device_name
from .request_client import Refused, RequestClient
from .request_contract import CHUNK_BYTES, MAX_BYTES, POLL_SECONDS, RequestFailed, Result
from .version import VERSION

log = get_logger(__name__)

# 种类 → 处理函数 fn(cfg, params) -> Result。
HANDLERS: dict = {
    "express_read": express_read.read_books,
    "folder_list": folder_fetch.list_files,
    "folder_fetch": folder_fetch.read_file,
    "account_history": account_history.read_history,
    "express_masters": express_masters.read_masters,
    "express_marks": express_masters.read_marks,
}
# 交一块失败（网络断了一下）最多重试几次；续传（服务端说已收到多少）也算在内。
RETRIES = 3
OFFSET_CODE = "companion.on_demand_offset"
# 服务端不认这两个接口（还没部署新版）、总闸关着或配对失效（401 / 403 / 404）：这条连接隔这么久再问，不每 15 秒刷日志。
REFUSED_BACKOFF = 600


class RequestWorker:
    def __init__(self, configs: Callable[[], Iterable], *, client_factory=RequestClient,
                 handlers: Optional[dict] = None, idle: float = POLL_SECONDS,
                 timer: Callable[[], float] = time.monotonic) -> None:
        self._configs = configs
        self._client_factory = client_factory
        self._handlers = dict(HANDLERS if handlers is None else handlers)
        self._idle = idle
        self._timer = timer
        # 被拒的连接（{服务器 + 密钥前缀: 下次再问的时刻}）
        self._refused: dict = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._working = False
        self._clients: dict = {}   # {连接身份: 客户端}（只在本线程用）

    # ── 线程 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        """有已配对的连接才启动；已在跑就不重复启动。"""
        if self._thread and self._thread.is_alive():
            return
        if not self._active():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pearnly-on-demand", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def busy(self) -> bool:
        """正在做一件（读文件、交结果）：自动更新等它做完。"""
        return self._working

    @contextmanager
    def _work(self):
        self._working = True
        try:
            yield
        finally:
            self._working = False

    def _active(self) -> list:
        try:
            return [cfg for cfg in list(self._configs() or []) if member_active(cfg)
                    and getattr(cfg, "paired", True) and getattr(cfg, "backend_url", "")
                    and getattr(cfg, "bearer_token", "")]
        except Exception:
            return []

    def _loop(self) -> None:
        try:
            self._serve()
        finally:
            self.close_clients()

    def _serve(self) -> None:
        while not self._stop.is_set():
            worked = False
            active = self._active()
            self.forget_others({_key(cfg) for cfg in active})
            for cfg in active:
                if self._stop.is_set():
                    return
                key = _key(cfg)
                if self._refused.get(key, 0) > self._timer():
                    continue
                try:
                    worked = self.round(cfg) or worked
                    self._refused.pop(key, None)
                except Refused as error:
                    self._refused[key] = self._timer() + REFUSED_BACKOFF
                    if error.rejected:
                        # 配对失效（401）：等重新连接（托盘由心跳 / 文件夹同步提示），换了密钥就是另一个成员键，马上再问
                        log.info("按需请求：这把密钥服务端不认（已吊销或电脑已移除），等重新连接: %s", error)
                    else:
                        log.info("按需请求暂不可用（%s 秒后再问）: %s", REFUSED_BACKOFF, error)
                except httpx.TransportError as error:
                    log.info("按需请求连不上服务器: %s", type(error).__name__)
                except Exception:
                    log.exception("按需请求本轮失败")
            if not worked:
                self._stop.wait(self._idle)

    # ── 一轮：领一件、做、交 ────────────────────────────────────────────────

    def round(self, cfg) -> bool:
        """问这条连接要一件来做；做了返回 True（马上再问一次）。出了错这条连接的客户端关掉，下一轮新建。"""
        client = self._client(cfg)
        try:
            reply = client.next({"version": VERSION, "kinds": list(self._handlers), "device": _device_name()})
            request = reply.get("request") if reply.get("ok") else None
            if not isinstance(request, dict):
                return False
            with self._work():
                self._handle(client, cfg, request)
            return True
        except BaseException:
            self._drop(_key(cfg))
            raise

    # ── 客户端（每条连接一个，一直用） ──────────────────────────────────────

    def _client(self, cfg):
        key = _key(cfg)
        client = self._clients.get(key)
        if client is None:
            client = self._clients[key] = self._client_factory(cfg.backend_url, cfg.bearer_token)
        return client

    def _drop(self, key: str) -> None:
        client = self._clients.pop(key, None)
        if client is not None:
            try:
                client.close()
            except Exception:
                log.debug("按需请求客户端关闭失败", exc_info=True)

    def forget_others(self, keep: set) -> None:
        """不再在用的连接（断开、换了密钥）：关掉它的客户端。"""
        for key in [key for key in self._clients if key not in keep]:
            self._drop(key)

    def close_clients(self) -> None:
        for key in list(self._clients):
            self._drop(key)

    def _handle(self, client, cfg, request: dict) -> None:
        ident = {"request": request.get("id"), "handle": request.get("handle")}
        if not all(isinstance(value, str) and value for value in ident.values()):
            return
        kind = request.get("kind")
        handler = self._handlers.get(kind)
        if handler is None:
            self._fail(client, ident, "unsupported_kind")
            return
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        try:
            result = handler(cfg, params)
        except RequestFailed as error:
            self._fail(client, ident, error.code)
            return
        except Exception:
            log.exception("按需请求处理失败: %s", kind)
            self._fail(client, ident, "failed")
            return
        if not isinstance(result, Result):
            self._fail(client, ident, "failed")
            return
        allowed = request.get("max_bytes")
        limit = min(allowed, MAX_BYTES.get(kind, 0)) if type(allowed) is int else MAX_BYTES.get(kind, 0)
        if len(result.content) > limit:
            self._fail(client, ident, "too_large")
            return
        self._upload(client, ident, request, result)

    def _fail(self, client, ident: dict, code: str) -> None:
        try:
            client.complete({**ident, "ok": False, "code": code})
        except Exception:
            log.exception("按需请求交回失败原因时出错")

    def _upload(self, client, ident: dict, request: dict, result: Result) -> None:
        """按服务端给的块大小一块一块交；服务端说已收到多少（续传）就从那里接着交。"""
        content = result.content
        size = len(content)
        digest = hashlib.sha256(content).hexdigest()
        chunk = request.get("chunk_bytes")
        chunk = chunk if type(chunk) is int and 0 < chunk <= CHUNK_BYTES else CHUNK_BYTES
        offset, retries = 0, 0
        while not self._stop.is_set():
            piece = content[offset:offset + chunk]
            final = offset + len(piece) >= size
            body = {**ident, "ok": True, "offset": offset, "content": base64.b64encode(piece).decode("ascii"),
                    "final": final}
            if final:
                body.update(size=size, sha256=digest, meta=result.meta)
            try:
                reply = client.complete(body)
            except Refused:
                raise
            except Exception:
                retries += 1
                if retries > RETRIES:
                    log.exception("按需请求结果没交上")
                    return
                self._stop.wait(2 * retries)
                continue
            if reply.get("ok"):
                if final:
                    return
                offset += len(piece)
                retries = 0
                continue
            received = reply.get("received")
            if reply.get("code") == OFFSET_CODE and type(received) is int and 0 <= received <= size \
                    and retries < RETRIES:
                offset, retries = received, retries + 1
                continue
            log.info("按需请求结果不再交: %s", reply.get("code"))
            return


def _key(cfg) -> str:
    """连接的身份（服务器 + 密钥摘要），只用来记退避，不落盘、不记日志。"""
    token = str(getattr(cfg, "bearer_token", "") or "")
    return str(getattr(cfg, "backend_url", "") or "") + "|" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
