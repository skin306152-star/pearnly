# -*- coding: utf-8 -*-
"""云端代录的 Express 登录凭据：桥（客户自己的 CC 电脑）经隧道 ``ctl\\`` 交给云端那份 CC（做法页「云端Express-代录凭据交接
做法-11-16」第二版）。本模块只有协议与两侧的处理函数，读写文件、凭据管理器、时钟、随机数都由调用方注入，Mac 上离线测。

加密一律用 ``cryptography`` 的实现（X25519、HKDF-SHA256、AES-256-GCM、HMAC-SHA256），本模块不实现任何加密原语。

文件（都在隧道 ``ctl\\`` 下，写入先 ``.part`` 再改名，读的一方读后删除并回读确认已不在）::

    cred-req.json         云端 → 桥  {v, id, epoch, connection, cloud_pub, expires, tag}
    cred-resp.json        桥 → 云端  {v, id, epoch, bridge_pub, nonce, ct, expires, tag}
    cred-forget.json      桥 → 云端  {v, id, epoch, connection, expires, tag}
    cred-forget-ack.json  云端 → 桥  {v, id, epoch, connection, expires, tag}

- 认证：两边都有这台云端电脑的**设备密钥**（``link_store.LinkStore.device_key``）。由它经 HKDF 派生 HMAC 密钥，给每个文件
  的规范化串签 ``tag``；没有设备密钥的一方算不出，替换了公钥、会话号、编号、过期时间的也对不上（原因码 ``unpaired``）。
- 加密：云端每次请求生成一次性 X25519 私钥（只在内存），桥每次回应也生成一次性的；ECDH 得到的共享秘密经 HKDF-SHA256
  （salt 由设备密钥派生，info 含 ``epoch|id|cloud_pub|bridge_pub``）得到 AES-256-GCM 密钥，nonce 96 位随机，附加数据是回应
  的规范化串。密文、nonce 被改动时解不开（``tampered``）。
- 一次性与过期：编号 128 位随机十六进制；``expires`` = 发出时刻 + 120 秒（墙上时钟，两台 Windows 都对时）；桥与云端各记已见
  编号，见过的一律 ``replayed``。会话号要与当前隧道会话（``tunnel_protocol.Session.epoch``）一致（``wrong_epoch``）。
- 桥只在本机存有这条连接的凭据（用户本人点过「授权登录」）时才回应，否则 ``not_authorized``、不写任何文件。

日志只记事件名、原因码、连接号；用户名、密码、密文、公钥、密钥一概不记，也不进异常文本与 ``repr``。
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, MutableSet, Optional, Tuple

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..logger import get_logger

log = get_logger(__name__)

VERSION = 1
TTL_SECONDS = 120
# 对方时钟比本机快多少秒以内的 expires 还认（超出当格式不对）
CLOCK_SKEW = 30
SWEEP_DELAY = 10.0

REQUEST_FILE = "cred-req.json"
RESPONSE_FILE = "cred-resp.json"
FORGET_FILE = "cred-forget.json"
FORGET_ACK_FILE = "cred-forget-ack.json"
FILES = (REQUEST_FILE, RESPONSE_FILE, FORGET_FILE, FORGET_ACK_FILE)

# 原因码
OK = "ok"
TAMPERED = "tampered"
REPLAYED = "replayed"
EXPIRED = "expired"
UNPAIRED = "unpaired"
WRONG_EPOCH = "wrong_epoch"
NOT_AUTHORIZED = "not_authorized"
MALFORMED = "malformed"
CODES = (TAMPERED, REPLAYED, EXPIRED, UNPAIRED, WRONG_EPOCH, NOT_AUTHORIZED, MALFORMED)

_LABEL = b"pearnly-cc/cloud-cred/v1"
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_CONNECTION = re.compile(r"^[A-Za-z0-9._:-]{1,140}$")
_MAX_FILE = 16 * 1024
_NONCE_BYTES = 12
_PUB_BYTES = 32


class Refused(Exception):
    """交接被拒；``code`` 是原因码（``CODES``）。文本只有原因码。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Answer:
    """桥处理一份请求的结果：``code`` 原因码（成功是 ``ok``），``response`` 要写进 ``cred-resp.json`` 的内容（拒绝时 None）。"""

    code: str
    connection: str = ""
    response: Optional[bytes] = field(default=None, repr=False)


