"""手机 / 电脑浏览器的网页推送（ADR-019 送达通道 2）：报文加密、VAPID 签名与订阅校验，纯函数。

只用标准库和 cryptography（Frappe 自己的密码加密也靠它，随基础镜像带着），不依赖 Frappe、网络或推送服务的 SDK；
存取密钥与订阅、真正发出由 `assistant.push` 负责。

- 加密按 RFC 8291（`aes128gcm`，RFC 8188 的单条记录）：每次随机一把发送方临时密钥和 16 字节盐，
  与浏览器订阅里的 `p256dh` 做 ECDH，再用订阅的 `auth` 派生内容密钥与随机数。
- 签名按 RFC 8292（VAPID，ES256）：`Authorization: vapid t=<JWT>, k=<公钥>`，`aud` 只取推送服务的协议与主机。
- 订阅只认几家浏览器推送服务的地址（防止借订阅接口让服务器去访问内网或任意地址），必须 https、默认端口、不带账号。
- 报文是紧凑 JSON `{title, body, url, tag}`，`url` 只能是对话页；超过上限时截短正文。
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import struct
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CURVE = ec.SECP256R1()
RECORD_SIZE = 4096
SALT_SIZE = 16
# 浏览器推送服务的主机：完全相同，或以这些后缀结尾（苹果、微软、Mozilla 按地区分主机）。
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
PUSH_HOST_SUFFIXES = (".push.apple.com", ".notify.windows.com", ".push.services.mozilla.com")
ENDPOINT_MAX = 1024
# 明文上限：一条记录 4096 字节，减去加密头、结束分隔与校验码后约 3993；留余量，泰文每字 3 字节。
PAYLOAD_MAX = 3800
TITLE_MAX = 140
TAG_MAX = 140
# 签名有效期：苹果要求不超过一天，取 1 小时。
VAPID_LIFETIME = 3600

HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
UNSAFE = re.compile(r"[\s\x00-\x1f\x7f\\]")
B64 = re.compile(r"^[A-Za-z0-9_\-+/]*={0,2}$")
# 点通知只打开对话页（深链接见 `contracts.deep_link`）。
ASSISTANT_PATH = re.compile(r"^/assistant(?:[/?#]|$)")


class InvalidSubscription(ValueError):
    pass


def b64e(data: bytes) -> str:
    """base64url，不带填充（RFC 8291 / 8292 与浏览器 `PushSubscription.toJSON()` 的写法）。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64d(text) -> bytes:
    """收 base64url 或标准 base64，带不带填充都行；不是 base64 抛 ValueError。"""
    text = str(text or "").strip()
    if not B64.match(text):
        raise ValueError("not base64")
    text = text.rstrip("=").replace("+", "-").replace("/", "_")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise ValueError("not base64") from None


# ---- 密钥 ----

def _private(raw: bytes):
    if len(raw) != 32:
        raise ValueError("a P-256 private key is 32 bytes")
    return ec.derive_private_key(int.from_bytes(raw, "big"), CURVE)


def _point(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _public(raw: bytes):
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("a P-256 public key is a 65-byte uncompressed point")
    return ec.EllipticCurvePublicKey.from_encoded_point(CURVE, raw)


def new_keys() -> tuple[str, str]:
    """一对新的 VAPID 密钥：(私钥 32 字节标量, 公钥 65 字节非压缩点)，都是 base64url。"""
    key = ec.generate_private_key(CURVE)
    return b64e(key.private_numbers().private_value.to_bytes(32, "big")), b64e(_point(key))


def public_of(private_b64: str) -> str:
    return b64e(_point(_private(b64d(private_b64))))


def matches(private_b64, public_b64) -> bool:
    """存着的私钥与公钥是不是一对（私钥解不开或格式不对都算不是）。"""
    try:
        return hmac.compare_digest(public_of(private_b64), str(public_b64 or ""))
    except ValueError:
        return False


# ---- RFC 8291 加密 ----

def _extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _expand(prk: bytes, info: bytes, size: int) -> bytes:
    # 输出不超过 32 字节，HKDF-Expand 只需一块
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:size]


def derive(ua_public: bytes, auth: bytes, sender_private: bytes, salt: bytes) -> dict:
    """RFC 8291 §3.3–3.4 与 RFC 8188 §2.2 的各项中间值（测试按 RFC 附录 A 逐项核对）。"""
    sender = _private(sender_private)
    as_public = _point(sender)
    ecdh = sender.exchange(ec.ECDH(), _public(ua_public))
    key_info = b"WebPush: info\x00" + ua_public + as_public
    prk_key = _extract(auth, ecdh)
    ikm = _expand(prk_key, key_info, 32)
    prk = _extract(salt, ikm)
    return {"as_public": as_public, "ecdh": ecdh, "key_info": key_info, "prk_key": prk_key, "ikm": ikm, "prk": prk,
            "cek": _expand(prk, b"Content-Encoding: aes128gcm\x00", 16),
            "nonce": _expand(prk, b"Content-Encoding: nonce\x00", 12)}


