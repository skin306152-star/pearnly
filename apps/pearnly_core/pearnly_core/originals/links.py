"""甲骨文原件存储的签名地址（ADR-023 / ADR-024，纯计算：不依赖 Frappe、无 IO）：生产签、甲骨文验，用同一把密钥（HMAC-SHA256）。

浏览器拿着签名地址直接找甲骨文上传、取预览、下载，不经过生产服务器；生产只做权限判定、记访问日志、签地址。
签名把方法、对象名、过期时间和这次请求的关键参数（上传的字节数；下载的 sha256 与文件名；预览的 sha256、类型、页码）
都签进去，改任何一项都对不上。地址本身就是凭据（与谷歌存储的签名地址同一个思路），所以有效期很短。
"""
import hashlib
import hmac
import re
import time
from urllib.parse import quote, urlencode

UPLOAD_SECONDS = 15 * 60
DOWNLOAD_SECONDS = 5 * 60
# 对象名与谷歌存储时一样：``<站点>/originals/<原件 id><扩展名>``；原件 id = 产品前缀（Cowork ``CWS-``、ERP ``ERO-``）
# + 48 位十六进制，由产品按工作空间 / 公司 + 上传人 + 幂等键算出（ADR-024）。
OBJECT = re.compile(r"(?P<site>[a-z0-9.-]+)/originals/(?P<source>(?:CWS|ERO)-[0-9a-f]{48})(?P<suffix>\.[a-z0-9]{2,5})")
SCHEME = "oracle://"


def object_name(site, source_name, suffix):
    return f"{site}/originals/{source_name}{suffix}"


def valid_object(name):
    return isinstance(name, str) and bool(OBJECT.fullmatch(name))


def preview_prefix(name):
    """这份原件的预览目录（相对存储根）：``<站点>/previews/<原件名>/``。"""
    found = OBJECT.fullmatch(name)
    if not found:
        raise ValueError("bad object name")
    return f"{found['site']}/previews/{found['source']}/"


def url_of(name, sha256):
    """记在原件记录 ``storage_url`` 上：``oracle://对象名#sha256``（同一对象只写一次，内容由 sha256 钉住）。"""
    return f"{SCHEME}{name}#{sha256}"


def parse_url(value):
    """``oracle://对象名#sha256`` → ``(对象名, sha256)``；不是这种地址返回 ``None``。"""
    if not isinstance(value, str) or not value.startswith(SCHEME) or "#" not in value:
        return None
    name, _sep, sha256 = value[len(SCHEME):].rpartition("#")
    if not valid_object(name) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        return None
    return name, sha256


def signature(key, method, name, expires, *parts):
    message = "\n".join([method, name, str(int(expires)), *(str(part) for part in parts)])
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def verify(key, method, name, expires, given, *parts, now=None):
    """签名对、没过期才算数（常数时间比较）。"""
    if not key or not valid_object(name) or not str(expires).isdigit() or int(expires) < int(now or time.time()):
        return False
    return hmac.compare_digest(signature(key, method, name, expires, *parts), str(given or ""))


def _link(base, path, name, query):
    return f"{base.rstrip('/')}/{path}/{quote(name, safe='/')}?{urlencode(query)}"


def upload_url(base, key, name, byte_size, *, seconds=UPLOAD_SECONDS, now=None):
    expires = int(now or time.time()) + seconds
    return _link(base, "o", name, {"e": expires, "n": int(byte_size),
                                   "s": signature(key, "PUT", name, expires, int(byte_size))})


def download_url(base, key, name, sha256, filename, *, seconds=DOWNLOAD_SECONDS, now=None):
    expires = int(now or time.time()) + seconds
    return _link(base, "o", name, {"e": expires, "h": sha256, "d": filename,
                                   "s": signature(key, "GET", name, expires, sha256, filename)})


def preview_url(base, key, name, sha256, kind, page, *, seconds=DOWNLOAD_SECONDS, now=None):
    expires = int(now or time.time()) + seconds
    return _link(base, "p", name, {"e": expires, "h": sha256, "k": kind, "n": int(page),
                                   "s": signature(key, "PREVIEW", name, expires, sha256, kind, int(page))})


__all__ = ["DOWNLOAD_SECONDS", "OBJECT", "SCHEME", "UPLOAD_SECONDS", "download_url", "object_name", "parse_url",
           "preview_prefix", "preview_url", "signature", "upload_url", "url_of", "valid_object", "verify"]
