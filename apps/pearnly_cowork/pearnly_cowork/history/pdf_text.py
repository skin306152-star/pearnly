"""PDF 文字层的纯判据（数字会计员第 3 期第 2 条「文字层 PDF 才能进 A / B」）：不连库、不依赖框架，生产（`history.text_layer`）
与离线回放（`scripts/da-replay.py`）同一条。"""
from __future__ import annotations

import re
from typing import Any

# 内容流里的隐形文字（文字绘制模式 3 Tr）：扫描件、手机扫描 App、复合机「可搜索 PDF」叠在整页图片上的识别文字层
_INVISIBLE_TEXT = re.compile(rb"(?<![\w.])3\s+Tr(?![\w])")


def invisible_text(content: Any) -> bool:
    """PDF 一页的内容流（bytes）里有隐形文字（``3 Tr``）：这一页是扫描图片上叠的识别文字层，不算文字层 PDF 的页。"""
    return isinstance(content, (bytes, bytearray)) and bool(_INVISIBLE_TEXT.search(bytes(content)))


def page_text(page: Any) -> str:
    """pypdf 的一页 → 它的文字层；带隐形文字的页给空串（按图片算）。读不出内容流时照常取文字。"""
    try:
        content = page.get_contents()
        raw = content.get_data() if content is not None else b""
    except Exception:  # noqa: BLE001 — 内容流读不出不判隐形文字
        raw = b""
    return "" if invisible_text(raw) else (page.extract_text() or "")


__all__ = ["invisible_text", "page_text"]
