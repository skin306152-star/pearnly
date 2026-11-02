# -*- coding: utf-8 -*-
"""单据明细行的附加说明（Express 工具栏「Note」录的「ป้อนหมายเหตุเพิ่มเติม」，表 ``ARTRNRM``）：按台商品每台的号码随单据写进去，
Express 打印时印在那一行正下方（载荷 17，评估 ``docs/work/2026-11-02-vehicle-artrnrm-评估.md``）。

照 Express 原生写法（服务器测试账套 ``70EXP\\test`` 实录 IV0000040 / RR0000014，``2026-10-25-express-native-rules.md`` 4.9；
S 盘副本 1,040 本、962 万条只读统计，11-02）：

- 一条说明 = 一行：``DOCNUM`` = 单号、``SEQNUM`` C(8) = 明细行号**右对齐 3 位再补空格到 8 位**（「  1     」；「@n」是整单说明，
  这里不写）、``REMARK`` C(50)；同一行的几条按顺序排；只写这张表，单头 / 明细不多任何字段。进货、销售、库存单共用这一张表。
- 界面一行最多录 10 条（962 万条里没有一行超过 10），但表里多于 10 条 Express 照样打开、打印（明细区满了续到下一页）、编辑保存
  不重写说明（69EXP\\TEST 实录 HP691102-002 一行 12 条，11-02），所以不设条数上限。
- 每本账套都有 ``ARTRNRM.CDX``（标签 DOCNUM+SEQNUM）：调用方把 :data:`TABLE` 放进写前备份与重建索引的表。
- 作废：OU / JU / IV / RR / SR 等把这张单的行说明打删除标记（字段不改），调拨 RL 原生保留（统计 157 留 : 5 删）。

载荷明细行带 ``remarks``（来源拼好的说明，每条 ≤ 50 字节 cp874）。没带的单不碰这张表，写出来与以前逐字节相同。
"""

from __future__ import annotations

from array import array
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import dbf

from . import dbf_schema as S

TABLE = "ARTRNRM"
KEY = "remarks"
MAX_BYTES = 50  # REMARK C(50)

ERR_TOO_LONG = "FIELD_TOO_LONG"


class RemarkError(Exception):
    """说明对不上（读回条数 / 内容不符）→ 调用方整单回滚。"""


def seqnum_text(seq) -> str:
    """行说明的 SEQNUM：行号右对齐 3 位再补空格到 8 位（「  1     」「 12     」）。"""
    return str(seq).strip().rjust(3).ljust(8)


def of_item(item) -> List[str]:
    """载荷明细行上的说明（去掉空的）。"""
    raw = item.get(KEY) if isinstance(item, dict) else None
    if not isinstance(raw, list):
        return []
    return [text for text in (str(value or "").strip() for value in raw) if text]


def any_remarks(items: Iterable) -> bool:
    return any(of_item(item) for item in items or [])


def tables(items: Iterable) -> Tuple[str, ...]:
    """这张单要不要动 ARTRNRM：带说明才进写前备份 / 重建索引（没带的单与以前逐字节相同）。"""
    return (TABLE,) if any_remarks(items) else ()


def problem(items: Iterable) -> Optional[Tuple[str, str]]:
    """写前查：每条 ≤ 50 字节（cp874）。有问题回 ``(错误码, 说明)``，整单不写。"""
    for index, item in enumerate(items or [], start=1):
        for text in of_item(item):
            if len(text.encode(S.CODEPAGE, "replace")) > MAX_BYTES:
                return ERR_TOO_LONG, f"line {index} remark exceeds {MAX_BYTES} bytes: {text!r}"
    return None


def planned(pairs: Iterable[Tuple[object, object]]) -> List[Tuple[str, str]]:
    """``[(行号, 载荷明细行)]`` → ``[(SEQNUM, 说明)]``，按行号、行内顺序排。行号由写入器给（它实际写的 STCRD 行号）。"""
    out = []
    for seq, item in pairs:
        out.extend((seqnum_text(seq), text) for text in of_item(item))
    return out


