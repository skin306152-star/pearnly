# -*- coding: utf-8 -*-
"""同一个已登录会话里并行发只读表单请求 —— 首次读全档的客户详情用（2026-09-27）。

## 为什么

大客户量租户的首次读全档几乎全耗在"逐个打开客户表单"上：ris 的 15,155 个客户每个约 0.24 秒，
客户组整组 61 分钟（2026-09-27 线上夜间日志：156 步、每步 23.7 秒）。DMS-TEST 实测同一会话
（同一个 PHPSESSID）并发读表单都正常、没有被踢，2 / 4 / 8 个并发分别约快 2.3 / 2.9 / 3.1 倍；
DMS 那边单个请求越慢（ris 比测试公司慢得多），并发的收益越大。

## 边界

* 只读：只接受 `*/form.php` 的 `status=n|e`（预算层 `snapshot_reader._Budget.before` 同样拦）。
* 用的是客户端此刻那条会话的 cookie（读档账号，或当时借到的管理员会话），不另外登录、不换账号。
* 并发数 `pearnly_dms_read_concurrency`（默认 4，上限 8，1 = 关闭并行、逐个读）：对方是老丈人
  一台服务器上的多家公司共用系统，4 个并发相当于 4 个人同时翻客户资料。
* 断连重试一次；非 200 或仍失败报 `ERR_DMS_TECHNICAL`（这一步作废、检查点保留，下一步接着读）。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import logging
import threading
from urllib.parse import urlsplit

from pearnly_dms.contracts.client_base import DmsClientError

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = 4
MAX_WORKERS = 8
TIMEOUT_SECONDS = 30


def workers() -> int:
    import frappe  # 懒引：离线的读取器测试（纯 Python，不带 frappe / requests）也要能导入本模块

    try:
        value = int(frappe.conf.get("pearnly_dms_read_concurrency", DEFAULT_WORKERS))
    except (TypeError, ValueError):
        return DEFAULT_WORKERS
    return max(1, min(MAX_WORKERS, value))


class ParallelForms:
    """`reader(path, [data, ...]) -> [body, ...]`，按输入顺序返回。"""

    def __init__(self, client, count: int):
        host = urlsplit(client.base_url).hostname or ""
        cookies = client.transport._rc.storage_state().get("cookies") or []
        self._cookies = [c for c in cookies if host.endswith(str(c.get("domain") or "").lstrip("."))]
        if not self._cookies:
            raise DmsClientError("no DMS session cookie to share", "ERR_DMS_TECHNICAL")
        self._client = client
        self._local = threading.local()
        self._pool = ThreadPoolExecutor(count, thread_name_prefix="dms-read")

    def _session(self):
        session = getattr(self._local, "session", None)
        if session is None:
            import requests

            session = self._local.session = requests.Session()
            for cookie in self._cookies:
                session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"),
                                    path=cookie.get("path") or "/")
        return session

    def _post(self, url, data):
        import requests

        for attempt in (1, 2):
            try:
                response = self._session().post(url, data=data, timeout=TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                if attempt == 2:
                    raise DmsClientError(f"{type(exc).__name__}: {exc}", "ERR_DMS_TECHNICAL") from exc
                logger.warning("[dms] parallel read disconnected, retrying once: %s", url)
                continue
            if response.status_code != 200:
                raise DmsClientError(f"{url} http={response.status_code}", "ERR_DMS_TECHNICAL")
            return response.content.decode("utf-8", "replace")

    def __call__(self, path, datas):
        if not str(path).endswith("/form.php") or any(d.get("status") not in {"n", "e"} for d in datas):
            raise DmsClientError("parallel reads are read-only", "ERR_DMS_SNAPSHOT_INCOMPLETE")
        url = self._client._url(path)
        return list(self._pool.map(lambda data: self._post(url, data), datas))

    def close(self):
        self._pool.shutdown(wait=True)


@contextmanager
def attached(client):
    """在这一块里让 `snapshot_reader` 并行读客户详情；关着或建不起来就照旧逐个读。"""
    count = workers()
    reader = None
    if count > 1:
        try:
            reader = ParallelForms(client, count)
        except Exception:
            logger.warning("[dms] parallel reads unavailable; reading one by one", exc_info=True)
    if reader is None:
        yield
        return
    client._parallel_post_text = reader
    try:
        yield
    finally:
        client.__dict__.pop("_parallel_post_text", None)
        reader.close()
