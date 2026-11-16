# -*- coding: utf-8 -*-
"""文件隧道的约定（评估第 4.3 节）：请求 / 回复文件的格式、签名、白名单、防重放。纯函数与纯状态，不做 IO、不导入 httpx。

一个文件 = 头一行 JSON（UTF-8，``\\n`` 结尾）+ 正文字节。

- 请求（云端写 ``in\\<序号>-<编号>.part``，写完改名 ``.req``）::

    {v:1, id, epoch, seq, method, path, query, headers, body_sha256, timeout, mac}

- 回复（桥写 ``out\\<编号>.part``，写完改名 ``.resp``）::

    {v:1, id, epoch, status, headers, refused, body_sha256, mac}

  ``refused`` 非空 = 隧道拒绝（不合规、站点连不上、超时），``status`` 为 0；云端把它变成网络错误，**从不变成 4xx**。

签名：每次会话桥生成 ``epoch`` 与 32 字节密钥 K（经 ``ctl\\`` 交给云端，只放内存）。

- 会话交接（桥写 ``ctl\\session.json.part``，写完改名 ``session.json``；云端读完删除，桥在会话结束时也删）::

    {v:1, epoch, key}      # key 是 K 的 base64（``encode_session`` / ``decode_session``）

请求 ``mac = HMAC-SHA256(K, epoch|seq|id|method|path|body_sha256|query|headers)``：前六项照评估，后面追加查询串与规范化的
白名单头，免得有人改查询参数而签名不变。回复同样签名（``resp|…``），云端核对，免得别人往 ``out\\`` 塞假回复（比如假 401）。
不靠两边时钟：防重放只看本会话 ``epoch``、``seq`` 在窗口内、``id`` 没见过。
"""

from __future__ import annotations

import base64
import binascii
import collections
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

VERSION = 1
# 单个请求 / 回复正文上限（评估：按需请求按 4 MB 分块交回，``request_contract.CHUNK_BYTES``；base64 后约 5.4 MB，留足余量）
MAX_BODY = 32 * 1024 * 1024
# 头一行上限（几十个字段，远小于此）
MAX_HEAD = 64 * 1024
# 只做数字上限：超过这个等待的请求现有代码里没有（最长的是交回按需请求结果 120 秒、整份上报心跳）
MAX_TIMEOUT = 3600.0
DEFAULT_TIMEOUT = 30.0
# 不同步到达、并发处理的请求编号允许乱序多少
SEQ_WINDOW = 256
SEEN_IDS = 4096

METHODS = ("GET", "POST")

_COWORK = "/api/method/pearnly_cowork.companion."
_CORE = "/api/method/pearnly_core.cc_desktop.machine."
PREFIXES = (_COWORK, _CORE)
# 逐条列 CC 现有实际调用的机器方法（路径集中在 ``channel.PATHS``，测试比对两边一致）。注释是调用它的源码文件。
ALLOWED_PATHS = frozenset({
    _COWORK + "heartbeat.report",                # queue_client.py（心跳）、health_check.py（设置页「检查连接」）
    _COWORK + "queue_api.lease",                 # queue_client.py
    _COWORK + "queue_api.acknowledge",           # queue_client.py
    _COWORK + "request_api.next_request",        # request_client.py
    _COWORK + "request_api.complete_request",    # request_client.py
    _COWORK + "device_api.hello",                # device_link.py
    _COWORK + "device_api.leave",                # device_link.py
    _COWORK + "credential_api.mrerp_login",      # mrerp_login.py
    _COWORK + "operator_api.operator_ai",        # operator_ai.py
    _CORE + "heartbeat",                         # queue_client.py、health_check.py
    _CORE + "lease",                             # queue_client.py
    _CORE + "acknowledge",                       # queue_client.py
    _CORE + "next_request",                      # request_client.py
    _CORE + "complete_request",                  # request_client.py
    _CORE + "leave",                             # device_link.py
    _CORE + "mrerp_login",                       # mrerp_login.py
    _CORE + "operator_ai",                       # operator_ai.py
})

# CC 现有客户端自己设的头只有 Authorization（桥换成真令牌，云端带的一律丢）；httpx 自动带的 Content-Type（json=）、Accept 照传。
# Host、User-Agent、Accept-Encoding、Content-Length、Connection、Cookie 不传：桥用自己的客户端发，主机只认配对的站点。
REQUEST_HEADERS = ("accept", "content-type")
RESPONSE_HEADERS = ("content-type",)

