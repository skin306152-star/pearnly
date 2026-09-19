# -*- coding: utf-8 -*-
"""把"已登录的浏览器"包成 HTTP 管道，交给业务客户端用。

**为什么要这样绕一圈**：DMS 没有开放 API，它是一个 PHP 表单站点。
老产品的做法（也是铁律）是：浏览器**真登录**一次，然后业务代码用
浏览器自己的 request API 去发那些表单请求 —— 因为请求会自动带上浏览器
真实建立的会话 cookie，等于"在已登录的页面上操作"，而不是拿账号密码去抓包重放。

所以这一层只做一件事：把 `context.request` 的返回包成业务客户端认的形状
（`status_code` / `text` / `content`）。业务代码因此**完全不认识 Playwright**，
单测时塞个假 transport 就能跑。

照搬老产品 `services/erp/mrerp_dms_adapter.py` 的 `PlaywrightTransport` / `_Resp`。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class Resp:
    """业务客户端只需要这三个字段（老产品 `_Resp` 的同名同形）。"""

    __slots__ = ("status_code", "text", "content")

    def __init__(self, status_code: int, text: str, content: bytes):
        self.status_code = status_code
        self.text = text
        self.content = content


class PlaywrightTransport:
    """用已登录的 Playwright context 发请求 —— 每个请求都带真实会话 cookie。"""

    def __init__(self, request_context: Any):
        self._rc = request_context

    @staticmethod
    def _wrap(r) -> Resp:
        # 从**原始字节**宽松解码：二进制响应（比如 xlsx 模板）会让
        # Playwright 的 r.text() 抛 UnicodeDecodeError，整条链路就断了。
        body = r.body()
        return Resp(r.status, body.decode("utf-8", "replace"), body)

    def get(self, url: str, timeout_ms: Optional[int] = None) -> Resp:
        return self._wrap(self._rc.get(url, timeout=timeout_ms or 30000))

    def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
    ) -> Resp:
        timeout = timeout_ms or 30000
        if files:
            multipart: Dict[str, Any] = {}
            for k, v in (data or {}).items():
                multipart[k] = str(v)
            for field, spec in files.items():
                filename, buf, content_type = spec
                multipart[field] = {"name": filename, "mimeType": content_type, "buffer": buf}
            r = self._rc.post(url, multipart=multipart, timeout=timeout)
        else:
            form = {k: str(v) for k, v in (data or {}).items()}
            r = self._rc.post(url, form=form, timeout=timeout)
        return self._wrap(r)
