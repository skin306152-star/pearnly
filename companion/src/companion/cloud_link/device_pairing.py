# -*- coding: utf-8 -*-
"""设备密钥的一次性配对：把桥为云端那台电脑生成的设备密钥（``link_store.LinkStore.device_key``，只在桥本机 DPAPI）
交给云端那份 CC。到场那一次，用户在云端 PowerShell 手打一行命令，带上桥生成、只在桥的可见界面上显示的**一次性令牌**。
做法页「云端Express-代录凭据交接做法-11-16」的「设备密钥的一次性配对」一节。

加密一律用 ``cryptography`` 的实现（X25519、HKDF-SHA256、AES-256-GCM、HMAC-SHA256），本模块不实现任何加密原语。
令牌的校验字符用 CRC-10 只为发现手打错字，**不提供任何安全性**。

令牌
    128 位随机（``secrets``），Crockford Base32（字母表 ``0-9 A-Z`` 去掉 ``I L O U``）编成 26 个字符（末 2 位补零），
    后接 2 个校验字符，共 28 个字符，4 个一组用短横线分隔，例如 ``7K3M-Q9TB-...-XXCC``（34 个可见字符）。
    校验：把 26 个数据字符依次按 5 位展开成 130 位，算 CRC-10（多项式 x^10+x^9+x^5+x^4+x+1，即 0x233，初值 0，
    不反射、不异或输出），10 位结果拆成 2 个 Base32 字符。CRC-10 能发现任何不超过 10 位的突发错误，所以任意一个字符
    打错、相邻两个字符对调一定能发现。读入时不分大小写，短横线与空白一概忽略，``O`` 当 ``0``、``I`` / ``L`` 当 ``1``。

有效期与次数（桥侧 :class:`BridgePairing`）
    生成后 2 分钟内有效，只能用一次；**收到的任何一份请求不对（格式错、标签不对、过期）令牌立即作废**，要重新生成。
    再生成一个，旧的随即作废。

协议（都在隧道 ``ctl\\`` 下，写入先 ``.part`` 再改名，读的一方读后删除并回读）::

    pair-req.json   云端 → 桥  {v, id, cloud_pub, expires, tag}
    pair-resp.json  桥 → 云端  {v, id, bridge_pub, nonce, ct, tag}
    pair-ack.json   云端 → 桥  {v, id, ok, tag}

- 认证密钥 = HKDF-SHA256(令牌 128 位, salt=固定串, info=固定串|auth|主机)；``tag`` = HMAC-SHA256(认证密钥, 规范串)。
  主机是桥登记的云端主机地址（``rdp.host`` 规范化），两边都按它派生。
- 云端生成一次性 X25519；桥核对标签、过期、令牌没用过后也生成一次性 X25519；ECDH 的共享秘密经 HKDF-SHA256
  （salt = HKDF(令牌, info=固定串|salt|主机)，info 含 ``id|cloud_pub|bridge_pub``）得 AES-256-GCM 密钥，加密设备密钥，
  附加数据是回应的规范串。私钥只在内存、用完即丢，交换有前向保密。
- 云端解开后把设备密钥存进云端用户自己的凭据管理器（Generic，DPAPI，``device_target(主机)``），读回核对，结果写回执。

日志只记事件名与原因码；令牌、设备密钥、公钥、密文一概不记，也不进异常文本与 ``repr``。
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..logger import get_logger
from . import cred_handoff as handoff
from . import rdp

log = get_logger(__name__)

VERSION = 1
TOKEN_TTL = 120.0          # 令牌有效期（秒）
REQUEST_TTL = 120          # 请求的 expires = 发出时刻 + 120 秒（墙上时钟）
CLOCK_SKEW = 30
ACK_WAIT = 60.0            # 桥写完回应后最多等回执多久
SWEEP_DELAY = handoff.SWEEP_DELAY
DEFAULT_TIMEOUT = 30.0
POLL_SECONDS = 0.5

REQUEST_FILE = "pair-req.json"
RESPONSE_FILE = "pair-resp.json"
ACK_FILE = "pair-ack.json"
FILES = (REQUEST_FILE, RESPONSE_FILE, ACK_FILE)

DEVICE_PREFIX = "Pearnly CC/cloud-device/"

# 状态（桥侧）
IDLE = "idle"
WAITING = "waiting"        # 令牌已生成，等云端请求
ANSWERED = "answered"      # 已回应，等回执
PAIRED = "paired"
FAILED = "failed"

# 原因码
OK = "ok"
CHECKSUM = "checksum"      # 令牌校验字符对不上（手打错字），云端不发请求
MALFORMED = "malformed"
TAMPERED = "tampered"      # 标签 / 密文对不上（令牌不对或文件被换过）
EXPIRED = "expired"
REPLAYED = "replayed"
NO_TOKEN = "no_token"      # 桥上没有可用的令牌（没生成、已用过、已作废）
TIMEOUT = "timeout"
STORE_FAILED = "store_failed"
NOT_STORED = "not_stored"  # 云端没存上（回执 ok=false）
NO_DEVICE_KEY = "no_device_key"  # 桥上读不到设备密钥（没有这条云端连接）
CODES = (CHECKSUM, MALFORMED, TAMPERED, EXPIRED, REPLAYED, NO_TOKEN, TIMEOUT, STORE_FAILED, NOT_STORED, NO_DEVICE_KEY)

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {ch: index for index, ch in enumerate(ALPHABET)}
_DECODE.update({"O": 0, "I": 1, "L": 1})
_TOKEN_BYTES = 16
_DATA_CHARS = 26
_CHECK_CHARS = 2
_GROUP = 4
_CRC_POLY = 0x233
_LABEL = b"pearnly-cc/cloud-pair/v1"
_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_MAX_FILE = 16 * 1024
_NONCE_BYTES = 12
_PUB_BYTES = 32


class Refused(Exception):
    """配对被拒；``code`` 是原因码。文本只有原因码。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


