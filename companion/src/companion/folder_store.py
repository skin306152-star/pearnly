# -*- coding: utf-8 -*-
"""文件夹自动录入的本机状态：允许读取的根目录、每条规则的运行进度与文件台账。

全部放在 ``%APPDATA%\\Pearnly\\folder_intake\\`` 下的独立文件里，**不碰 companion.json**（配对与连接配置的
格式、版本号都不变；旧版小助手看不到这些文件，降级再升级也不丢配对）。

按「连接身份」分开存：服务器地址 + 连接号（配对码里的那一段）。同一个配置改配到别的工作空间 / 租户，
身份就变了，原来允许读取的根目录不再沿用，要在本机重新确认（防止把文件夹带到别家）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from .config import APPDATA_DIR
from .pairing import _endpoint_id_from_token

STATE_DIR = APPDATA_DIR / "folder_intake"
ROOTS_FILE = "roots.json"
_LOCK = threading.RLock()
# 推送结论已定、表格也取过的文件，台账里留这么久后只留一条「墓碑」（规则、相对路径、大小、修改时间、结束时间），
# 同一份文件仍认得：不再登记、结果表格也不会再写一份到当月（2026-10 起；以前整行删掉，删后会再登记一次）。
# 墓碑在规则文件夹完整扫描一次、文件已不在时清掉（`Ledger.forget_missing`），台账以文件夹里现存的文件为上限。
KEEP_SECONDS = 120 * 86400
TOMBSTONE_FIELDS = ("rule", "relpath", "size", "mtime", "closed_at")


def connection_key(cfg) -> str:
    """服务器地址 + 连接号 → 本机状态的键；任一项变了就是另一条连接。"""
    host = (urlsplit(str(getattr(cfg, "backend_url", "") or "")).netloc or "").lower()
    endpoint = _endpoint_id_from_token(str(getattr(cfg, "bearer_token", "") or "")) or str(
        getattr(cfg, "endpoint_id", "") or "")
    if not host or not endpoint:
        return ""
    return hashlib.sha256(f"{host}|{endpoint}".encode("utf-8")).hexdigest()[:24]


def _dir() -> Path:
    return Path(os.environ.get("PEARNLY_FOLDER_STATE_DIR") or STATE_DIR)


def _read(name: str, default: Any) -> Any:
    path = _dir() / name
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(name: str, value: Any) -> None:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)
    os.replace(temporary, folder / name)


# ── 允许读取的根目录（本机设，按连接分开） ───────────────────────────────────────────


def roots(key: str) -> list[dict]:
    with _LOCK:
        rows = _read(ROOTS_FILE, {}).get(key) or []
    return [row for row in rows if isinstance(row, dict) and row.get("path")]


def set_all_roots(mapping: dict) -> None:
    """一次保存多条连接的根目录：先整体检查（任何两个根目录都不能相同或互相包含，不论是不是同一条连接——
    否则同一份文件会被两条规则各收一次、各扣一次费），全部通过才写，不会只存一半。"""
    from .folder_plan import root_id

    with _LOCK:
        everything = _read(ROOTS_FILE, {})
        for key, paths in mapping.items():
            clean = []
            for path in paths:
                normal = os.path.normpath(str(path).strip())
                if normal and normal not in clean:
                    clean.append(normal)
            everything[key] = [{"id": root_id(path), "label": path, "path": path} for path in clean]
            if not clean:
                everything.pop(key, None)
        flat = [row.get("path", "") for rows in everything.values() for row in rows or []]
        for index, left in enumerate(flat):
            for right in flat[index + 1:]:
                if _overlap(left, right):
                    raise ValueError(right)
        _write(ROOTS_FILE, everything)


def set_roots(key: str, paths: list[str]) -> None:
    """保存一条连接允许读取的根目录（检查口径同 :func:`set_all_roots`）。"""
    set_all_roots({key: paths})


def _overlap(a: str, b: str) -> bool:
    left = os.path.normcase(os.path.normpath(a)).rstrip("\\/")
    right = os.path.normcase(os.path.normpath(b)).rstrip("\\/")
    if not left or not right:
        return False
    return left == right or left.startswith(right + os.sep) or right.startswith(left + os.sep)


# ── 每条连接的运行状态与文件台账 ─────────────────────────────────────────────────────


class Ledger:
    """一条连接的本机进度：各规则跑到哪（``rules``）、每份文件传到哪一步（``files``）、待上报的运行记录（``runs``），
    以及云端对话提醒已经收到哪一刻为止（``notices_after``，epoch 秒，随同步上报）。"""

    def __init__(self, key: str) -> None:
        self.key = key
        self.name = f"ledger-{key}.json"
        with _LOCK:
            data = _read(self.name, {})
        self.rules: dict = data.get("rules") or {}
        self.files: dict = data.get("files") or {}
        self.runs: list = data.get("runs") or []
        try:
            self.notices_after = max(0.0, float(data.get("notices_after") or 0))
        except (TypeError, ValueError):
            self.notices_after = 0.0

    def save(self) -> None:
        now = time.time()
        for name, row in list(self.files.items()):
            if row.get("entry") and row.get("closed_at") and now - float(row["closed_at"]) > KEEP_SECONDS:
                self.files[name] = {field: row[field] for field in TOMBSTONE_FIELDS if field in row}
        with _LOCK:
            _write(self.name, {"rules": self.rules, "files": self.files, "runs": self.runs[-50:],
                               "notices_after": self.notices_after})

    def forget_missing(self, rule: str, seen: set) -> int:
        """规则文件夹完整扫描一次之后（``seen``：扫到的相对路径，casefold）：清掉文件已不在的墓碑、「不收」与补传记录
        （没有在途条目的行）；还在等结果的行照留。返回清掉几条。"""
        gone = [name for name, row in self.files.items() if row.get("rule") == rule and not row.get("entry")
                and str(row.get("relpath") or "").casefold() not in seen]
        for name in gone:
            del self.files[name]
        return len(gone)

    def rule_state(self, rule: str) -> dict:
        return self.rules.setdefault(rule, {})

    @staticmethod
    def file_key(rule: str, relpath: str) -> str:
        return f"{rule}|{relpath.casefold()}"

    def known(self, rule: str, relpath: str, size: int, mtime: float) -> Optional[dict]:
        row = self.files.get(self.file_key(rule, relpath))
        if row and not row.get("retry") and int(row.get("size") or 0) == size \
                and abs(float(row.get("mtime") or 0) - mtime) < 1:
            return row
        return None

    def watch(self, limit: int = 500) -> list[str]:
        """要向云端问状态的条目：还没完的优先，其次是完成了但还可能有新版表格的（30 天内）。"""
        now = time.time()
        open_rows = [row for row in self.files.values() if row.get("entry") and not row.get("finished")]
        recent = [row for row in self.files.values() if row.get("entry") and row.get("finished")
                  and not row.get("closed_at") and now - float(row.get("finished_at") or now) <= 30 * 86400]
        names = []
        for row in open_rows + recent:
            if row["entry"] not in names:
                names.append(row["entry"])
            if len(names) >= limit:
                break
        return names

    def by_entry(self, entry: str) -> Optional[dict]:
        for row in self.files.values():
            if row.get("entry") == entry:
                return row
        return None
