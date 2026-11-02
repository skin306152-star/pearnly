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
