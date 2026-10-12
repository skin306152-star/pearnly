# -*- coding: utf-8 -*-
"""按需请求里的两种文件夹请求（服务端 ``folder_intake/fetch.py`` 发起，CC 对话里用）：列已授权根目录里的文件
（``folder_list``）、交回其中一份（``folder_fetch``）。只读：不改、不删、不登记，与文件夹自动录入的台账无关。

- 只认本机在 CC 桌面端里加过的根目录（``folder_store.roots(connection_key(cfg))``，与文件夹自动录入同一份，按编号找），
  云端给的编号不在这里就不做（root_missing）；
- 路径拆段，拒绝空路径、``.``、``..``、盘符、以分隔符开头的绝对 / UNC 路径与 Windows 不认的字符；最终路径（解开符号链接与
  联接点之后）必须仍在根目录里面（outside_root）；
- 只认 ``FETCH_EXTENSIONS``（与云端 ``folder_intake.contracts.FETCH_EXTENSIONS``、对话附件同一份），一份最多 30 MB；
- 列文件跳过隐藏、临时与 Office 锁文件，限时限量限深（看不完、有更深的文件夹没进去都标 ``truncated``），新的在前；
  不进符号链接 / 联接点：经它们到的文件要么在根目录外（取不了，outside_root），要么在根目录里、按真实路径已经列出，
  所以不算没列完。
"""

from __future__ import annotations

import gzip
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Callable

from .folder_plan import _skipped_name, is_result_folder
from .folder_store import connection_key, roots
from .request_contract import MAX_BYTES, RequestFailed, Result

FETCH_EXTENSIONS = (".csv", ".docx", ".gif", ".heic", ".heif", ".jpeg", ".jpg", ".json", ".md", ".pdf", ".png",
                    ".tsv", ".txt", ".webp", ".xls", ".xlsx")
FILE_MAX_BYTES = MAX_BYTES["folder_fetch"]
LIST_MAX = 5000
SCAN_ENTRIES = 50_000
SCAN_SECONDS = 10.0
DEPTH = 12
_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
# Windows 的重解析点：联接点（mount point）与符号链接
_REPARSE_TAGS = (0xA0000003, 0xA000000C)


def _root(cfg, root_id) -> Path:
    if isinstance(root_id, str) and root_id:
        for row in roots(connection_key(cfg)):
            if row.get("id") == root_id:
                path = Path(row["path"])
                if path.is_dir():
                    return path
                break
    raise RequestFailed("root_missing")


def _parts(value, *, allow_empty: bool) -> list:
    """整条路径去掉首尾空白、按分隔符拆段，每段原样保留（与服务端 ``folder_intake.contracts._browse_parts`` 同一规则）。"""
    if not isinstance(value, str) or len(value) > 1000:
        raise RequestFailed("path_invalid")
    text = value.strip()
    if text.startswith(("\\", "/")) or re.match(r"^[A-Za-z]:", text):
        raise RequestFailed("path_invalid")  # 绝对路径、UNC、盘符
    parts = [part for part in re.split(r"[\\/]+", text) if part]
    if not parts and not allow_empty:
        raise RequestFailed("path_invalid")
    if any(part in (".", "..") or not part.strip() or _BAD.search(part) for part in parts):
        raise RequestFailed("path_invalid")
    return parts


def _inside(root: Path, target) -> bool:
    """解开符号链接与联接点后，``target`` 仍在根目录里面（或就是根目录）。"""
    base = os.path.normcase(os.path.realpath(str(root)))
    real = os.path.normcase(os.path.realpath(str(target)))
    return real == base or real.startswith(base.rstrip("\\/") + os.sep)


def _is_link(path: str) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) in _REPARSE_TAGS


def _extensions(value) -> tuple:
    wanted = tuple(item for item in (value if isinstance(value, list) else []) if item in FETCH_EXTENSIONS)
    return wanted or FETCH_EXTENSIONS


def list_files(cfg, params: dict, *, clock: Callable[[], float] = time.monotonic) -> Result:
    """根目录（或其中一个文件夹，含子文件夹）里能取的文件：{files: [{path, name, size, modified, result_folder}],
    truncated, scanned}，gzip 后的 JSON；``path`` 相对根目录、``\\`` 分隔，``modified`` 是 epoch 秒。"""
    root = _root(cfg, params.get("root_id"))
    start = root.joinpath(*_parts(params.get("folder") or "", allow_empty=True))
    if not start.is_dir() or not _inside(root, start):
        raise RequestFailed("folder_missing")
    search = str(params.get("search") or "").strip().casefold()[:100]
    extensions = _extensions(params.get("extensions"))
    limit = params.get("limit")
    limit = limit if type(limit) is int and 0 < limit <= LIST_MAX else LIST_MAX
    deadline = clock() + SCAN_SECONDS
    found, scanned, truncated = [], 0, False
    base_depth = len(start.parts)
    for current, dirs, names in os.walk(start):
        here = Path(current)
        dirs[:] = sorted(d for d in dirs if not _skipped_name(d) and not _is_link(os.path.join(current, d)))
        if dirs and len(here.parts) - base_depth >= DEPTH:
            dirs[:] = []
            truncated = True  # 更深的文件夹没进去：清单不全
        for name in sorted(names):
            scanned += 1
            if scanned > SCAN_ENTRIES or clock() > deadline:
                truncated = True
                break
            if _skipped_name(name) or not name.lower().endswith(extensions):
                continue
            path = here / name
            relative = "\\".join(path.relative_to(root).parts)
            if search and search not in relative.casefold():
                continue
            try:
                info = os.stat(path)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or not _inside(root, path):
                continue
            found.append({"path": relative, "name": name, "size": info.st_size, "modified": info.st_mtime,
                          "result_folder": any(is_result_folder(part) for part in relative.split("\\")[:-1])})
        if truncated:
            break
    found.sort(key=lambda row: (-row["modified"], row["path"]))
    if len(found) > limit:
        found, truncated = found[:limit], True
    payload = {"files": found, "truncated": truncated, "scanned": scanned}
    content = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if len(content) > MAX_BYTES["folder_list"]:
        raise RequestFailed("too_large")
    return Result(content, {"format": "json.gz", "count": len(found)})


def read_file(cfg, params: dict) -> Result:
    """根目录里的一份文件原样交回；``meta`` 带文件名与修改时间。"""
    root = _root(cfg, params.get("root_id"))
    parts = _parts(params.get("path"), allow_empty=False)
    name = parts[-1]
    if not name.lower().endswith(FETCH_EXTENSIONS):
        raise RequestFailed("type_not_allowed")
    target = root.joinpath(*parts)
    if not _inside(root, target):
        raise RequestFailed("outside_root")
    try:
        info = os.stat(target)
    except FileNotFoundError:
        raise RequestFailed("not_found") from None
    except OSError:
        raise RequestFailed("unreadable") from None
    if not stat.S_ISREG(info.st_mode):
        raise RequestFailed("not_found")
    if info.st_size > FILE_MAX_BYTES:
        raise RequestFailed("too_large")
    if info.st_size == 0:
        raise RequestFailed("empty")
    try:
        with open(target, "rb") as handle:
            content = handle.read(FILE_MAX_BYTES + 1)
    except PermissionError:
        raise RequestFailed("file_busy") from None
    except OSError:
        raise RequestFailed("unreadable") from None
    if len(content) > FILE_MAX_BYTES:
        raise RequestFailed("too_large")
    if not content:
        raise RequestFailed("empty")
    return Result(content, {"name": name, "modified": info.st_mtime})