# ── 令牌 ──────────────────────────────────────────────────────────────────────

def _crc10(symbols) -> int:
    """数据字符（各 5 位）的 CRC-10（0x233，初值 0）。只用来发现手打错字。"""
    register = 0
    for symbol in symbols:
        for shift in range(4, -1, -1):
            feedback = ((register >> 9) & 1) ^ ((symbol >> shift) & 1)
            register = (register << 1) & 0x3FF
            if feedback:
                register ^= _CRC_POLY & 0x3FF
    return register


def _symbols_of(raw: bytes) -> list:
    number = int.from_bytes(raw, "big") << (_DATA_CHARS * 5 - _TOKEN_BYTES * 8)
    return [(number >> (5 * (_DATA_CHARS - 1 - index))) & 0x1F for index in range(_DATA_CHARS)]


def format_token(raw: bytes) -> str:
    """128 位 → ``XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXCC``（26 个数据字符 + 2 个校验字符，4 个一组）。"""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != _TOKEN_BYTES:
        raise ValueError("token")
    symbols = _symbols_of(bytes(raw))
    check = _crc10(symbols)
    text = "".join(ALPHABET[s] for s in symbols) + ALPHABET[check >> 5] + ALPHABET[check & 0x1F]
    return "-".join(text[i:i + _GROUP] for i in range(0, len(text), _GROUP))


def new_token() -> tuple:
    """生成一个令牌：回 ``(128 位原文, 显示用的字符串)``。"""
    raw = secrets.token_bytes(_TOKEN_BYTES)
    return raw, format_token(raw)


