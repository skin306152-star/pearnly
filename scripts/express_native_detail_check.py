"""真账副本离线回归共用：CC 新写的存货明细（STCRD）是不是照 Express 原生的记法（10-26，验收 B3b 实录点 ①）。

- 行号 SEQNUM C(3) 右对齐（"  1"；Express 拼 RDOCNUM 是「单号 C(12) + SEQNUM 原样」，以前 CC 写左对齐）；
- 单位 TQUCOD 不空、倍数 TFACTOR > 0（单位空着 Express 录退货要人手补选、倍数 0 报 13）。
另统计这本账套里原生明细（不是这次写的）的行号写法，作对照。
"""
from __future__ import annotations

from pathlib import Path


def problems(dbf, schema, acct: Path, docnums) -> list:
    """这几张单的明细里不照原生记法的，回问题列表（空 = 都对）。"""
    wanted = set(docnums)
    found = []
    table = dbf.Table(str(Path(acct) / "STCRD.DBF"), codepage=schema.CODEPAGE, unicode_errors=schema.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in table.field_names}
        for rec in schema.live_rows(table):
            docnum = str(rec.DOCNUM or "").strip()
            if docnum not in wanted:
                continue
            seq = str(rec.SEQNUM or "")
            if seq != seq.strip().rjust(3):
                found.append(f"{docnum} SEQNUM {seq!r} not right aligned")
            if "TQUCOD" in names and not str(rec.TQUCOD or "").strip():
                found.append(f"{docnum} line {seq.strip()} TQUCOD empty")
            if "TFACTOR" in names and not float(rec.TFACTOR or 0) > 0:
                found.append(f"{docnum} line {seq.strip()} TFACTOR {rec.TFACTOR}")
    finally:
        table.close()
    return found


def native_alignment(dbf, schema, acct: Path, exclude, limit=2000) -> dict:
    """这本账套里原生明细（不是 ``exclude`` 里的单）的行号写法：{"right": n, "left": n}，最多看 ``limit`` 行。"""
    out = {"right": 0, "left": 0}
    table = dbf.Table(str(Path(acct) / "STCRD.DBF"), codepage=schema.CODEPAGE, unicode_errors=schema.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        for rec in schema.live_rows(table):
            seq = str(rec.SEQNUM or "")
            if str(rec.DOCNUM or "").strip() in exclude or not seq.strip() or len(seq.strip()) >= 3:
                continue
            out["right" if seq == seq.strip().rjust(3) else "left"] += 1
            if sum(out.values()) >= limit:
                break
    finally:
        table.close()
    return out
