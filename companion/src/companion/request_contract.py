# -*- coding: utf-8 -*-
"""按需请求的约定常量：与服务端 ``apps/pearnly_core/pearnly_core/cc_desktop/wire/request_contracts.py`` 同名同值
（``tests/test_companion_contract.py`` 逐值比对）。口径与加一种请求的做法见服务端那份文件头与 ``request_worker``。"""

from __future__ import annotations

from dataclasses import dataclass, field

KINDS = ("express_read", "folder_list", "folder_fetch", "account_history", "express_masters", "express_marks",
         "express_doc_check", "express_ar_open")
CHUNK_BYTES = 4 * 1024 * 1024
MAX_BYTES = {"express_read": 50 * 1024 * 1024, "folder_list": 2 * 1024 * 1024, "folder_fetch": 40 * 1024 * 1024,
             "account_history": 5 * 1024 * 1024, "express_masters": 16 * 1024 * 1024, "express_marks": 64 * 1024,
             "express_doc_check": 256 * 1024, "express_ar_open": 2 * 1024 * 1024}
LEASE_SECONDS = {"express_read": 900, "folder_list": 180, "folder_fetch": 600, "account_history": 900,
                 "express_masters": 900, "express_marks": 120, "express_doc_check": 120, "express_ar_open": 180}
# 空闲时多久来领一次（服务端 SEEN_SECONDS 之内来过才算在线，Cowork 还据此续代录单的租约，所以不能拉长到 SEEN_SECONDS 的一半以上）。
# 1.12.25 起 15 → 30 秒：有新请求时领单回复带 requests_waiting 马上叫醒（``request_wake``，领单 3 秒一次），空问只是兜底与报到；
# 写账线在做单时叫醒到不了，回到 15 秒（``request_wake.BUSY_SECONDS``）。问失败了按退避时刻重问、不等满一次空闲：
# 30（空闲）+ 30（一次读超时）+ 15（首次退避）= 75 秒，仍在 SEEN_SECONDS（90）之内。
POLL_SECONDS = 30


@dataclass(frozen=True)
class Result:
    """一件请求的结果：交回服务端的字节（格式由这一种请求自己定，JSON 一律 gzip 后交）与一个小的说明对象。"""

    content: bytes
    meta: dict = field(default_factory=dict)


class RequestFailed(Exception):
    """这件做不了：``code`` 原样交回服务端（如 root_missing、not_found、too_large），工具据此告诉用户。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
