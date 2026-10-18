# -*- coding: utf-8 -*-
"""按需请求的两个机器接口（与心跳 / 领推送任务同一份配对码身份）：

  POST /api/method/pearnly_cowork.companion.request_api.next_request      领一件（没有回 request: null）
  POST /api/method/pearnly_cowork.companion.request_api.complete_request  交结果（分块）或失败原因

业务上的「不行」回 200 + ``{ok: false, code}``；401 / 403 / 404 表示旧服务端没有这些接口、总闸关着或配对失效。
"""

from __future__ import annotations

from typing import Optional

import httpx

from . import http_tls
from .queue_client import unwrap

_METHOD = "/api/method/pearnly_cowork.companion.request_api."
NEXT = _METHOD + "next_request"
COMPLETE = _METHOD + "complete_request"


class Refused(Exception):
    """服务端不认这些接口（旧版本）、总闸关着或配对失效：这一轮不做。"""


class RequestClient:
    def __init__(self, backend_url: str, bearer_token: str, timeout: float = 30.0) -> None:
        self.backend_url = backend_url
        self._client = httpx.Client(
            base_url=backend_url,
            headers={"Authorization": f"Bearer {bearer_token}"},
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            verify=http_tls.context(),
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, body: dict, timeout: Optional[float] = None) -> dict:
        response = self._client.post(path, json=body, timeout=timeout or httpx.USE_CLIENT_DEFAULT)
        if response.status_code in (401, 403, 404):
            raise Refused(f"on-demand request refused: {response.status_code}")
        response.raise_for_status()
        return unwrap(response)

    def next(self, body: dict) -> dict:
        return self._post(NEXT, body)

    def complete(self, body: dict) -> dict:
        return self._post(COMPLETE, body, timeout=120.0)