def parse_token(text) -> bytes:
    """读用户打的令牌（大小写、短横线、空白都容错）：回 128 位原文；字符不对、长度不对抛 ``Refused(malformed)``，
    校验字符对不上抛 ``Refused(checksum)``。"""
    cleaned = "".join(ch for ch in str(text or "").upper() if not ch.isspace() and ch not in "-_")
    if len(cleaned) != _DATA_CHARS + _CHECK_CHARS or any(ch not in _DECODE for ch in cleaned):
        raise Refused(MALFORMED)
    values = [_DECODE[ch] for ch in cleaned]
    data, check = values[:_DATA_CHARS], (values[_DATA_CHARS] << 5) | values[_DATA_CHARS + 1]
    if _crc10(data) != check:
        raise Refused(CHECKSUM)
    number = 0
    for value in data:
        number = (number << 5) | value
    pad = _DATA_CHARS * 5 - _TOKEN_BYTES * 8
    if number & ((1 << pad) - 1):
        raise Refused(MALFORMED)
    return (number >> pad).to_bytes(_TOKEN_BYTES, "big")


# ── 派生与编码 ────────────────────────────────────────────────────────────────

def _host_bytes(host: str) -> bytes:
    try:
        return rdp.host(host).encode("utf-8")
    except rdp.RdpValueError:
        raise Refused(MALFORMED) from None


def _hkdf(material: bytes, info: bytes, salt: bytes = _LABEL) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=_LABEL + b"|" + info).derive(material)


def auth_key(raw_token: bytes, host: str) -> bytes:
    return _hkdf(raw_token, b"auth|" + _host_bytes(host))


def _salt(raw_token: bytes, host: str) -> bytes:
    return _hkdf(raw_token, b"salt|" + _host_bytes(host))


def _canon(*parts) -> bytes:
    return "|".join(str(part) for part in parts).encode("utf-8")


def _request_canon(request_id, host, cloud_pub, expires) -> bytes:
    return _canon("pair-req", VERSION, request_id, rdp.host(host), cloud_pub, expires)


def _response_canon(request_id, host, cloud_pub, bridge_pub) -> bytes:
    return _canon("pair-resp", VERSION, request_id, rdp.host(host), cloud_pub, bridge_pub)


def _ack_canon(request_id, host, ok) -> bytes:
    return _canon("pair-ack", VERSION, request_id, rdp.host(host), "1" if ok else "0")


def _tag(key: bytes, message: bytes) -> str:
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(message)
    return mac.finalize().hex()


def _check_tag(key: bytes, message: bytes, tag) -> None:
    try:
        expected = bytes.fromhex(str(tag or ""))
    except ValueError:
        raise Refused(TAMPERED) from None
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(message)
    try:
        mac.verify(expected)
    except InvalidSignature:
        raise Refused(TAMPERED) from None


def _aes_key(shared: bytes, raw_token: bytes, host: str, request_id, cloud_pub, bridge_pub) -> bytes:
    return _hkdf(shared, _canon("enc", request_id, cloud_pub, bridge_pub), salt=_salt(raw_token, host))


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


