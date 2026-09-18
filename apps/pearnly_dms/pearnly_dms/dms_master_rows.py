# -*- coding: utf-8 -*-
"""DMS 下拉主档行的解析与**单次登录内**的备忘。

关键区分（老产品踩过、这里保留）：**取数失败**（返回 `None`）和**主档是空的**
（返回 `[]`）是两件事。前者不能当成"这个人不在名册里"——
半份名册比取不到更危险，因为"查无此人"会变成瞎话。

照搬老产品 `services/erp/mrerp_dms_master_rows.py`。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def memo(client: Any) -> Dict[tuple, Any]:
    """这个客户端在本轮登录内的主档备忘（主档在一次登录期间不会变）。"""
    return client.__dict__.setdefault("_bshsd_memo", {})


def parse_rows(elemname: str, text: str) -> Optional[List[List[Any]]]:
    """解析 bshsd 的 JSON，并保住"空列表 vs 取数失败"这个区别。"""
    if not text.strip():
        return []
    try:
        rows = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        rows = None
    if not isinstance(rows, list):
        logger.warning("[dms] bshsd %s：返回体不是 JSON 数组，按取数失败处理", elemname)
        return None
    return rows
