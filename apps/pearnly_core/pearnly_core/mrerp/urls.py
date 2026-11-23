"""MR.ERP 系统地址的存储语法校验（纯函数）：只许 http / https 公网主机名或公网 IP，不带账号、片段，不许本机 / 元数据名。

出站时另由 `session.public_host` 解析 DNS 核对只落公网。由 Cowork `connections.model.validate_url` 原样搬来（2026-11-02），
那里改为调用这里并照旧抛它自己的 ``connection.url_invalid``。
"""
import ipaddress
from urllib.parse import urlsplit

INVALID = "connection.url_invalid"


def validate_url(value):
    """Storage syntax guard. Outbound adapters must separately pin public DNS."""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        raise ValueError(INVALID) from None
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password
            or parsed.fragment or host in {"localhost", "metadata", "metadata.google.internal"}
            or host.endswith((".localhost", ".local", ".internal")) or "." not in host):
        raise ValueError(INVALID)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ValueError(INVALID)
    return value


def system_key(value) -> str:
    """同一个 MR.ERP 系统的比较键（11-23：连接按系统地址复用，双击、重试、重开弹窗都不新建）：协议与主机名小写、主机去末尾的点、
    路径去末尾的斜杠，默认端口不写；读不出来的按原文去空白比。纯函数，两个产品的连接都用它判「同一系统地址」。"""
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return text
    if not parsed.scheme or not host:
        return text
    scheme = parsed.scheme.lower()
    shown = "" if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f":{port}"
    return f"{scheme}://{host}{shown}{parsed.path.rstrip('/')}"