def _pub(private_key: X25519PrivateKey) -> str:
    return _b64(private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


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


def _note(event: str, code: str) -> None:
    """日志只有事件名与原因码（时间由日志本身带）。"""
    log.info("云端设备配对 · 事件=%s 原因=%s", event, code)


# ── 桥侧 ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Ticket:
    """给可见界面显示的令牌与过期时间（墙上时钟秒）。``repr`` 不带令牌。"""

    token: str = field(repr=False)
    expires: int


class BridgePairing:
    """桥这边一次配对的状态：令牌只在内存，用一次、失败一次、过期、重新生成都作废。线程安全。

    ``device_key()`` 现取设备密钥；``clock`` 单调时钟（有效期、兜底删），``wall`` 墙上时钟（请求的 expires）。"""

    def __init__(self, host: str, device_key: Callable[[], str], *, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.host = rdp.host(host)
        self._device_key = device_key
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._raw: Optional[bytes] = None
        self._deadline = 0.0
        self._auth: Optional[bytes] = None     # 已回应、等回执时留着核回执
        self._answered_id = ""
        self._ack_deadline = 0.0
        self._sweep_at: Optional[float] = None
        self.state = IDLE
        self.code = ""
        self.expires = 0

    def __repr__(self) -> str:
        return f"BridgePairing(host={self.host!r}, state={self.state!r}, code={self.code!r})"

    def begin(self) -> Ticket:
        """生成新令牌（旧的随即作废）。令牌只回给调用方显示，不进日志。"""
        raw, text = new_token()
        with self._lock:
            self._clear()
            self._raw, self._deadline = raw, self._clock() + TOKEN_TTL
            self.expires = int(self._wall() + TOKEN_TTL)
            self.state, self.code = WAITING, ""
        _note("begin", OK)
        return Ticket(text, self.expires)

    def cancel(self) -> None:
        with self._lock:
            if self.state in (WAITING, ANSWERED):
                self._fail(NO_TOKEN, "cancel")

    @property
    def active(self) -> bool:
        return self.state in (WAITING, ANSWERED)

    def status(self) -> dict:
        with self._lock:
            return {"state": self.state, "code": self.code, "expires": self.expires}

    def next_due(self) -> Optional[float]:
        """下一个要处理的时刻（单调时钟）：令牌过期、回执超时、兜底删；没有回 None。"""
        with self._lock:
            times = [t for t in (self._deadline if self.state == WAITING else None,
                                 self._ack_deadline if self.state == ANSWERED else None, self._sweep_at)
                     if t is not None]
        return min(times) if times else None

    def _clear(self) -> None:
        self._raw, self._auth, self._answered_id = None, None, ""
        self._deadline = self._ack_deadline = 0.0

    def _fail(self, code: str, event: str) -> None:
        self._clear()
        self.state, self.code = FAILED, code
        _note(event, code)

    def pump(self, ctl_dir: Path) -> bool:
        """看一遍 ``ctl``：处理请求、收回执、到点作废与兜底删。状态有变化回 True。"""
        ctl_dir = Path(ctl_dir)
        before = (self.state, self.code)
        request = handoff.take(ctl_dir / REQUEST_FILE)
        if request is not None:
            self._answer(ctl_dir, request)
        ack = handoff.take(ctl_dir / ACK_FILE)
        if ack is not None:
            self._acked(ack)
        now = self._clock()
        with self._lock:
            if self.state == WAITING and now > self._deadline:
                self._fail(EXPIRED, "token_expired")
            elif self.state == ANSWERED and now > self._ack_deadline:
                self._fail(TIMEOUT, "ack_timeout")
            sweep_due = self._sweep_at is not None and now >= self._sweep_at
            if sweep_due:
                self._sweep_at = None
        if sweep_due:
            handoff.sweep(ctl_dir, (REQUEST_FILE, RESPONSE_FILE))
        return (self.state, self.code) != before

    def _answer(self, ctl_dir: Path, data: bytes) -> None:
        with self._lock:
            raw, deadline = self._raw, self._deadline
            # 不论这份请求对不对，令牌都用掉了（失败上限 = 1）
            self._raw = None
            if raw is None or self.state != WAITING:
                _note("request_refused", NO_TOKEN)
                return
            try:
                if self._clock() > deadline:
                    raise Refused(EXPIRED)
                head = _decode(data)
                request_id = _hex_id(head.get("id"))
                cloud_pub, expires = head.get("cloud_pub"), head.get("expires")
                if not isinstance(cloud_pub, str) or isinstance(expires, bool) or not isinstance(expires, int):
                    raise Refused(MALFORMED)
                key = auth_key(raw, self.host)
                _check_tag(key, _request_canon(request_id, self.host, cloud_pub, expires), head.get("tag"))
                now = self._wall()
                if now > expires:
                    raise Refused(EXPIRED)
                if expires > now + REQUEST_TTL + CLOCK_SKEW:
                    raise Refused(MALFORMED)
                peer = X25519PublicKey.from_public_bytes(_unb64(cloud_pub, _PUB_BYTES))
                device_key = str(self._device_key() or "").encode("utf-8")
                if len(device_key) < 16:
                    raise Refused(NO_DEVICE_KEY)
                private_key = X25519PrivateKey.generate()
                bridge_pub = _pub(private_key)
                aad = _response_canon(request_id, self.host, cloud_pub, bridge_pub)
                aes = _aes_key(private_key.exchange(peer), raw, self.host, request_id, cloud_pub, bridge_pub)
                del private_key
                nonce = os.urandom(_NONCE_BYTES)
                try:
                    ct = AESGCM(aes).encrypt(nonce, device_key, aad)
                finally:
                    device_key, aes = b"", b""
                response = _encode({"v": VERSION, "id": request_id, "bridge_pub": bridge_pub, "nonce": _b64(nonce),
                                    "ct": _b64(ct), "tag": _tag(key, aad)})
                handoff.write_atomic(ctl_dir / RESPONSE_FILE, response)
            except Refused as exc:
                self._fail(exc.code, "request_refused")
                return
            except Exception as exc:  # noqa: BLE001 - 读设备密钥、写文件失败：作废，不回应
                self._fail(MALFORMED, "answer_failed:" + type(exc).__name__)
                return
            self._auth, self._answered_id = key, request_id
            self._ack_deadline = self._clock() + ACK_WAIT
            self._sweep_at = self._clock() + SWEEP_DELAY
            self.state = ANSWERED
        _note("answer", OK)

    def _acked(self, data: bytes) -> None:
        with self._lock:
            if self.state != ANSWERED or self._auth is None:
                _note("ack_refused", NO_TOKEN)
                return
            try:
                head = _decode(data)
                if _hex_id(head.get("id")) != self._answered_id:
                    raise Refused(REPLAYED)
                ok = head.get("ok")
                if not isinstance(ok, bool):
                    raise Refused(MALFORMED)
                _check_tag(self._auth, _ack_canon(self._answered_id, self.host, ok), head.get("tag"))
            except Refused as exc:
                self._fail(exc.code, "ack_refused")
                return
            if ok:
                self._clear()
                self.state, self.code = PAIRED, OK
                _note("paired", OK)
            else:
                self._fail(NOT_STORED, "ack")


# ── 云端侧 ────────────────────────────────────────────────────────────────────

def device_target(host: str) -> str:
    """云端用户凭据管理器里存设备密钥的 Generic 目标名 ``Pearnly CC/cloud-device/<主机>``。"""
    try:
        return DEVICE_PREFIX + rdp.host(host)
    except rdp.RdpValueError:
        raise Refused(MALFORMED) from None


def _store_device_key(host: str, device_key: str) -> None:
    from ..express_operator import credentials

    credentials.store(device_target(host), rdp.host(host), device_key)


def load_device_key(host: str, *, read: Optional[Callable[[str], object]] = None) -> str:
    """从云端本用户的凭据管理器读配对时存下的设备密钥；没有 / 不是 Windows 回空串。"""
    if read is None:
        from ..express_operator import credentials

        read = credentials.read
    try:
        found = read(device_target(host))
    except Refused:
        return ""
    return str(found[1]) if found and found[1] else ""


def pair(ctl_dir: Path, token: str, host: str, *, timeout: float = DEFAULT_TIMEOUT, poll: float = POLL_SECONDS,
         now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
         store: Callable[[str, str], None] = _store_device_key,
         read_back: Optional[Callable[[str], str]] = None) -> str:
    """云端用用户打的令牌向桥要设备密钥，存进本用户凭据管理器并读回核对，写回执。回原因码（成功 ``ok``）。
    令牌校验字符不对时什么文件都不写（``checksum``）。结束时请求、回应两个文件都删掉并回读。"""
    ctl_dir = Path(ctl_dir)
    try:
        raw = parse_token(token)
        key = auth_key(raw, host)
    except Refused as exc:
        _note("cloud_refused", exc.code)
        return exc.code
    if read_back is None:
        read_back = load_device_key
    private_key = X25519PrivateKey.generate()
    cloud_pub, request_id = _pub(private_key), secrets.token_hex(16)
    expires = int(now()) + REQUEST_TTL
    head = {"v": VERSION, "id": request_id, "cloud_pub": cloud_pub, "expires": expires,
            "tag": _tag(key, _request_canon(request_id, host, cloud_pub, expires))}
    request_path, response_path = ctl_dir / REQUEST_FILE, ctl_dir / RESPONSE_FILE
    handoff.remove_confirmed(response_path)
    handoff.write_atomic(request_path, _encode(head))
    _note("cloud_request", OK)
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        while True:
            reply = handoff.take(response_path)
            if reply is not None:
                code = _open_and_store(ctl_dir, reply, raw, key, host, request_id, cloud_pub, private_key, store,
                                       read_back)
                _note("cloud_result", code)
                return code
            if time.monotonic() >= deadline or now() > expires:
                _note("cloud_result", TIMEOUT)
                return TIMEOUT
            sleep(poll)
    finally:
        handoff.sweep(ctl_dir, (REQUEST_FILE, RESPONSE_FILE))


def _open_and_store(ctl_dir, reply, raw, key, host, request_id, cloud_pub, private_key, store, read_back) -> str:
    ok = False
    try:
        head = _decode(reply)
        if _hex_id(head.get("id")) != request_id:
            raise Refused(REPLAYED)
        bridge_pub, nonce, ct = head.get("bridge_pub"), head.get("nonce"), head.get("ct")
        if not all(isinstance(item, str) for item in (bridge_pub, nonce, ct)):
            raise Refused(MALFORMED)
        aad = _response_canon(request_id, host, cloud_pub, bridge_pub)
        _check_tag(key, aad, head.get("tag"))
        peer = X25519PublicKey.from_public_bytes(_unb64(bridge_pub, _PUB_BYTES))
        aes = _aes_key(private_key.exchange(peer), raw, host, request_id, cloud_pub, bridge_pub)
        try:
            plain = AESGCM(aes).decrypt(_unb64(nonce, _NONCE_BYTES), _unb64(ct), aad)
        except InvalidTag:
            raise Refused(TAMPERED) from None
        try:
            device_key = plain.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused(MALFORMED) from None
        finally:
            plain = b""
        if len(device_key) < 16:
            raise Refused(MALFORMED)
        try:
            store(host, device_key)
            ok = read_back(host) == device_key
        except Exception as exc:  # noqa: BLE001 - 写凭据管理器失败
            log.warning("云端设备配对 · 事件=store_failed 原因=%s", type(exc).__name__)
            ok = False
        finally:
            device_key = ""
        code = OK if ok else STORE_FAILED
    except Refused as exc:
        code = exc.code
        if code == REPLAYED:
            return code   # 不是本次的回应：不回执
    ack = {"v": VERSION, "id": request_id, "ok": ok, "tag": _tag(key, _ack_canon(request_id, host, ok))}
    handoff.write_atomic(Path(ctl_dir) / ACK_FILE, _encode(ack))
    return code


__all__ = ["ACK_FILE", "ALPHABET", "ANSWERED", "BridgePairing", "CHECKSUM", "CODES", "DEVICE_PREFIX", "EXPIRED", "FAILED",
           "FILES", "IDLE", "MALFORMED", "NOT_STORED", "NO_DEVICE_KEY", "NO_TOKEN", "OK", "PAIRED", "REPLAYED", "REQUEST_FILE",
           "RESPONSE_FILE", "Refused", "STORE_FAILED", "TAMPERED", "TIMEOUT", "TOKEN_TTL", "Ticket", "WAITING",
           "auth_key", "device_target", "format_token", "load_device_key", "new_token", "pair", "parse_token"]
