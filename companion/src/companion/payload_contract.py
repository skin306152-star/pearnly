# -*- coding: utf-8 -*-
"""载荷版本协商(与云端后端严格对齐,字段名一字不差)。

后端将来改 payload 结构会带 `payload_version` 递增;本机声明的支持集
`SUPPORTED_PAYLOAD_VERSIONS` 落后于云端新版号时,任务必须诚实失败
(见 worker._handle_dbf_task),绝不按旧逻辑瞎写出错账 —— 静默错账是最大罪。

`payload_version` 缺失视同 1(兼容后端尚未升级期间下发的旧载荷)。
"""

from __future__ import annotations

from typing import Optional

SUPPORTED_PAYLOAD_VERSIONS = frozenset({1})

ERR_PAYLOAD_VERSION = "ERR_PAYLOAD_VERSION"


def check_payload_version(payload: dict) -> Optional[str]:
    """payload_version 是否在本机支持集内。支持 → None;不支持 → 错误码字符串。"""
    got = (payload or {}).get("payload_version", 1)
    if got in SUPPORTED_PAYLOAD_VERSIONS:
        return None
    return ERR_PAYLOAD_VERSION