@dataclass(frozen=True)
class PendingRequest:
    """云端发出的一次请求：一次性私钥只在内存（``repr`` 不带）。"""

    id: str
    epoch: str
    connection: str
    cloud_pub: str
    expires: int
    private_key: X25519PrivateKey = field(repr=False)


@dataclass(frozen=True)
class PendingForget:
    id: str
    epoch: str
    connection: str
    expires: int


# ── 校验与派生 ────────────────────────────────────────────────────────────────

def connection_of(value) -> str:
    """连接号（与 ``express_operator.credentials.target`` 的后缀同一个字）：字母数字与 ``._:-``，1–140 个字符。"""
    text = str(value or "").strip()
    if not _CONNECTION.match(text):
        raise Refused(MALFORMED)
    return text


def bridge_target(host: str, connection: str) -> str:
    """桥本机存云端代录凭据的 Generic 目标名 ``Pearnly CC/cloud-express/<主机>/<连接>``（与本地代录那条分开）。"""
    from . import rdp

    return "Pearnly CC/cloud-express/" + rdp.host(host) + "/" + connection_of(connection)


def _key_material(device_key) -> bytes:
    if isinstance(device_key, bytes):
        raw = device_key
    else:
        raw = str(device_key or "").encode("utf-8")
    if len(raw) < 16:
        raise Refused(UNPAIRED)
    return raw


def _hkdf(material: bytes, info: bytes, salt: Optional[bytes] = None) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt if salt is not None else _LABEL,
                info=_LABEL + b"|" + info).derive(material)


def _auth_key(device_key) -> bytes:
    return _hkdf(_key_material(device_key), b"auth")


def _salt(device_key) -> bytes:
    return _hkdf(_key_material(device_key), b"salt")


def _canon(*parts) -> bytes:
    return "|".join(str(part) for part in parts).encode("utf-8")


def _tag(device_key, message: bytes) -> str:
    mac = hmac.HMAC(_auth_key(device_key), hashes.SHA256())
    mac.update(message)
    return mac.finalize().hex()


def _check_tag(device_key, message: bytes, tag) -> None:
    try:
        expected = bytes.fromhex(str(tag or ""))
    except ValueError:
        raise Refused(UNPAIRED) from None
    mac = hmac.HMAC(_auth_key(device_key), hashes.SHA256())
    mac.update(message)
    try:
        mac.verify(expected)
    except InvalidSignature:
        raise Refused(UNPAIRED) from None


def _request_canon(v, request_id, epoch, connection, cloud_pub, expires) -> bytes:
    return _canon("req", v, request_id, epoch, connection, cloud_pub, expires)


def _response_canon(v, request_id, epoch, cloud_pub, bridge_pub, expires) -> bytes:
    return _canon("resp", v, request_id, epoch, cloud_pub, bridge_pub, expires)


def _forget_canon(kind, v, forget_id, epoch, connection, expires) -> bytes:
    return _canon(kind, v, forget_id, epoch, connection, expires)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(value, size: Optional[int] = None) -> bytes:
    try:
        raw = base64.b64decode(str(value or ""), validate=True)
    except (binascii.Error, ValueError):
        raise Refused(MALFORMED) from None
    if size is not None and len(raw) != size:
        raise Refused(MALFORMED)
    return raw


