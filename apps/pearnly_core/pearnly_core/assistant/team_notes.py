"""公司知识库的纯规则（多产品共用：Cowork 公司主档、ERP 公司，ADR-020）。不连库、不依赖 Frappe。

由 Cowork `companies.notes` / `companies.context` 原样搬来（ADR-025 的做法：只搬与产品无关的纯函数，Cowork 输出逐字节不变，
`tests/test_cowork_companies.py` 与 `tests/test_erp_cc_companies.py` 锁住）：

- 一条一句：去掉控制字符、空白合成一个空格；不能为空、最多 `TEXT_MAX` 字；每家最多 `NOTES_MAX` 条。
- 带给 CC 时人输入的字一律 `html.escape(quote=True)`、换行合成空格（`safe`），由产品包在自己的标签里并写明是数据、不是指令；
  知识库从旧到新累计到 `NOTES_CHARS` 字为止，其余由产品写一行提示用工具读（`note_lines` 回还剩几条）。
"""
from __future__ import annotations

import html
import re
from typing import Any, Iterable, Mapping

NOTES_MAX = 50
TEXT_MAX = 500
NOTES_CHARS = 6000
ORIGINS = ("person", "cc")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def normalize(value: Any) -> str:
    """去掉控制字符、空白合成一个空格（不判长短）。"""
    return " ".join(_CONTROL.sub(" ", str(value or "")).split())


def valid(text: str) -> bool:
    """规整后的一条能不能存：不为空、不超过 `TEXT_MAX` 字。"""
    return bool(text) and len(text) <= TEXT_MAX


def safe(value: Any) -> str:
    return html.escape(" ".join(str(value or "").split()), quote=True)


def note_lines(notes: Iterable[Mapping[str, Any]], budget: int = NOTES_CHARS) -> tuple[list, int]:
    """带给 CC 的知识库各行（``- [编号] 正文``，从旧到新）与放不下的条数。"""
    notes = list(notes)
    lines, used = [], 0
    for index, note in enumerate(notes):
        line = f"- [{safe(note.get('id'))}] {safe(note.get('text'))}"
        if used + len(line) > budget:
            return lines, len(notes) - index
        lines.append(line)
        used += len(line) + 1
    return lines, 0


__all__ = ["NOTES_CHARS", "NOTES_MAX", "ORIGINS", "TEXT_MAX", "normalize", "note_lines", "safe", "valid"]
