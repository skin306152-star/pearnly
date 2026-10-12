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
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

from .config import APPDATA_DIR
from .pairing import _endpoint_id_from_token

STATE_DIR = APPDATA_DIR / "folder_intake"
ROOTS_FILE = "roots.json"
_LOCK = threading.RLock()
# 推送结论已定、表格也取过的文件，台账里留这么久后只留一条「墓碑」（规则、相对路径、大小、修改时间、结束时间），
# 同一份文件仍认得：不再登记、结果表格也不会再写一份到当月（2026-10 起；以前整行删掉，删后会再登记一次）。
# 墓碑在规则文件夹完整扫描一次、文件已不在时清掉（`Ledger.forget_missing`），台账以文件夹里现存的文件为上限。
# 已删除、换到别的电脑读的任务：云端回复里列出这台电脑读的全部任务名（`rule_names`，含停用的），超过同样这么久
# 没再出现的任务连同它的台账行整批删掉（`Ledger.saw_rules`）。
KEEP_SECONDS = 120 * 86400
TOMBSTONE_FIELDS = ("rule", "relpath", "size", "mtime", "closed_at")
# 任务「最近一次出现在同步回复里」的时间隔这么久才改一次（不为它每轮重写台账）。
SEEN_REFRESH = 86400


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
    _write_text(name, json.dumps(value, ensure_ascii=False, sort_keys=True))


def _write_text(name: str, text: str) -> None:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (name + ".tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(text)
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
    """一条连接的本机进度：各规则跑到哪（``rules``，另记 ``seen_at`` 最近一次出现在同步回复里的时间、``open_run``
    中途停下的那一次运行）、每份文件传到哪一步（``files``）、待上报的运行记录（``runs``），以及云端对话提醒已经收到
    哪一刻为止（``notices_after``，epoch 秒，随同步上报；``None`` = 这条连接还没收到过提醒回复）。

    ``save`` 只在内容变了时才写（台账大时整份重写很费盘），序列化在锁外做，写文件时才持锁。"""

    def __init__(self, key: str) -> None:
        self.key = key
        self.name = f"ledger-{key}.json"
        with _LOCK:
            data = _read(self.name, {})
        self.rules: dict = data.get("rules") or {}
        self.files: dict = data.get("files") or {}
        self.runs: list = data.get("runs") or []
        value = data.get("notices_after")
        try:
            self.notices_after: Optional[float] = None if value is None else max(0.0, float(value))
        except (TypeError, ValueError):
            self.notices_after = None
        self._saved = _digest(self._text()) if data else None

    def _text(self) -> str:
        return json.dumps({"rules": self.rules, "files": self.files, "runs": self.runs[-50:],
                           "notices_after": self.notices_after}, ensure_ascii=False, sort_keys=True)

    def save(self) -> bool:
        """写回本机；内容和上次读到 / 写过的一样就不写。返回写了没有。"""
        now = time.time()
        for name, row in list(self.files.items()):
            if row.get("entry") and row.get("closed_at") and now - float(row["closed_at"]) > KEEP_SECONDS:
                self.files[name] = {field: row[field] for field in TOMBSTONE_FIELDS if field in row}
        text = self._text()
        digest = _digest(text)
        if digest == self._saved:
            return False
        with _LOCK:
            _write_text(self.name, text)
        self._saved = digest
        return True

    def forget_missing(self, rule: str, seen: set, keep: Iterable[str] = ()) -> int:
        """规则文件夹完整扫描一次之后（``seen``：扫到的相对路径，casefold）：清掉文件已不在的墓碑、「不收」与补传记录
        （没有在途条目的行）；还在等结果的行照留。``keep``：这次读不了的文件夹 / 文件（相对规则文件夹、casefold；
        ``""`` = 整个规则文件夹）——只有它们下面的行照留（网络抖一下、某个子文件夹没权限，别处照清）。返回清掉几条。"""
        guarded = tuple(keep)
        if "" in guarded:
            return 0

        def kept(relpath: str) -> bool:
            return any(relpath == prefix or relpath.startswith(prefix + "\\") for prefix in guarded)

        gone = [name for name, row in self.files.items() if row.get("rule") == rule and not row.get("entry")
                and str(row.get("relpath") or "").casefold() not in seen
                and not kept(str(row.get("relpath") or "").casefold())]
        for name in gone:
            del self.files[name]
        return len(gone)

    def saw_rules(self, names: Iterable[str], now: float, *, complete: bool) -> int:
        """这一次同步回复里出现的任务名：记下最近一次出现的时间（隔 ``SEEN_REFRESH`` 才改一次）。

        ``complete``：回复带的是这台电脑读的全部任务名（含停用的，云端 ``rule_names``）。这时才清：超过 ``KEEP_SECONDS``
        没再出现的任务（已删除、换到别的电脑读）连同它的台账行整批删掉；以前没记过时间的从现在起算。只看到启用中的
        任务时不清——停用很久再启用的新任务靠这些行认出处理过的文件。返回删掉几行文件记录。"""
        listed = {name for name in names if name}
        for name in listed:
            state = self.rule_state(name)
            if now - float(state.get("seen_at") or 0) >= SEEN_REFRESH:
                state["seen_at"] = now
        if not complete:
            return 0
        doomed = set()
        for name in (set(self.rules) | {row.get("rule") for row in self.files.values()}) - listed:
            if not name:
                continue
            state = self.rule_state(name)
            if state.get("seen_at") is None:
                state["seen_at"] = now
            elif now - float(state["seen_at"]) > KEEP_SECONDS:
                doomed.add(name)
        gone = [key for key, row in self.files.items() if row.get("rule") in doomed]
        for key in gone:
            del self.files[key]
        for name in doomed:
            self.rules.pop(name, None)
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

    def entry_index(self) -> dict:
        """{条目号: 台账行}（一轮里逐条问状态时先建一次，不每条都把整份台账走一遍）。"""
        index: dict = {}
        for row in self.files.values():
            if row.get("entry"):
                index.setdefault(row["entry"], row)
        return index


def _digest(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).digest()