def _pub_bytes(private_key: X25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _aes_key(shared: bytes, device_key, epoch, request_id, cloud_pub, bridge_pub) -> bytes:
    return _hkdf(shared, _canon("enc", epoch, request_id, cloud_pub, bridge_pub), salt=_salt(device_key))


def _encode(head: dict) -> bytes:
    return (json.dumps(head, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")


def _decode(data) -> dict:
    if not isinstance(data, (bytes, bytearray)) or len(data) > _MAX_FILE:
        raise Refused(MALFORMED)
    try:
        head = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused(MALFORMED) from None
    if not isinstance(head, dict) or head.get("v") != VERSION:
        raise Refused(MALFORMED)
    return head


def _hex_id(value) -> str:
    if not isinstance(value, str) or not _HEX32.match(value):
        raise Refused(MALFORMED)
    return value


def _expires(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refused(MALFORMED)
    return value


def _check_time(expires: int, now: float) -> None:
    if now > expires:
        raise Refused(EXPIRED)
    if expires > now + TTL_SECONDS + CLOCK_SKEW:
        raise Refused(MALFORMED)


def new_id() -> str:
    return secrets.token_hex(16)


def _note(event: str, code: str, connection: str = "") -> None:
    """日志只有事件名、原因码、连接号。"""
    log.info("云端凭据交接 · 事件=%s 原因=%s 连接=%s", event, code, connection or "-")


# ── 云端侧：请求与解开回应 ──────────────────────────────────────────────────────

def make_request(device_key, epoch: str, connection: str, now: float) -> Tuple[PendingRequest, bytes]:
    """云端要这条连接的凭据：生成一次性 X25519（私钥只在回的 :class:`PendingRequest` 里），回 ``cred-req.json`` 的内容。"""
    connection = connection_of(connection)
    epoch = _hex_id(epoch)
    private_key = X25519PrivateKey.generate()
    cloud_pub = _b64(_pub_bytes(private_key))
    request_id, expires = new_id(), int(now) + TTL_SECONDS
    head = {"v": VERSION, "id": request_id, "epoch": epoch, "connection": connection, "cloud_pub": cloud_pub,
            "expires": expires}
    head["tag"] = _tag(device_key, _request_canon(VERSION, request_id, epoch, connection, cloud_pub, expires))
    _note("request", OK, connection)
    return PendingRequest(request_id, epoch, connection, cloud_pub, expires, private_key), _encode(head)


def open_response(data, pending: PendingRequest, device_key, now: float,
                  seen_ids: Optional[MutableSet] = None) -> Tuple[str, str]:
    """云端解开桥的回应，回 ``(用户名, 密码)``；不对抛 :class:`Refused`。``seen_ids`` 记下已解开过的编号（防重放）。"""
    try:
        head = _decode(data)
        request_id = _hex_id(head.get("id"))
        if request_id != pending.id or (seen_ids is not None and request_id in seen_ids):
            raise Refused(REPLAYED)
        epoch, bridge_pub, nonce = head.get("epoch"), head.get("bridge_pub"), head.get("nonce")
        expires = _expires(head.get("expires"))
        if not all(isinstance(item, str) for item in (epoch, bridge_pub, nonce, head.get("ct"))):
            raise Refused(MALFORMED)
        aad = _response_canon(VERSION, request_id, epoch, pending.cloud_pub, bridge_pub, expires)
        _check_tag(device_key, aad, head.get("tag"))
        if epoch != pending.epoch:
            raise Refused(WRONG_EPOCH)
        if expires != pending.expires:
            raise Refused(UNPAIRED)
        _check_time(expires, now)
        peer = X25519PublicKey.from_public_bytes(_unb64(bridge_pub, _PUB_BYTES))
        key = _aes_key(pending.private_key.exchange(peer), device_key, epoch, request_id, pending.cloud_pub, bridge_pub)
        try:
            plain = AESGCM(key).decrypt(_unb64(nonce, _NONCE_BYTES), _unb64(head.get("ct")), aad)
        except InvalidTag:
            raise Refused(TAMPERED) from None
        if seen_ids is not None:
            seen_ids.add(request_id)
        try:
            body = json.loads(plain.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused(MALFORMED) from None
        finally:
            plain = b""
        if not isinstance(body, dict):
            raise Refused(MALFORMED)
        user, password = body.get("user"), body.get("password")
        body = None
        if not isinstance(user, str) or not isinstance(password, str) or not user or not password:
            raise Refused(MALFORMED)
    except Refused as exc:
        _note("response_refused", exc.code, pending.connection)
        raise
    _note("response", OK, pending.connection)
    return user, password


# ── 桥侧：回应请求 ─────────────────────────────────────────────────────────────

def answer(request_bytes, device_key, epoch: str, now: float, seen_ids: MutableSet,
           read_credential: Callable[[str], Optional[Tuple[str, str]]]) -> Answer:
    """桥处理一份 ``cred-req.json``：校验标签、会话号、过期、编号；本机存有这条连接的凭据才加密回应。不抛，回原因码。"""
    connection = ""
    try:
        head = _decode(request_bytes)
        request_id = _hex_id(head.get("id"))
        req_epoch, cloud_pub = head.get("epoch"), head.get("cloud_pub")
        connection = connection_of(head.get("connection"))
        expires = _expires(head.get("expires"))
        if not isinstance(req_epoch, str) or not isinstance(cloud_pub, str):
            raise Refused(MALFORMED)
        _check_tag(device_key, _request_canon(VERSION, request_id, req_epoch, connection, cloud_pub, expires),
                   head.get("tag"))
        if req_epoch != epoch:
            raise Refused(WRONG_EPOCH)
        _check_time(expires, now)
        if request_id in seen_ids:
            raise Refused(REPLAYED)
        seen_ids.add(request_id)
        peer = X25519PublicKey.from_public_bytes(_unb64(cloud_pub, _PUB_BYTES))
        found = read_credential(connection)
        if not found or not found[0] or not found[1]:
            raise Refused(NOT_AUTHORIZED)
        private_key = X25519PrivateKey.generate()
        bridge_pub = _b64(_pub_bytes(private_key))
        aad = _response_canon(VERSION, request_id, epoch, cloud_pub, bridge_pub, expires)
        key = _aes_key(private_key.exchange(peer), device_key, epoch, request_id, cloud_pub, bridge_pub)
        del private_key
        nonce = os.urandom(_NONCE_BYTES)
        plain = json.dumps({"user": found[0], "password": found[1]}).encode("utf-8")
        found = None
        try:
            ct = AESGCM(key).encrypt(nonce, plain, aad)
        finally:
            plain, key = b"", b""
        head = {"v": VERSION, "id": request_id, "epoch": epoch, "bridge_pub": bridge_pub, "nonce": _b64(nonce),
                "ct": _b64(ct), "expires": expires, "tag": _tag(device_key, aad)}
    except Refused as exc:
        _note("request_refused", exc.code, connection)
        return Answer(exc.code, connection)
    except Exception as exc:  # noqa: BLE001 - 读本机凭据等失败：当没授权，不回应
        log.warning("云端凭据交接 · 事件=answer_failed 原因=%s 连接=%s", type(exc).__name__, connection or "-")
        return Answer(NOT_AUTHORIZED, connection)
    _note("answer", OK, connection)
    return Answer(OK, connection, _encode(head))


# ── 撤销：桥发、云端删并回执 ─────────────────────────────────────────────────────

def make_forget(device_key, epoch: str, connection: str, now: float) -> Tuple[PendingForget, bytes]:
    """桥请云端删掉这条连接的凭据（``cred-forget.json``，内容只有连接号）。"""
    connection, epoch = connection_of(connection), _hex_id(epoch)
    forget_id, expires = new_id(), int(now) + TTL_SECONDS
    head = {"v": VERSION, "id": forget_id, "epoch": epoch, "connection": connection, "expires": expires,
            "tag": _tag(device_key, _forget_canon("forget", VERSION, forget_id, epoch, connection, expires))}
    _note("forget_request", OK, connection)
    return PendingForget(forget_id, epoch, connection, expires), _encode(head)


def answer_forget(data, device_key, epoch: str, now: float, seen_ids: MutableSet,
                  forget: Callable[[str], object]) -> Tuple[str, str, Optional[bytes]]:
    """云端处理撤销：校验后 ``forget(连接号)``，回 ``(原因码, 连接号, cred-forget-ack.json 的内容或 None)``。不抛。"""
    connection = ""
    try:
        head = _decode(data)
        forget_id = _hex_id(head.get("id"))
        connection = connection_of(head.get("connection"))
        forget_epoch, expires = head.get("epoch"), _expires(head.get("expires"))
        if not isinstance(forget_epoch, str):
            raise Refused(MALFORMED)
        _check_tag(device_key, _forget_canon("forget", VERSION, forget_id, forget_epoch, connection, expires),
                   head.get("tag"))
        if forget_epoch != epoch:
            raise Refused(WRONG_EPOCH)
        _check_time(expires, now)
        if forget_id in seen_ids:
            raise Refused(REPLAYED)
        seen_ids.add(forget_id)
        forget(connection)
    except Refused as exc:
        _note("forget_refused", exc.code, connection)
        return exc.code, connection, None
    ack = {"v": VERSION, "id": forget_id, "epoch": epoch, "connection": connection, "expires": expires,
           "tag": _tag(device_key, _forget_canon("forget-ack", VERSION, forget_id, epoch, connection, expires))}
    _note("forget", OK, connection)
    return OK, connection, _encode(ack)


def open_forget_ack(data, pending: PendingForget, device_key) -> str:
    """桥核对云端的撤销回执；对上回 ``ok``，否则回原因码。"""
    try:
        head = _decode(data)
        if _hex_id(head.get("id")) != pending.id:
            raise Refused(REPLAYED)
        _check_tag(device_key, _forget_canon("forget-ack", VERSION, pending.id, head.get("epoch"),
                                             head.get("connection"), head.get("expires")), head.get("tag"))
        if head.get("epoch") != pending.epoch or head.get("connection") != pending.connection \
                or head.get("expires") != pending.expires:
            raise Refused(UNPAIRED)
    except Refused as exc:
        _note("forget_ack_refused", exc.code, pending.connection)
        return exc.code
    _note("forget_ack", OK, pending.connection)
    return OK


# ── ctl 目录里的文件 ─────────────────────────────────────────────────────────

def write_atomic(path: Path, data: bytes) -> None:
    """先写 ``.part`` 再改名（同 ``ctl\\session.json``）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with open(part, "wb") as handle:
            handle.write(data)
        os.replace(part, path)
    except BaseException:
        remove_confirmed(part)
        raise


def remove_confirmed(path: Path) -> bool:
    """删掉并回读确认不在；回 True = 已不在。"""
    path = Path(path)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("云端凭据交接 · 事件=remove_failed 原因=%s", type(exc).__name__)
    return not path.exists()


def take(path: Path, limit: int = _MAX_FILE) -> Optional[bytes]:
    """读一份交接文件，读后删除并回读确认（删不掉仍回内容，由兜底再删）；没有回 None。最多读 ``limit`` + 1 字节。"""
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit + 1)
    except FileNotFoundError:
        return None
    if not remove_confirmed(path):
        log.warning("云端凭据交接 · 事件=remove_unconfirmed 原因=-")
    return data


def sweep(ctl_dir: Path, names: Iterable[str] = FILES) -> bool:
    """兜底：删掉 ``ctl\\`` 里这些交接文件（及其 ``.part``），回全部确认不在。"""
    ok = True
    for name in names:
        for path in (Path(ctl_dir) / name, Path(ctl_dir) / (name + ".part")):
            ok = remove_confirmed(path) and ok
    return ok


class Sweeper:
    """桥写完回应后 :data:`SWEEP_DELAY` 秒再删一遍（由宿主的循环到点调 :meth:`run_due`）。"""

    def __init__(self) -> None:
        self._due: list = []

    def schedule(self, at: float, names: Iterable[str]) -> None:
        self._due.append((at, tuple(names)))

    def run_due(self, ctl_dir: Path, now: float) -> int:
        ready = [item for item in self._due if item[0] <= now]
        self._due = [item for item in self._due if item[0] > now]
        for _, names in ready:
            sweep(ctl_dir, names)
        return len(ready)

    @property
    def pending(self) -> int:
        return len(self._due)

    @property
    def next_due(self):
        """最早一次兜底删的时刻；没有回 None（宿主据此决定中转线程等多久）。"""
        return min((item[0] for item in self._due), default=None)


__all__ = ["Answer", "CODES", "EXPIRED", "FILES", "FORGET_ACK_FILE", "FORGET_FILE", "MALFORMED", "NOT_AUTHORIZED", "OK",
           "PendingForget", "PendingRequest", "REPLAYED", "REQUEST_FILE", "RESPONSE_FILE", "Refused", "SWEEP_DELAY",
           "Sweeper", "TAMPERED", "TTL_SECONDS", "UNPAIRED", "WRONG_EPOCH", "answer", "answer_forget", "bridge_target",
           "connection_of", "make_forget", "make_request", "open_forget_ack", "open_response", "remove_confirmed",
           "sweep", "take", "write_atomic"]
