# -*- coding: utf-8 -*-
"""写完分录后把科目余额表 GLBAL 的对应格标成「待重算」（照 Express 原生；所有会写分录的直写共用这一步）。

Express 原生保存一张单（11-12 正版 Express，Process Monitor 抓取 + 前后文件比对，共享笔记「共享写入实验-11-12/GLBAL核实/」与 P0）：
不累加 GLBAL 的金额，而是对每行分录对应的 GLBAL 行（键 = 科目 ACCNUM + 部门 DEPCOD）把计算状态栏 ``CALSTA`` 里「该期间那一位」置成
``'0'``（待重算）；金额栏、期初栏、LY / NY 栏不动，与借贷方向无关。之后 Express 重算时只重算标了 0 的格，从 GLJNLIT 重新求和。

我们的直写以前写了 GLJNL / GLJNLIT 却不标 CALSTA：那些格仍是 ``'1'``（已算好），写的金额进不了科目余额与试算表，直到有人跑整本重算
（69EXP\\TEST 里我们写的 RE0000006 三个科目 11 月不在 GLBAL 里、CALSTA 全 1；Express 自己录的 RR0000094 四行被标了 0）。

做法：本年度（ISPRD 的 CY 段）第 k 期 → ``CALSTA`` 第 k 位（实证：11 月单、1 月起账的账套标第 11 位）。只把 ``'1'`` 改成 ``'0'``，
别的字节一个不动、不追加行、不动索引键（GLBAL 不重建索引）；``'0'`` 已是待重算，不用改。

还没证据、**不猜**的情形（主会话 11-12）：这几格不写，记进回执 ``meta.glbal_unmarked``（「科目|部门|期: 原因」），由人跑一次重算：
- 对应的 GLBAL 行不存在（Express 那时怎么建行没查清）；
- 单据日期落在下一年度（NY 段，是不是第 13–24 位没核准）或 ISPRD 判不出期间；
- 那一位不是 ``'1'`` 也不是 ``'0'``（例如空格：这一期还没算过，Express 怎么对待没核准）；
- 账套没有 GLBAL 表。

写完立即读回：要标的位都是 ``'0'``，否则抛写盘的读回错误（``READBACK_VERIFY_FAILED``，同各写路的读回不符），由调用方整单回滚
（GLBAL 已在各写路的写前备份里）。

调用方式（各写路在写完 GLJNLIT、关表之后）::

    entries = [...]                       # 刚追加 / 改过的 GLJNLIT 行（dict，至少有 ACCNUM、DEPCOD、VOUDAT）
    glbal_mark.mark(account_dir, entries)

没标上的格由 :func:`collecting` 收集（``worker._handle_dbf_task`` 包住一次写入），随回执 ``meta.glbal_unmarked`` 回云端。
收款分支（``pearnly/collection-cc-chat``）的写路合入后同样在写完 GLJNLIT 后调 :func:`mark`，并把 GLBAL 加进它的写前备份（:data:`TABLE`）。
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

import dbf

from . import dbf_schema as S
from . import fiscal_period
from .logger import get_logger

log = get_logger(__name__)

TABLE = "GLBAL"
FIELD = "CALSTA"
PENDING, DONE = "0", "1"

REASON_NO_TABLE = "no GLBAL"
REASON_NO_ROW = "no GLBAL row"
REASON_PERIOD = "period not in this fiscal year"
REASON_STATE = "calculation state not set"

_unmarked: contextvars.ContextVar[Optional[List[str]]] = contextvars.ContextVar("glbal_unmarked", default=None)


@contextlib.contextmanager
def collecting() -> Iterator[List[str]]:
    """收集这一次写入里没标上的格（「科目|部门|期: 原因」）。"""
    bucket: List[str] = []
    token = _unmarked.set(bucket)
    try:
        yield bucket
    finally:
        _unmarked.reset(token)


def _note(text: str) -> None:
    bucket = _unmarked.get()
    if bucket is not None and text not in bucket:
        bucket.append(text)
    log.warning("科目余额没标待重算：%s", text)


def _day(value) -> Optional[dt.date]:
    if isinstance(value, dt.datetime):
        return value.date()
    return value if isinstance(value, dt.date) else None


def _text(value) -> str:
    return str(value or "").strip()


def _cells(account_dir, entries: Iterable) -> List[Tuple[str, str, Optional[dt.date]]]:
    seen, out = set(), []
    for row in entries or ():
        get = row.get if isinstance(row, dict) else (lambda key, _r=row: getattr(_r, key, None))
        key = (_text(get("ACCNUM")), _text(get("DEPCOD")), _day(get("VOUDAT")))
        if key[0] and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def mark(account_dir, entries: Iterable) -> List[str]:
    """把这些分录行对应的 GLBAL 格标成待重算；返回没标上的格（同时记进 :func:`collecting`）。"""
    cells = _cells(account_dir, entries)
    if not cells:
        return []
    path = Path(account_dir) / f"{TABLE}.DBF"
    if not path.is_file():
        skipped = [f"{acc}|{dep}|: {REASON_NO_TABLE}" for acc, dep, _day_ in cells]
        for text in skipped:
            _note(text)
        return skipped
    calendar = fiscal_period.load(account_dir)
    wanted = {}  # (科目, 部门) -> {位}
    skipped: List[str] = []
    for acc, dep, day in cells:
        period = calendar.locate(day) if (calendar is not None and day is not None) else None
        if period is None or period.slot != fiscal_period.SLOT_CY:
            skipped.append(f"{acc}|{dep}|{day}: {REASON_PERIOD}")
            continue
        wanted.setdefault((acc, dep), set()).add(period.index)
    changed = []  # (recno, 位)
    table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_WRITE)
    try:
        if FIELD not in {name.upper() for name in table.field_names}:
            skipped += [f"{acc}|{dep}|{index}: {REASON_NO_TABLE}" for (acc, dep), spots in wanted.items() for index in spots]
            wanted = {}
        found = set()
        for rec in S.live_rows(table):
            key = (_text(rec["ACCNUM"]), _text(rec["DEPCOD"]))
            spots = wanted.get(key)
            if not spots:
                continue
            found.add(key)
            state = str(rec[FIELD] or "")
            text = list(state.ljust(max(len(state), max(spots))))
            touched = False
            for index in sorted(spots):
                if text[index - 1] == DONE:
                    text[index - 1] = PENDING
                    touched = True
                    changed.append((dbf.recno(rec), index))
                elif text[index - 1] != PENDING:
                    skipped.append(f"{key[0]}|{key[1]}|{index}: {REASON_STATE}")
            if touched:
                with rec as fields:
                    fields[FIELD] = "".join(text)
        for (acc, dep), spots in wanted.items():
            if (acc, dep) not in found:
                skipped += [f"{acc}|{dep}|{index}: {REASON_NO_ROW}" for index in sorted(spots)]
    finally:
        table.close()
    _verify(path, changed)
    for text in skipped:
        _note(text)
    return skipped


def _verify(path: Path, changed: List[Tuple[int, int]]) -> None:
    if not changed:
        return
    table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        for recno, index in changed:
            state = str(table[recno][FIELD] or "")
            if len(state) < index or state[index - 1] != PENDING:
                from .dbf_writer import ERR_VERIFY, _WriteError  # 写盘错误类在 dbf_writer（它导入本模块，这里按需导入免得互相导入）

                raise _WriteError(f"GLBAL row {recno} {FIELD} position {index} is {state!r} after marking", ERR_VERIFY,
                                  {"kind": "readback_verify", "table": TABLE, "recno": recno})
    finally:
        table.close()


__all__ = ["TABLE", "collecting", "mark"]