def encrypt(plaintext: bytes, ua_public: bytes, auth: bytes, *, salt=None, sender_private=None) -> bytes:
    """加密成推送请求的正文：头（盐 + 记录长度 + 发送方公钥）+ 一条以 0x02 结尾的记录。盐与发送方密钥默认每次随机。"""
    if len(auth) != 16:
        raise ValueError("auth secret is 16 bytes")
    salt = os.urandom(SALT_SIZE) if salt is None else salt
    if sender_private is None:
        sender_private = ec.generate_private_key(CURVE).private_numbers().private_value.to_bytes(32, "big")
    if len(salt) != SALT_SIZE:
        raise ValueError("salt is 16 bytes")
    record = plaintext + b"\x02"
    if len(record) + 16 > RECORD_SIZE:
        raise ValueError("push payload too large")
    keys = derive(ua_public, auth, sender_private, salt)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(keys["as_public"])) + keys["as_public"]
    return header + AESGCM(keys["cek"]).encrypt(keys["nonce"], record, None)


# ---- RFC 8292 VAPID ----

def signing_input(claims: dict) -> str:
    """JWT 的待签部分；头固定，声明按传入顺序紧凑输出（与 RFC 8292 示例逐字节相同）。"""
    header = json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":"))
    return b64e(header.encode()) + "." + b64e(json.dumps(claims, separators=(",", ":")).encode())


def audience(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    port = f":{parts.port}" if parts.port and parts.port != 443 else ""
    return f"{parts.scheme}://{parts.hostname}{port}"


def vapid_authorization(endpoint, private_b64, public_b64, subject, now, lifetime=VAPID_LIFETIME) -> str:
    """推送请求的 `Authorization` 头：`vapid t=<JWT>, k=<公钥>`。ES256 签名转成 r‖s 共 64 字节。"""
    unsigned = signing_input({"aud": audience(endpoint), "exp": int(now) + int(lifetime), "sub": subject})
    r, s = decode_dss_signature(_private(b64d(private_b64)).sign(unsigned.encode(), ec.ECDSA(hashes.SHA256())))
    return f"vapid t={unsigned}.{b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}, k={public_b64}"


def verify(token: str, public_b64: str) -> bool:
    """用公钥验 JWT 的签名（测试用来核对自己签的与 RFC 示例）。"""
    try:
        unsigned, _, signature = str(token).rpartition(".")
        raw = b64d(signature)
        if not unsigned or len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        _public(b64d(public_b64)).verify(der, unsigned.encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError):
        return False


# ---- 订阅与报文 ----

def endpoint_key(endpoint) -> str:
    """订阅的唯一键：推送地址常超过 140 字（Data 字段放不下、Small Text 建不了唯一索引），用 sha256。"""
    return hashlib.sha256(str(endpoint or "").encode("utf-8")).hexdigest()


def push_host(endpoint) -> str:
    """校验推送地址，回它的主机；不是白名单里的推送服务抛 InvalidSubscription。"""
    text = str(endpoint or "")
    if not text or len(text) > ENDPOINT_MAX or UNSAFE.search(text):
        raise InvalidSubscription("invalid push endpoint")
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError:
        raise InvalidSubscription("invalid push endpoint") from None
    host = parts.hostname or ""
    if (parts.scheme != "https" or port not in (None, 443) or parts.netloc.lower() not in (host, host + ":443")
            or not HOST.match(host)):
        raise InvalidSubscription("invalid push endpoint")
    if host not in PUSH_HOSTS and not host.endswith(PUSH_HOST_SUFFIXES):
        raise InvalidSubscription("push service not allowed")
    return host


def subscription(endpoint, p256dh, auth) -> dict:
    """浏览器交来的订阅（`PushSubscription.toJSON()` 的 endpoint 与 keys）→ 规范化后的
    `{endpoint, p256dh, auth, key}`；不合格抛 InvalidSubscription。"""
    endpoint = str(endpoint or "").strip()
    push_host(endpoint)
    try:
        ua_public, secret = b64d(p256dh), b64d(auth)
        _public(ua_public)  # 65 字节、0x04 开头，且是曲线上的点
    except ValueError:
        raise InvalidSubscription("invalid push keys") from None
    if len(secret) != 16:
        raise InvalidSubscription("invalid push keys")
    return {"endpoint": endpoint, "p256dh": b64e(ua_public), "auth": b64e(secret), "key": endpoint_key(endpoint)}


def _compact(fields: dict) -> bytes:
    return json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def push_payload(title, body, url, tag) -> bytes:
    """后台脚本收到的报文；超过 PAYLOAD_MAX 字节时截短正文（末尾加省略号）。"""
    url = str(url or "")
    if not ASSISTANT_PATH.match(url):
        raise ValueError("push url must open the assistant page")
    fields = {"title": str(title or "")[:TITLE_MAX], "body": str(body or ""), "url": url, "tag": str(tag or "")[:TAG_MAX]}
    data = _compact(fields)
    if len(data) <= PAYLOAD_MAX:
        return data
    text, low, high = fields["body"], 0, len(fields["body"]) - 1
    while low < high:  # 能放下的最长前缀
        middle = (low + high + 1) // 2
        if len(_compact({**fields, "body": text[:middle] + "…"})) <= PAYLOAD_MAX:
            low = middle
        else:
            high = middle - 1
    data = _compact({**fields, "body": text[:low] + "…"})
    if len(data) > PAYLOAD_MAX:
        raise ValueError("push payload too large")
    return data
