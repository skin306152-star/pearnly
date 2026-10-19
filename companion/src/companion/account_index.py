# -*- coding: utf-8 -*-
"""账套清单本机索引（2026-10-18）：上次读到的登记表行、每份账套的公司名 / 税号 / 分店号 / 默认科目 / 会计期间、
用过的科目表，连同读它们时文件的（大小, 修改时间），存在本机 ``account_index.json``（与 companion.json 同一目录，
不含任何密钥、令牌或单据内容）。

为什么：一台电脑授权了十几个年度文件夹、一千多份账套时（办公室测试机 1353 份，其中 1252 份在局域网服务器上），
原来每次启动都把每份账套的 ISINFO（两次）、ISPRD 重新打开一遍：四千多次打开 DBF，测试机约 5 分钟、21.8 秒 CPU，
还给事务所的服务器加读盘负担。有了索引，重启后每份账套只看一眼两个文件的大小与修改时间（stat），没变就用上次读到的，
变了的那几份才重读。

失效规则：
- 键是文件 / 目录路径（不分大小写）；值连着读它时的（大小, 修改时间纳秒）。对不上就当没有，重读后覆盖；
  读的过程中文件又变了也没关系：存的是读之前的大小与修改时间，下一遍对不上会再读。
- 账套行里的「可写」「文件号（uid）」「网络路径（share）」不进索引，每一遍都现查（多台电脑认同一份账套靠文件号，
  必须是现在的；写入照旧只认推送目标里明确选定、当场核过的账套）。
- 30 天没用到的条目存盘时删掉（账套删了、年度文件夹不再授权的不会一直留着）；科目表只留最近用过的 8 份。
- 文件坏了 / 版本不对 / 读不了 → 当作空索引（重读一遍就补回来），不报错；写盘先写临时文件再整体替换。
- 「上次找新年度文件夹的时间」也记在这里（``discovered_at``）：重启不再每次都整盘找，24 小时一次；
  网页「重新读取」照旧马上找。
- 录入标记（1.9.1，第 3 期 E3）：每份账套 ``GLJNL.DBF`` 表头读出的「最后更新日期 / 记录数」，同样连着（大小, 修改时间）存，
  文件没变就不再打开它（``entries``；旧版本机索引没有这一项，当空，读一遍补上）。

本模块只管内存与本机文件，不读任何账套（读账套在 account_probe）；线程安全，整个程序共用一个（``INDEX``）。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logger import get_logger

log = get_logger(__name__)

VERSION = 1
KEEP_DAYS = 30
KEEP_CHARTS = 8
_ANY = object()


def index_file() -> Path:
    from .config import APPDATA_DIR

    return APPDATA_DIR / "account_index.json"


def _today() -> str:
    return _dt.date.today().isoformat()


def _pair(value) -> Optional[Tuple[int, int]]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _details_stamp(value) -> Optional[tuple]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    return tuple(_pair(item) if item is not None else None for item in value)


def _details_value(value) -> Optional[tuple]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    company, tax_id, mapping, extra = value
    if not isinstance(mapping, dict) or not isinstance(extra, dict):
        return None
    return str(company or ""), str(tax_id or ""), dict(mapping), dict(extra)


class AccountIndex:
    """见文件头。所有方法都可以在任何线程调；第一次用到时才读本机文件。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loaded = False
        self._dirty = False
        self._details: Dict[str, list] = {}  # 账套目录 → [(ISINFO 戳, ISPRD 戳), (公司名, 税号, 默认科目, 期间等), 用到的日期]
        self._registry: Dict[str, list] = {}  # 登记表路径 → [戳, [(显示名, 槽位, 原始路径)], 用到的日期]
        self._charts: Dict[str, list] = {}  # 账套目录 → [GLACC 戳, 科目表, 用到的日期, 用到的次序]
        self._entries: Dict[str, list] = {}  # 账套目录 → [GLJNL 戳, 录入标记, 用到的日期]
        self._discovered_at = 0.0
        self._order = 0

    # ── 读写本机文件 ────────────────────────────────────────────────────────────

    def _ensure(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = json.loads(index_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            return
        today = _today()
        for key, entry in (raw.get("details") or {}).items():
            if isinstance(entry, list) and len(entry) == 3:
                stamp, value = _details_stamp(entry[0]), _details_value(entry[1])
                if stamp is not None and value is not None:
                    self._details[str(key)] = [stamp, value, str(entry[2] or today)]
        for key, entry in (raw.get("registry") or {}).items():
            if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[1], list):
                stamp = _pair(entry[0])
                rows = [tuple(str(cell or "") for cell in row) for row in entry[1]
                        if isinstance(row, list) and len(row) == 3]
                if stamp is not None:
                    self._registry[str(key)] = [stamp, rows, str(entry[2] or today)]
        for key, entry in (raw.get("charts") or {}).items():
            if isinstance(entry, list) and len(entry) == 4 and isinstance(entry[1], list):
                stamp = _pair(entry[0])
                rows = [dict(row) for row in entry[1] if isinstance(row, dict)]
                if stamp is not None:
                    self._charts[str(key)] = [stamp, rows, str(entry[2] or today), int(entry[3] or 0)]
                    self._order = max(self._order, int(entry[3] or 0))
        for key, entry in (raw.get("entries") or {}).items():
            if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[1], str):
                stamp = _pair(entry[0])
                if stamp is not None:
                    self._entries[str(key)] = [stamp, entry[1], str(entry[2] or today)]
        try:
            self._discovered_at = float(raw.get("discovered_at") or 0.0)
        except (TypeError, ValueError):
            self._discovered_at = 0.0
        # 按账套文件夹计（几个年度登记同一个文件夹只算一个；本机找不到的文件夹没有可存的）：与清单里的「账套 N 个」不是一回事
        log.info("[accounts] 本机账套索引：%d 个账套文件夹、%d 个登记表", len(self._details), len(self._registry))

    def save(self) -> bool:
        """有改动才写（先写临时文件再整体替换）；30 天没用到的条目这时删掉。返回是否写了。"""
        with self._lock:
            if not self._loaded or not self._dirty:
                return False
            cutoff = (_dt.date.today() - _dt.timedelta(days=KEEP_DAYS)).isoformat()
            for table in (self._details, self._registry, self._charts, self._entries):
                for key in [key for key, entry in table.items() if entry[2] < cutoff]:
                    table.pop(key, None)
            data = {
                "version": VERSION,
                "discovered_at": self._discovered_at,
                "details": {key: [list(entry[0]), list(entry[1]), entry[2]] for key, entry in self._details.items()},
                "registry": {key: [list(entry[0]), [list(row) for row in entry[1]], entry[2]]
                             for key, entry in self._registry.items()},
                "charts": {key: [list(entry[0]), entry[1], entry[2], entry[3]] for key, entry in self._charts.items()},
                "entries": {key: [list(entry[0]), entry[1], entry[2]] for key, entry in self._entries.items()},
            }
            self._dirty = False
        target = index_file()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temp = tempfile.mkstemp(prefix=".account_index-", suffix=".json", dir=str(target.parent))
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        except OSError:
            log.warning("[accounts] 本机账套索引存不进去（下次重读）", exc_info=True)
            with self._lock:
                self._dirty = True
            return False
        return True

    # ── 账套详情（ISINFO / ISPRD 读出来的） ─────────────────────────────────────

    def details(self, key: str, stamp: tuple) -> Optional[tuple]:
        with self._lock:
            self._ensure()
            entry = self._details.get(key)
            if entry is None or entry[0] != stamp:
                return None
            self._touch(entry)
            return entry[1]

    def remember_details(self, key: str, stamp: tuple, value: tuple) -> None:
        with self._lock:
            self._ensure()
            self._details[key] = [tuple(stamp), value, _today()]
            self._dirty = True

    # ── 登记表（SCCOMP 的非删除行） ─────────────────────────────────────────────

    def registry(self, key: str, stamp: tuple) -> Optional[List[tuple]]:
        with self._lock:
            self._ensure()
            entry = self._registry.get(key)
            if entry is None or entry[0] != stamp:
                return None
            self._touch(entry)
            return list(entry[1])

    def remember_registry(self, key: str, stamp: tuple, rows: List[tuple]) -> None:
        with self._lock:
            self._ensure()
            self._registry[key] = [tuple(stamp), [tuple(row) for row in rows], _today()]
            self._dirty = True

    # ── 科目表（GLACC） ─────────────────────────────────────────────────────────

    def chart(self, key: str, stamp: Any = _ANY) -> Optional[List[dict]]:
        """``stamp`` 不给：不管文件现在怎样，回上次存的（启动时先报，后台随后核对）。"""
        with self._lock:
            self._ensure()
            entry = self._charts.get(key)
            if entry is None or (stamp is not _ANY and entry[0] != stamp):
                return None
            self._touch(entry)
            return [dict(row) for row in entry[1]]

    def remember_chart(self, key: str, stamp: tuple, rows: List[dict]) -> None:
        with self._lock:
            self._ensure()
            self._order += 1
            self._charts[key] = [tuple(stamp), [dict(row) for row in rows], _today(), self._order]
            for old in sorted(self._charts, key=lambda name: self._charts[name][3])[:-KEEP_CHARTS]:
                self._charts.pop(old, None)
            self._dirty = True

    # ── 录入标记（GLJNL 表头） ───────────────────────────────────────────────────

    def entry(self, key: str, stamp: tuple) -> Optional[str]:
        with self._lock:
            self._ensure()
            found = self._entries.get(key)
            if found is None or found[0] != stamp:
                return None
            self._touch(found)
            return found[1]

    def remember_entry(self, key: str, stamp: tuple, mark: str) -> None:
        with self._lock:
            self._ensure()
            self._entries[key] = [tuple(stamp), str(mark or ""), _today()]
            self._dirty = True

    # ── 找新年度文件夹的时间 ────────────────────────────────────────────────────

    def discovery_due(self, now: float, interval: float) -> bool:
        with self._lock:
            self._ensure()
            return not self._discovered_at or now - self._discovered_at >= interval or now < self._discovered_at

    def mark_discovered(self, now: float) -> None:
        with self._lock:
            self._ensure()
            self._discovered_at = float(now)
            self._dirty = True

    def _touch(self, entry: list) -> None:
        today = _today()
        if entry[2] != today:
            entry[2] = today
            self._dirty = True

    def _reset_for_tests(self) -> None:
        """当作程序重启：内存里的全清，下一次用到时重读本机文件。"""
        with self._lock:
            self._loaded = False
            self._dirty = False
            self._details.clear()
            self._registry.clear()
            self._charts.clear()
            self._entries.clear()
            self._discovered_at = 0.0
            self._order = 0


INDEX = AccountIndex()

__all__ = ["INDEX", "AccountIndex", "index_file"]
