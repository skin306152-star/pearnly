# -*- coding: utf-8 -*-
"""桥那一侧（评估第 4.3 节）：处理云端写进 ``in\\`` 的请求，代发到配对时记下的站点，回复写进 ``out\\``。

处理逻辑（``process`` 一个 ``.req``）与监视方式分开：``host.CloudHost`` 的后台线程在目录有变化时（``dir_watch``，Windows 上
``ReadDirectoryChangesW``）或每 60 秒兜底时看一遍 ``pending``、交给工作线程 ``process``。

- 先把 ``.req`` 改名 ``.work`` 认领（几条线并发处理时同一个请求只处理一次；云端据此分清「桥不在」与「桥慢」）；
- 核对签名、会话号、防重放、路径与方法白名单、大小（``tunnel_protocol.decode_request``）；
- 只发到 ``site``（``https://<租户域名>``，配对时记下），请求里带的主机名一律不用，不跟随重定向；
  给了 ``route`` 时按请求的方法路径选站点与令牌（一个桥同时配了 Cowork 与 ERP 两个站点，两种通道的路径前缀不同），
  选不出（这台云端电脑还没在那种通道的站点登记）按路径不允许拒绝；
- ``Authorization`` 由桥换上真令牌，云端带的一律丢；
- 站点的回复（包括 4xx / 5xx）原样写回；不合规、站点连不上、超时写「隧道拒绝」回复（云端变成网络错误，不是 4xx）；
  重放的请求不写回复（免得盖掉同一编号真正的回复）；
- 日志只记方法、路径、状态码、字节数、耗时（拒绝时记原因码），不记正文和头；
- ``cleanup``：超过 10 分钟没人取的文件删掉。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union
from urllib.parse import urlsplit

import httpx

from ..logger import get_logger
from . import tunnel_protocol as protocol

log = get_logger(__name__)

STALE_SECONDS = 10 * 60
_SUFFIXES = (".req", ".part", ".work", ".resp")


@dataclass(frozen=True)
class Outgoing:
    """交给代发函数的请求：``url`` 已拼好（站点 + 白名单路径 + 查询串），``headers`` 已带上真令牌。"""

    method: str
    url: str
    headers: dict
    body: bytes
    timeout: float


@dataclass(frozen=True)
class Incoming:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""


class SiteUnreachable(Exception):
    """代发函数用它（或 httpx 的网络错误）表示站点连不上。"""


class SiteTimeout(Exception):
    """代发函数用它（或 httpx 的超时）表示站点超时。"""


Forward = Callable[[Outgoing], Incoming]
# 按方法路径选站点：回 ``(站点 https://<主机>, 令牌)``；选不出回 None
Route = Callable[[str], Optional[tuple]]


def httpx_forward(client: httpx.Client) -> Forward:
    """用一个保持连接的 httpx 客户端代发（桥上建一份，``verify=http_tls.context()``，``follow_redirects=False``）。"""

    def forward(out: Outgoing) -> Incoming:
        response = client.request(out.method, out.url, headers=out.headers, content=out.body,
                                  timeout=httpx.Timeout(out.timeout, connect=min(10.0, out.timeout)),
                                  follow_redirects=False)
        return Incoming(status=response.status_code, headers=dict(response.headers), body=response.content)

    return forward


def normalize_site(site: str) -> str:
    """配对时记下的站点只能是 ``https://<主机>``（可带端口），不带路径、查询、账号。"""
    parts = urlsplit(str(site or "").strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password \
            or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("site must be https://<host>")
    return f"https://{parts.netloc.lower()}"


class TunnelRelay:
    def __init__(self, root, session: protocol.Session, site: str, token: Union[str, Callable[[], str]],
                 forward: Forward, *, clock: Callable[[], float] = time.time, route: Optional[Route] = None) -> None:
        self.root = Path(root)
        self.inbox = self.root / "in"
        self.outbox = self.root / "out"
        self.session = session
        # 给了 route 时站点每个请求现选，``site`` 可以空着（云端电脑还没在任何站点登记）
        self.site = normalize_site(site) if site or route is None else ""
        self._token = token if callable(token) else (lambda: token)
        self._forward = forward
        self._clock = clock
        self._route = route
        self._guard = protocol.ReplayGuard()
        self._guard_lock = threading.Lock()

    def ensure_dirs(self) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True, exist_ok=True)

    def pending(self) -> list:
        """``in\\`` 里等着处理的请求（按序号）。"""
        try:
            names = sorted(entry.name for entry in os.scandir(self.inbox) if entry.name.endswith(".req"))
        except OSError:
            return []
        return [self.inbox / name for name in names]

    def poll_once(self) -> int:
        """轮询一遍：处理现在所有的 ``.req``，回处理了几个。"""
        return sum(1 for path in self.pending() if self.process(path))

    def process(self, path) -> bool:
        """处理一个 ``.req``。别的线已经认领了回 False。"""
        path = Path(path)
        request_id = protocol.id_of_request_name(path.name)
        if not request_id:
            return False   # 名字不合规：不处理，等清理
        work = path.with_suffix(".work")
        try:
            os.replace(path, work)
        except OSError:
            return False
        started = time.monotonic()
        try:
            data = _read_limited(work)
            if data is None:
                self._refuse(request_id, protocol.TOO_LARGE, "-", "-", started)
                return True
            if protocol.peek_id(data) not in ("", request_id):
                self._refuse(request_id, protocol.BAD_FORMAT, "-", "-", started)
                return True
            try:
                with self._guard_lock:
                    request = protocol.decode_request(data, self.session, self._guard)
            except protocol.TunnelError as error:
                # 重放的、或拿已用过的编号来的：不写回复，免得盖掉那个编号真正的回复（真的云端从不重发）
                answer = error.code != protocol.REPLAYED and not self._seen(request_id)
                self._refuse(request_id, error.code, "-", "-", started, answer=answer)
                return True
            self._relay(request, started)
            return True
        finally:
            _remove(work)

    def _relay(self, request: protocol.Request, started: float) -> None:
        site, token = self.site, None
        if self._route is not None:
            try:
                target = self._route(request.path)
                if target is not None:
                    site, token = normalize_site(target[0]), str(target[1] or "")
            except Exception as error:  # noqa: BLE001 - 选不出就拒绝，不发到别的站点
                log.info("隧道选站点失败：%s", type(error).__name__)
                target = None
            if target is None:
                self._refuse(request.id, protocol.PATH_NOT_ALLOWED, request.method, request.path, started)
                return
        url = site + request.path + (f"?{request.query}" if request.query else "")
        headers = dict(request.headers)
        token = str(self._token() or "") if token is None else token
        if token:
            headers["authorization"] = f"Bearer {token}"
        out = Outgoing(method=request.method, url=url, headers=headers, body=request.body, timeout=request.timeout)
        try:
            incoming = self._forward(out)
        except (SiteTimeout, httpx.TimeoutException):
            self._refuse(request.id, protocol.SITE_TIMEOUT, request.method, request.path, started)
            return
        except (SiteUnreachable, httpx.HTTPError, OSError):
            self._refuse(request.id, protocol.SITE_UNREACHABLE, request.method, request.path, started)
            return
        try:
            data = protocol.encode_reply(self.session, request.id, incoming.status, incoming.headers, incoming.body)
        except protocol.TunnelError as error:
            self._refuse(request.id, error.code, request.method, request.path, started)
            return
        self._write_reply(request.id, data)
        log.info("隧道 %s %s → %s，发 %d 字节，回 %d 字节，%d ms", request.method, request.path, incoming.status,
                 len(request.body), len(incoming.body), _ms(started))

    def _seen(self, request_id: str) -> bool:
        with self._guard_lock:
            return self._guard.seen(request_id)

    def _refuse(self, request_id: str, code: str, method: str, path: str, started: float, answer: bool = True) -> None:
        if answer:
            self._write_reply(request_id, protocol.encode_refusal(self.session, request_id, code))
        # 白名单外的路径不记原样（可能被拿来塞内容），只记原因码
        shown = path if protocol.path_allowed(path) else "-"
        log.info("隧道拒绝 %s %s：%s，%d ms", method, shown, code, _ms(started))

    def _write_reply(self, request_id: str, data: bytes) -> None:
        part = self.outbox / f"{request_id}.part"
        try:
            with open(part, "wb") as handle:
                handle.write(data)
            os.replace(part, self.outbox / protocol.reply_name(request_id))
        except OSError as error:
            _remove(part)
            log.info("隧道回复写不进（%s），云端按超时处理", type(error).__name__)

    def cleanup(self, max_age: float = STALE_SECONDS) -> int:
        """删掉 ``in\\`` / ``out\\`` 里超过 ``max_age`` 秒没人取的文件，回删了几个。"""
        floor = self._clock() - max_age
        removed = 0
        for folder in (self.inbox, self.outbox):
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False) and entry.name.endswith(_SUFFIXES) \
                            and entry.stat(follow_symlinks=False).st_mtime < floor and _remove(Path(entry.path)):
                        removed += 1
                except OSError:
                    continue
        if removed:
            log.info("隧道清理：删掉 %d 个没人取的文件", removed)
        return removed


def _read_limited(path: Path) -> Optional[bytes]:
    limit = protocol.MAX_HEAD + 1 + protocol.MAX_BODY
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return b""
    return None if len(data) > limit else data


def _remove(path: Path) -> bool:
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