# 隧道拒绝的原因码（只进回复文件和日志，不给用户看）
BAD_FORMAT = "bad_format"
BAD_VERSION = "bad_version"
BAD_MAC = "bad_mac"
BAD_BODY = "bad_body"
STALE_EPOCH = "stale_epoch"
REPLAYED = "replayed"
PATH_NOT_ALLOWED = "path_not_allowed"
METHOD_NOT_ALLOWED = "method_not_allowed"
TOO_LARGE = "too_large"
RESPONSE_TOO_LARGE = "response_too_large"
SITE_UNREACHABLE = "site_unreachable"
SITE_TIMEOUT = "site_timeout"
BAD_REPLY = "bad_reply"

_HEX32 = re.compile(r"\A[0-9a-f]{32}\Z")
_REQ_NAME = re.compile(r"\A(\d{12})-([0-9a-f]{32})\.req\Z")
_QUERY_BAD = re.compile(r"[\x00-\x20\x7f#]")


class TunnelError(Exception):
    """隧道这一层的问题；``code`` 是上面的原因码。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Session:
    """一次会话：``epoch`` 会话号（32 位十六进制），``key`` 32 字节签名密钥。"""

    epoch: str
    key: bytes = field(repr=False)


@dataclass(frozen=True)
class Request:
    id: str
    epoch: str
    seq: int
    method: str
    path: str
    query: str
    headers: dict
    body: bytes
    timeout: float


@dataclass(frozen=True)
class Reply:
    id: str
    status: int
    headers: dict
    body: bytes
    refused: str = ""


def new_session() -> Session:
    return Session(epoch=secrets.token_hex(16), key=secrets.token_bytes(32))


SESSION_FILE = "session.json"
_KEY_BYTES = 32


def encode_session(session: Session) -> bytes:
    """会话交接文件的内容（``ctl\\session.json``）：只有版本、会话号、密钥的 base64。"""
    if not _HEX32.match(str(session.epoch)) or len(session.key) != _KEY_BYTES:
        raise TunnelError(BAD_FORMAT, "session")
    return (json.dumps({"v": VERSION, "epoch": session.epoch, "key": base64.b64encode(session.key).decode("ascii")},
                       separators=(",", ":")) + "\n").encode("ascii")


def decode_session(data: bytes) -> Session:
    """云端读会话交接文件；不合规抛 :class:`TunnelError`（文本不带密钥）。"""
    try:
        head = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise TunnelError(BAD_FORMAT, "session") from None
    if not isinstance(head, dict):
        raise TunnelError(BAD_FORMAT, "session")
    if head.get("v") != VERSION:
        raise TunnelError(BAD_VERSION, "session")
    epoch, key = head.get("epoch"), head.get("key")
    if not isinstance(epoch, str) or not _HEX32.match(epoch) or not isinstance(key, str):
        raise TunnelError(BAD_FORMAT, "session")
    try:
        raw = base64.b64decode(key.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        raise TunnelError(BAD_FORMAT, "session") from None
    if len(raw) != _KEY_BYTES:
        raise TunnelError(BAD_FORMAT, "session")
    return Session(epoch=epoch, key=raw)


def new_id() -> str:
    return secrets.token_hex(16)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── 文件名 ──

def request_stem(seq: int, request_id: str) -> str:
    return f"{int(seq):012d}-{request_id}"


def request_name(seq: int, request_id: str) -> str:
    return request_stem(seq, request_id) + ".req"


def id_of_request_name(name: str) -> str:
    """``.req`` 文件名里的编号；名字不合规回空串（桥不处理，等清理）。"""
    match = _REQ_NAME.match(name)
    return match.group(2) if match else ""


def reply_name(request_id: str) -> str:
    return f"{request_id}.resp"


# ── 白名单 ──

def path_allowed(path: str) -> bool:
    return isinstance(path, str) and path in ALLOWED_PATHS


def filter_headers(headers: Iterable, allowed: Iterable[str] = REQUEST_HEADERS) -> dict:
    """只留白名单里的头（名字小写；同名多个取最后一个）。接受映射或 (名, 值) 序列。"""
    keep = set(allowed)
    items = headers.items() if isinstance(headers, Mapping) else headers
    out: dict = {}
    for name, value in items:
        key = str(name).strip().lower()
        if key in keep and isinstance(value, str) and "\r" not in value and "\n" not in value:
            out[key] = value
    return out


def _canon(headers: Mapping) -> str:
    return json.dumps(sorted((str(k), str(v)) for k, v in headers.items()), separators=(",", ":"), ensure_ascii=True)


def _sign(key: bytes, parts: Iterable) -> str:
    message = "|".join(str(part) for part in parts).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _request_mac(key: bytes, epoch, seq, request_id, method, path, body_sha, query, headers: Mapping) -> str:
    return _sign(key, (epoch, seq, request_id, method, path, body_sha, query, _canon(headers)))


def _reply_mac(key: bytes, epoch, request_id, status, refused, body_sha, headers: Mapping) -> str:
    return _sign(key, ("resp", epoch, request_id, status, refused, body_sha, _canon(headers)))


def _pack(head: dict, body: bytes) -> bytes:
    line = json.dumps(head, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return line + b"\n" + body


def _unpack(data: bytes, code: str) -> tuple[dict, bytes]:
    if not isinstance(data, (bytes, bytearray)):
        raise TunnelError(code, "not bytes")
    cut = data.find(b"\n", 0, MAX_HEAD + 1)
    if cut < 0:
        raise TunnelError(code, "no header line")
    try:
        head = json.loads(bytes(data[:cut]).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise TunnelError(code, f"header: {type(error).__name__}") from None
    if not isinstance(head, dict):
        raise TunnelError(code, "header is not an object")
    return head, bytes(data[cut + 1:])


def _timeout(value) -> float:
    if value is None:
        return DEFAULT_TIMEOUT
    number = float(value)
    if not number > 0:
        return DEFAULT_TIMEOUT
    return min(number, MAX_TIMEOUT)


# ── 请求 ──

def encode_request(session: Session, *, seq: int, request_id: str, method: str, path: str, query: str = "",
                   headers: Iterable = (), body: bytes = b"", timeout: Optional[float] = None) -> bytes:
    """云端把一个请求写成文件内容。超过上限抛 ``TunnelError(TOO_LARGE)``（调用方不写文件）。"""
    if len(body) > MAX_BODY:
        raise TunnelError(TOO_LARGE, f"{len(body)} bytes")
    if not _HEX32.match(request_id or ""):
        raise TunnelError(BAD_FORMAT, "request id")
    kept = filter_headers(headers)
    method = str(method).upper()
    body_sha = sha256_hex(body)
    head = {"v": VERSION, "id": request_id, "epoch": session.epoch, "seq": int(seq), "method": method, "path": path,
            "query": query or "", "headers": kept, "body_sha256": body_sha, "timeout": _timeout(timeout)}
    head["mac"] = _request_mac(session.key, session.epoch, int(seq), request_id, method, path, body_sha, query or "",
                               kept)
    return _pack(head, body)


def peek_id(data: bytes) -> str:
    """不核签名地读出请求编号（只用来给拒绝回复起名）；读不出回空串。"""
    try:
        head, _body = _unpack(data, BAD_FORMAT)
    except TunnelError:
        return ""
    value = head.get("id")
    return value if isinstance(value, str) and _HEX32.match(value) else ""


class ReplayGuard:
    """本会话见过的请求：``seq`` 在窗口内、``id`` 与 ``seq`` 都没见过才收。不加锁，调用方串行调用 ``admit``。"""

    def __init__(self, window: int = SEQ_WINDOW, remember: int = SEEN_IDS) -> None:
        self.window = window
        self.top = 0
        self._seqs: set = set()
        self._ids: set = set()
        self._order: collections.deque = collections.deque()
        self._remember = remember

    def seen(self, request_id: str) -> bool:
        return request_id in self._ids

    def admit(self, seq: int, request_id: str) -> None:
        if request_id in self._ids:
            raise TunnelError(REPLAYED, "id seen")
        if seq <= self.top - self.window or seq in self._seqs:
            raise TunnelError(REPLAYED, "seq outside window or seen")
        self._seqs.add(seq)
        if seq > self.top:
            self.top = seq
            floor = self.top - self.window
            self._seqs = {value for value in self._seqs if value > floor}
        self._ids.add(request_id)
        self._order.append(request_id)
        while len(self._order) > self._remember:
            self._ids.discard(self._order.popleft())


def decode_request(data: bytes, session: Session, guard: Optional[ReplayGuard] = None) -> Request:
    """桥核对一个请求文件：格式、版本、会话号、签名、正文摘要、大小、方法、路径白名单，最后才记进防重放（伪造的请求不占窗口）。
    不合规抛 ``TunnelError``。云端带来的白名单外请求头（包括 Authorization）在这里丢掉。"""
    if len(data) > MAX_HEAD + 1 + MAX_BODY:
        raise TunnelError(TOO_LARGE, f"{len(data)} bytes")
    head, body = _unpack(data, BAD_FORMAT)
    if head.get("v") != VERSION:
        raise TunnelError(BAD_VERSION, str(head.get("v")))
    request_id, epoch, seq = head.get("id"), head.get("epoch"), head.get("seq")
    method, path, query = head.get("method"), head.get("path"), head.get("query", "")
    headers, body_sha, mac = head.get("headers", {}), head.get("body_sha256"), head.get("mac")
    if not (isinstance(request_id, str) and _HEX32.match(request_id) and isinstance(epoch, str)
            and isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0
            and isinstance(method, str) and isinstance(path, str) and isinstance(query, str)
            and isinstance(headers, dict) and all(isinstance(v, str) for v in headers.values())
            and isinstance(body_sha, str) and isinstance(mac, str)):
        raise TunnelError(BAD_FORMAT, "fields")
    if epoch != session.epoch:
        raise TunnelError(STALE_EPOCH)
    expected = _request_mac(session.key, epoch, seq, request_id, method, path, body_sha, query, headers)
    if not hmac.compare_digest(expected, mac):
        raise TunnelError(BAD_MAC)
    if len(body) > MAX_BODY:
        raise TunnelError(TOO_LARGE, f"{len(body)} bytes")
    if not hmac.compare_digest(sha256_hex(body), body_sha):
        raise TunnelError(BAD_BODY, "sha256")
    if method not in METHODS:
        raise TunnelError(METHOD_NOT_ALLOWED, method)
    if not path_allowed(path):
        raise TunnelError(PATH_NOT_ALLOWED)
    if _QUERY_BAD.search(query):
        raise TunnelError(BAD_FORMAT, "query")
    try:
        timeout = _timeout(head.get("timeout"))
    except (TypeError, ValueError):
        raise TunnelError(BAD_FORMAT, "timeout") from None
    if guard is not None:
        guard.admit(seq, request_id)
    return Request(id=request_id, epoch=epoch, seq=seq, method=method, path=path, query=query,
                   headers=filter_headers(headers), body=body, timeout=timeout)


# ── 回复 ──

def encode_reply(session: Session, request_id: str, status: int, headers: Iterable = (), body: bytes = b"") -> bytes:
    """桥把站点的回复写成文件内容（状态码、白名单头、正文原样）。正文超过上限抛 ``TunnelError(RESPONSE_TOO_LARGE)``。"""
    if len(body) > MAX_BODY:
        raise TunnelError(RESPONSE_TOO_LARGE, f"{len(body)} bytes")
    kept = filter_headers(headers, RESPONSE_HEADERS)
    body_sha = sha256_hex(body)
    head = {"v": VERSION, "id": request_id, "epoch": session.epoch, "status": int(status), "headers": kept,
            "refused": "", "body_sha256": body_sha}
    head["mac"] = _reply_mac(session.key, session.epoch, request_id, int(status), "", body_sha, kept)
    return _pack(head, body)


def encode_refusal(session: Session, request_id: str, code: str) -> bytes:
    """隧道拒绝：没有状态码、没有正文，云端变成网络错误。"""
    body_sha = sha256_hex(b"")
    head = {"v": VERSION, "id": request_id, "epoch": session.epoch, "status": 0, "headers": {}, "refused": code,
            "body_sha256": body_sha}
    head["mac"] = _reply_mac(session.key, session.epoch, request_id, 0, code, body_sha, {})
    return _pack(head, b"")


def decode_reply(data: bytes, session: Session, request_id: str) -> Reply:
    """云端核对回复：会话号、编号、签名、正文摘要都对才收，否则抛 ``TunnelError(BAD_REPLY)``。"""
    if len(data) > MAX_HEAD + 1 + MAX_BODY:
        raise TunnelError(BAD_REPLY, "too large")
    head, body = _unpack(data, BAD_REPLY)
    status, headers, refused = head.get("status"), head.get("headers", {}), head.get("refused", "")
    body_sha, mac = head.get("body_sha256"), head.get("mac")
    if not (head.get("v") == VERSION and head.get("id") == request_id and head.get("epoch") == session.epoch
            and isinstance(status, int) and not isinstance(status, bool) and isinstance(headers, dict)
            and isinstance(refused, str) and isinstance(body_sha, str) and isinstance(mac, str)):
        raise TunnelError(BAD_REPLY, "fields")
    expected = _reply_mac(session.key, session.epoch, request_id, status, refused, body_sha, headers)
    if not hmac.compare_digest(expected, mac) or not hmac.compare_digest(sha256_hex(body), body_sha):
        raise TunnelError(BAD_REPLY, "mac")
    if not refused and not 100 <= status <= 599:
        raise TunnelError(BAD_REPLY, "status")
    return Reply(id=request_id, status=status, headers=filter_headers(headers, RESPONSE_HEADERS), body=body,
                 refused=refused)