def append(account_dir, docnum: str, rows: Sequence[Tuple[str, str]]) -> int:
    """追加这张单的行说明；回追加前的记录数（读回只看这之后的行：同号单早有说明时不混）。dbf 库写字符栏会去掉两头空格，
    SEQNUM 追加后按字节补成原生写法。"""
    from dbf.constants import LENGTH, START

    table = dbf.Table(str(Path(account_dir) / f"{TABLE}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_WRITE)
    try:
        since = len(table)
        for seqnum, text in rows:
            table.append({"DOCNUM": docnum, "SEQNUM": seqnum.strip(), "REMARK": text})
            record = table[-1]
            fielddef = record._meta["SEQNUM"]
            start, length = fielddef[START], fielddef[LENGTH]
            record._data[start:start + length] = array("B", seqnum.ljust(length)[:length].encode("ascii"))
            record._update_disk()
        return since
    finally:
        table.close()


def _read(account_dir, docnum: str, since: int = 0) -> List[Tuple[str, str, bool]]:
    table = dbf.Table(str(Path(account_dir) / f"{TABLE}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        out = []
        for record in table:
            if dbf.recno(record) < since or (record.DOCNUM or "").strip() != docnum:
                continue
            out.append((_raw_seqnum(record), (record.REMARK or "").rstrip(), dbf.is_deleted(record)))
        return out
    finally:
        table.close()


def _raw_seqnum(record) -> str:
    from dbf.constants import LENGTH, START

    fielddef = record._meta["SEQNUM"]
    start, length = fielddef[START], fielddef[LENGTH]
    return bytes(record._data[start:start + length]).decode("ascii", "replace")


def verify(account_dir, docnum: str, rows: Sequence[Tuple[str, str]], since: int) -> None:
    """读回本次追加的行：条数、行号字节、内容都要与计划一致，不一致抛 :class:`RemarkError`。"""
    found = [(seqnum, text) for seqnum, text, deleted in _read(account_dir, docnum, since) if not deleted]
    wanted = [(seqnum.ljust(8)[:8], text) for seqnum, text in rows]
    if found != wanted:
        raise RemarkError(f"{TABLE} readback {found!r} != {wanted!r}")


Written = Tuple[List[Tuple[str, str]], int]  # (写了哪些行, 追加前的记录数)


def write(account_dir, docnum: str, pairs: Iterable[Tuple[object, object]]) -> Written:
    """写入器写完明细后调：``pairs`` = ``[(这一项实际写的 STCRD 行号, 载荷明细行)]``。没有说明什么都不碰。"""
    rows = planned(pairs)
    return (rows, append(account_dir, docnum, rows)) if rows else (rows, 0)


def check(account_dir, docnum: str, written: Optional[Written]) -> None:
    """写入器读回时调（:func:`write` 的回值）。"""
    if written and written[0]:
        verify(account_dir, docnum, written[0], written[1])


def has_line_remarks(account_dir, docnum: str) -> bool:
    """这张单有没有还算数的行说明（作废前判，有才把 :data:`TABLE` 放进写前备份与重建索引）。"""
    if not (Path(account_dir) / f"{TABLE}.DBF").is_file():
        return False
    return any(not deleted and not seqnum.strip().startswith("@") for seqnum, _text, deleted in _read(account_dir, docnum))


def void(account_dir, docnum: str) -> int:
    """作废：给这张单的行说明打删除标记（字段不改，整单说明「@n」不动），回打了几条。调拨 RL 原生保留说明，调用方不调。"""
    path = Path(account_dir) / f"{TABLE}.DBF"
    if not path.is_file():
        return 0
    table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(dbf.READ_WRITE)
    count = 0
    try:
        for record in S.live_rows(table):
            if (record.DOCNUM or "").strip() == docnum and not (record.SEQNUM or "").strip().startswith("@"):
                dbf.delete(record)
                count += 1
        return count
    finally:
        table.close()


__all__ = ["ERR_TOO_LONG", "KEY", "MAX_BYTES", "RemarkError", "TABLE", "Written",
           "any_remarks", "append", "check", "has_line_remarks", "of_item", "planned", "problem", "seqnum_text", "tables", "verify", "void",
           "write"]
