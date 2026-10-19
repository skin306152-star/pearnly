# -*- coding: utf-8 -*-
"""文件夹自动录入的四个机器接口（与心跳 / 领任务同一份配对码身份）和签名直传。

  POST /api/method/pearnly_cowork.folder_intake.machine.sync           上报本机情况，取规则与在途状态
  POST /api/method/pearnly_cowork.folder_intake.machine.begin          登记一份文件，拿上传方式
  POST /api/method/pearnly_cowork.folder_intake.machine.finish         上传完成（经接口上传的带上内容）
  POST /api/method/pearnly_cowork.folder_intake.machine.workbook_file  取一张发票的结果表格

业务上的「不收」回 200 + ``{ok: false, code}``；401 是配对失效（密钥被吊销 / 电脑被移除，托盘提示重新连接），
403 / 404 是总闸关着、连接停用或旧服务端没有这些接口。
签名直传用**单独的** httpx 客户端：不带配对密钥、不跟随跳转，只发云端给的那几个头。
"""

from __future__ import annotations

import base64
from typing import Optional
from urllib.parse import urlsplit

import httpx

from . import http_tls
from .logger import get_logger
from .queue_client import unwrap

log = get_logger(__name__)

_METHOD = "/api/method/pearnly_cowork.folder_intake.machine."
SYNC = _METHOD + "sync"
BEGIN = _METHOD + "begin"
FINISH = _METHOD + "finish"
WORKBOOK = _METHOD + "workbook_file"


class Refused(Exception):
    """服务端不认这些接口（旧版本）、总闸关着或配对失效：本轮不做。``status`` 是 HTTP 状态码；``rejected`` = 配对失效（401）。"""

    def __init__(self, message: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.status = int(status or 0)

    @property
    def rejected(self) -> bool:
        return self.status == 401


class FolderClient:
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
            # 旧服务端没有这些接口、总闸关着、配对失效：本轮不做，下一轮再看（不影响写账套那条线）。
            raise Refused(f"folder intake refused: {response.status_code}", response.status_code)
        response.raise_for_status()
        return unwrap(response)

    def sync(self, body: dict) -> dict:
        return self._post(SYNC, body)

    def begin(self, body: dict) -> dict:
        return self._post(BEGIN, body)

    def finish(self, entry: str, content: Optional[bytes] = None) -> dict:
        body = {"entry": entry}
        if content is not None:
            body["content"] = base64.b64encode(content).decode("ascii")
        return self._post(FINISH, body, timeout=300.0)

    def workbook(self, entry: str, history: str) -> dict:
        return self._post(WORKBOOK, {"entry": entry, "history": history}, timeout=120.0)


def put_direct(url: str, headers: dict, path, backend_url: str, timeout: float = 600.0) -> None:
    """把原件 PUT 到云端签发的原件桶地址。不带配对密钥（独立客户端），也不发往租户站点本身。"""
    target = (urlsplit(url).netloc or "").lower()
    if not target or target == (urlsplit(backend_url).netloc or "").lower():
        raise ValueError("refusing to upload to the tenant site")
    clean = {str(k): str(v) for k, v in (headers or {}).items() if str(k).lower() != "authorization"}
    with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(timeout, connect=15.0),
                      verify=http_tls.context()) as client, \
            open(path, "rb") as handle:
        response = client.put(url, content=handle.read(), headers=clean)
    if response.status_code == 412:
        return  # 同一个对象已经传过（不覆盖）：当作已上传
    response.raise_for_status()
