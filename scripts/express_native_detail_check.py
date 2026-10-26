"""真账副本离线回归共用：CC 新写的存货明细（STCRD）是不是照 Express 原生的记法（10-26，验收 B3b 实录点 ①）。

- 行号 SEQNUM C(3) 右对齐（"  1"；Express 拼 RDOCNUM 是「单号 C(12) + SEQNUM 原样」，以前 CC 写左对齐）；单头 NXTSEQ、凭证 SEQIT
  同样右对齐；
- 单位 TQUCOD 不空、倍数 TFACTOR > 0（单位空着 Express 录退货要人手补选、倍数 0 报 13）；
- 金额跟单头价格口径：价含税（FLGVAT 1）应税行 TRNVAL 记含税，其余 TRNVAL = NETVAL。
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
    # 明细金额跟单头的价格口径（S 盘副本 286 本，10-26）：价含税（FLGVAT 1，现销 HS / 现购 HP / 退货 SR）应税行 TRNVAL ≈ NETVAL × 1.07、
    # 免税行（VATCOD '0'）TRNVAL = NETVAL；价不含税 / 无税（赊单、FLGVAT 2 / 0）TRNVAL = NETVAL
    flags = {}
    for name in ("ARTRN", "APTRN"):
        path = Path(acct) / f"{name}.DBF"
        if path.is_file():
            table = dbf.Table(str(path), codepage=schema.CODEPAGE, unicode_errors=schema.UNICODE_ERRORS)
            table.open(dbf.READ_ONLY)
            try:
                flags.update({str(rec.DOCNUM or "").strip(): str(rec.FLGVAT or "").strip()
                              for rec in schema.live_rows(table) if str(rec.DOCNUM or "").strip() in wanted})
            finally:
                table.close()
    table = dbf.Table(str(Path(acct) / "STCRD.DBF"), codepage=schema.CODEPAGE, unicode_errors=schema.UNICODE_ERRORS)
    table.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in table.field_names}
        for rec in schema.live_rows(table):
            docnum = str(rec.DOCNUM or "").strip()
            if docnum not in flags:
                continue
            gross, net = float(rec.TRNVAL or 0), float(rec.NETVAL or 0)
            exempt = "VATCOD" in names and str(rec.VATCOD or "").strip() == "0"
            want = net * 1.07 if flags[docnum] == "1" and not exempt else net
            if abs(gross - want) > 0.05:
                found.append(f"{docnum} line {str(rec.SEQNUM).strip()} FLGVAT {flags[docnum]!r} TRNVAL {gross} NETVAL {net}")
    finally:
        table.close()
    # 单头「下一行号」与凭证行号也照原生右对齐（ARTRN / APTRN / STTRN.NXTSEQ C(3)、GLJNLIT.SEQIT C(2)，S 盘副本 121 本，10-26）
    for name, key, field in (("ARTRN", "DOCNUM", "NXTSEQ"), ("APTRN", "DOCNUM", "NXTSEQ"), ("STTRN", "DOCNUM", "NXTSEQ"),
                             ("GLJNLIT", "VOUCHER", "SEQIT")):
        path = Path(acct) / f"{name}.DBF"
        if not path.is_file():
            continue
        table = dbf.Table(str(path), codepage=schema.CODEPAGE, unicode_errors=schema.UNICODE_ERRORS)
        table.open(dbf.READ_ONLY)
        try:
            if field not in {f.upper() for f in table.field_names}:
                continue
            for rec in schema.live_rows(table):
                if str(getattr(rec, key) or "").strip() in wanted:
                    value = str(getattr(rec, field) or "")
                    if value.strip() and value != value.strip().rjust(len(value)):
                        found.append(f"{name}.{field} {value!r} of {str(getattr(rec, key)).strip()} not right aligned")
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
