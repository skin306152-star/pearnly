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

import logging
from typing import Any, Dict, Optional

from pearnly_dms.contracts.client_base import DmsClientError

logger = logging.getLogger(__name__)

# **读**路径的判据 —— DMS 没有 GET 形式的读，读也是 POST 到列表/搜索（`component/`）
# 和表单回显（`/form.php`）。只对这些路径做"断连重试一次"。
# 写路径（`cus/new.php`、`cus/edit.php`、`drfcbc/*`、附件上传）**绝不重试**：
# 连接断了不代表对方没落库，重试会把一张单变成两张。
_READ_PATH_MARKERS = ("component/", "/form.php")


def _is_read_path(url: str) -> bool:
    return any(marker in str(url or "") for marker in _READ_PATH_MARKERS)


def _is_transport_error(exc: BaseException) -> bool:
    """Playwright 侧的传输层异常（`socket hang up` / 超时 / 连接被重置）。

    只认这一个家族：别的异常（参数写错、解析炸了）照原样抛出去，不会被包装成
    "连接超时，请重试" —— 那会把排障线索变成一句误导。
    """
    try:
        from playwright._impl._errors import Error as PlaywrightError
    except Exception:  # pragma: no cover - playwright 是硬依赖，这里只做防御
        return False
    return isinstance(exc, PlaywrightError)


def _retry_read_once(call, url: str):
    """读请求断连 → 重试一次；仍失败才上报 `ERR_DMS_TECHNICAL`。

    为什么要这一层：2026-09-21 线上那次 `socket hang up` 落在查客户那条搜索上，
    异常一路冒到最外层被兜成 `ERR_UNEXPECTED`（用户看到"服务器内部错误，请联系管理员"）。
    代码里本来就有更准的话术：`ERR_DMS_TECHNICAL` =「DMS 连接超时 — 请重试」。
    """
    try:
        return call()
    except Exception as exc:
        if not _is_transport_error(exc) or not _is_read_path(url):
            raise
        logger.warning("[dms] 读请求断连，重试一次：%s（%s: %s）", url, type(exc).__name__, exc)
    try:
        return call()
    except Exception as exc:  # noqa: BLE001
        if not _is_transport_error(exc):
            raise
        raise DmsClientError(f"{type(exc).__name__}: {exc}", "ERR_DMS_TECHNICAL") from exc


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
        return _retry_read_once(
            lambda: self._wrap(self._rc.get(url, timeout=timeout_ms or 30000)), url
        )

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
            send = lambda: self._wrap(self._rc.post(url, multipart=multipart, timeout=timeout))
        else:
            form = {k: str(v) for k, v in (data or {}).items()}
            send = lambda: self._wrap(self._rc.post(url, form=form, timeout=timeout))
        # 上传（files）必然是写路径 —— `_is_read_path` 不会放它重试。
        return _retry_read_once(send, url)
