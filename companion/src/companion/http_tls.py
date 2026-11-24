# -*- coding: utf-8 -*-
"""CC 桌面版所有 HTTPS 客户端共用一份 TLS 设置（2026-10-18 轻量化）。

httpx 每建一个客户端就新建一份 SSL 上下文、把 certifi 的一百多张根证书重新解析一遍；Windows 上 Python 3.11 自带的
OpenSSL 3.0 做这一步很慢。办公室测试机（i5-1135G7）10-18 实测：按需请求线程每 15 秒新建一个客户端，每次约 250 ms CPU；
文件夹线程每分钟一次，同样约 250 ms——什么都不做时后台就一直占着约 2% 的一个核（老电脑更多）。

所以整个进程只建一份（``httpx.create_ssl_context()``，与 httpx 默认完全相同：certifi 根证书、校验证书与主机名，照样认
``SSL_CERT_FILE`` / ``SSL_CERT_DIR``），各客户端经 ``verify=context()`` 共用。SSLContext 可以跨线程共用。

注意：httpcore 每建一条连接都会对这份上下文调一次 ``set_alpn_protocols``。现在所有客户端都只用 HTTP/1.1，写进去的值一样；
以后哪个客户端要开 ``http2=True``，给它单独建一份，别和这份共用（否则不同线程会互相改协议列表）。
"""

from __future__ import annotations

import ssl
import threading
from typing import Optional

_lock = threading.Lock()
_context: Optional[ssl.SSLContext] = None


def context() -> ssl.SSLContext:
    global _context
    with _lock:
        if _context is None:
            import httpx

            _context = httpx.create_ssl_context()
        return _context


# 空闲连接留多久（1.12.25）。httpx 默认只留 5 秒：领单 3 秒一次赶得上，按需请求（空闲时几十秒一次）、文件夹同步每次都
# 重新建 TCP + TLS（11-24 本机实测：一台连两个站点的电脑每分钟 8.6 次新握手，办公室同一出口合起来约每分钟 28 次）。
# 60 秒：比服务端 nginx 默认的 65 秒短，服务端先关的空闲连接这边不会再拿来用。
KEEPALIVE_SECONDS = 60.0


def limits():
    """出站客户端共用的连接池设置（与 httpx 默认相同，只把空闲连接留到 :data:`KEEPALIVE_SECONDS`）。更新下载是一次性的，不用。"""
    import httpx

    return httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=KEEPALIVE_SECONDS)
