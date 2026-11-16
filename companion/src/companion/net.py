"""CC 桌面版所有 HTTP 客户端的唯一出口。

各模块不再自己 ``httpx.Client(...)``，一律 ``net.client(...)``：参数原样交给 ``httpx.Client``，本模块不加、不改任何
默认值（timeout、verify、follow_redirects、headers、base_url、limits、http2 等全由调用方给）。

留一个进程级钩子 ``set_transport_factory``，只给以后的云端模式用（请求改走自定义 transport，例如文件隧道）：
- 钩子为空（默认，线上所有现有用户）：完全不碰 transport，等同直接 ``httpx.Client(**kwargs)``，零行为变化；
- 钩子非空且调用方没传 transport：用 ``factory(kwargs)`` 的返回值作 transport；调用方显式给的 transport 优先。
目前没有任何地方设置这个钩子。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

import httpx

TransportFactory = Callable[[Dict[str, Any]], httpx.BaseTransport]

_lock = threading.Lock()
_factory: Optional[TransportFactory] = None


def set_transport_factory(factory: Optional[TransportFactory]) -> None:
    """设置（或用 None 清除）进程级 transport 工厂。"""
    global _factory
    with _lock:
        _factory = factory


def client(**kwargs: Any) -> httpx.Client:
    """建一个 ``httpx.Client``；没设钩子时与 ``httpx.Client(**kwargs)`` 完全相同。"""
    with _lock:
        factory = _factory
    if factory is not None and "transport" not in kwargs:
        kwargs["transport"] = factory(dict(kwargs))
    return httpx.Client(**kwargs)
