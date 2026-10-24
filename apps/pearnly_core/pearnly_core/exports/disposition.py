"""下载响应头里的文件名（多产品共用，纯函数、只依赖标准库）。

框架原生下载（`type` = download / binary / pdf）把文件名按 latin-1 原样写进 ``Content-Disposition``，中文 / 泰文
文件名在浏览器里成了乱码（ERP 任务管理复测 10-23，Cowork 同样）。这里照 RFC 6266 / RFC 5987 写：
``filename*=UTF-8''<百分号编码>`` 给现代浏览器，前面再带一个只含 ASCII 的 ``filename="…"`` 兜底。
"""
from __future__ import annotations

import re
from urllib.parse import quote

FALLBACK = "download"
# 兜底名里不能出现的：非 ASCII、控制字符、引号、反斜杠（引号串里要转义的两个直接换掉）。
_UNSAFE = re.compile(r'[^\x20-\x7e]+|["\\]+')


def needs_encoding(filename: str) -> bool:
    """原样写进响应头会出问题的文件名：带非 ASCII、控制字符、引号或反斜杠。"""
    return bool(_UNSAFE.search(filename or ""))


def ascii_fallback(filename: str) -> str:
    """只含可打印 ASCII 的兜底名：不安全的一段换成一个 ``_``，去掉开头的分隔符；名字部分空了用 ``download``。"""
    name = _UNSAFE.sub("_", filename or "")
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    stem = stem.lstrip("_-. ")
    if not stem.strip("_-. "):
        stem = FALLBACK
    return stem + (f".{extension}" if extension else "")


def header(kind: str, filename: str) -> str:
    """``Content-Disposition`` 的值：``kind`` 是 attachment / inline。"""
    kind = (kind or "attachment").strip() or "attachment"
    name = (filename or "").replace("\r", "").replace("\n", "")
    if not name:
        return kind
    if not needs_encoding(name):
        return f'{kind}; filename="{name}"'
    return f"{kind}; filename=\"{ascii_fallback(name)}\"; filename*=UTF-8''{quote(name, safe='')}"


__all__ = ["FALLBACK", "ascii_fallback", "header", "needs_encoding"]
