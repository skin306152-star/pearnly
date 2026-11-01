# -*- coding: utf-8 -*-
"""读账套（只读）：开录前记下各表行数，录完 / 续录时只读新增的行；逐字段核对；找「这张单存没存」。

要读的表先复制到本机临时目录再读（``express_read.snapshot``，Express 开着账套照样能读），账套里的文件一个字节都不动。
行数只读表头 8 个字节（不拷整表）。金额口径同直写（``dbf_fields``）：NETVAL 税前、VATAMT 税、NETAMT 含税；明细看 STCRD，
有税的单另看 ISVAT 的 AMT01 / VAT01。
"""
from __future__ import annotations

import datetime as dt
import struct
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Protocol

from .draft import DOC_TYPES, Draft, money

DOC_TABLES = ("APTRN", "ARTRN", "STCRD", "ISVAT")


class Book(Protocol):
    def counts(self, tables: Iterable[str] = DOC_TABLES) -> Dict[str, int]: ...

    def rows_since(self, since: Dict[str, int], tables: Iterable[str] = DOC_TABLES) -> Dict[str, List[Dict]]: ...

    def codes(self, table: str, column: str) -> set: ...


def _plain(value):
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return value.rstrip()
    return value


def _field(rec, column: str):
    """真账套里偶有坏数字（如数值栏只存了「.」，10-26 实机 69EXP\\test）：读不出的栏给 None，不让整张表读不了。"""
    try:
        return _plain(rec[column])
    except Exception:  # noqa: BLE001
        return None


class DbfBook:
    """真账套。``account_dir`` 是这本账套的文件夹（批次开录前已核对是这家公司的这本）。"""

    def __init__(self, account_dir):
        self.dir = Path(account_dir)

    def _path(self, name: str) -> Optional[Path]:
        for entry in self.dir.iterdir():
            if entry.name.upper() == f"{name}.DBF":
                return entry
        return None

    def counts(self, tables: Iterable[str] = DOC_TABLES) -> Dict[str, int]:
        out = {}
        for name in tables:
            path = self._path(name)
            if path is not None:
                with open(path, "rb") as reader:
                    out[name] = struct.unpack("<I", reader.read(8)[4:8])[0]
        return out

    def rows_since(self, since: Dict[str, int], tables: Iterable[str] = DOC_TABLES) -> Dict[str, List[Dict]]:
        import dbf

        from .. import dbf_schema as S
        from ..express_read import snapshot

        tables = list(tables)
        out: Dict[str, List[Dict]] = {}
        with snapshot(self.dir, tables=tables) as (folder, _changed):
            for name in tables:
                path = Path(folder) / f"{name}.DBF"
                if not path.is_file():
                    continue
                table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
                table.open(dbf.READ_ONLY)
                try:
                    columns = [str(c).upper() for c in table.field_names]
                    rows = []
                    for index, rec in enumerate(table):
                        if index < since.get(name, 0):
                            continue
                        row = {c: _field(rec, c) for c in columns}
                        row.update({"_row": index, "_del": bool(dbf.is_deleted(rec))})
                        rows.append(row)
                    out[name] = rows
                finally:
                    table.close()
        return out

    def codes(self, table: str, column: str) -> set:
        rows = self.rows_since({}, [table]).get(table, [])
        return {str(r.get(column) or "").strip() for r in rows if not r.get("_del")}


# ── 核对 ──────────────────────────────────────────────────────────────────
def _text(value) -> str:
    return str(value or "").strip()


def _num(value) -> Decimal:
    return money(value or 0)


def _qty(value) -> str:
    text = f"{Decimal(str(value or 0)):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _day(value) -> Optional[dt.date]:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value:
        return dt.date.fromisoformat(value[:10])
    return None


def heads_for(draft: Draft, rows: Dict[str, List[Dict]]) -> List[Dict]:
    spec = DOC_TYPES[draft.doc_type]
    return [r for r in rows.get(spec["head"], []) if not r.get("_del")
            and _text(r.get("DOCNUM")).upper().startswith(draft.doc_type)]


def find_saved(draft: Draft, rows: Dict[str, List[Dict]]) -> Dict:
    """断点续录：在这张单开录之后新增的行里找它。回 ``{"docnum": 已存的单号或空, "placeholders": [金额 0 的空单头]}``。

    按对方码 + 日期 +（有的话）对方单号 + 含税合计认；录到明细时 Express 会先写一张金额 0 的单头占号，保存才填金额——
    崩在中间就留下这种空单头，要人去作废（不自动改已存的单）。"""
    spec = DOC_TYPES[draft.doc_type]
    want = draft.totals()["total"]
    docnum, placeholders = "", []
    for head in heads_for(draft, rows):
        same_party = _text(head.get(spec["party"])) == draft.party_code
        same_day = _day(head.get("DOCDAT")) == draft.day
        same_ref = not draft.ref_no or _text(head.get(spec["ref"])) == draft.ref_no
        if not (same_party and same_day and same_ref):
            continue
        total = _num(head.get("NETAMT"))
        if total == want:
            docnum = _text(head.get("DOCNUM"))
        elif total == 0:
            placeholders.append(_text(head.get("DOCNUM")))
    return {"docnum": docnum, "placeholders": [p for p in placeholders if p != docnum]}


def compare(draft: Draft, rows: Dict[str, List[Dict]], docnum: str = "", *, check_ref: bool = True) -> Dict:
    """录完逐字段核对。回 ``{"ok", "docnum", "diffs": [{"field", "want", "got"}]}``。
    ``check_ref``：这种表单的配方填不填对方单号（销项的「อ้างอิง」是哪一栏还没在服务器版上录样本核过，评估 §16）；不填就不比。"""
    spec = DOC_TYPES[draft.doc_type]
    heads = heads_for(draft, rows)
    if docnum:
        heads = [h for h in heads if _text(h.get("DOCNUM")) == docnum] or heads
    real = [h for h in heads if _num(h.get("NETAMT")) != 0] or heads
    if not real:
        return {"ok": False, "docnum": "", "diffs": [{"field": "head", "want": draft.doc_type, "got": ""}]}
    head = real[-1]
    docnum = _text(head.get("DOCNUM"))
    diffs: List[Dict] = []

    def check(name, want, got):
        if want != got:
            diffs.append({"field": name, "want": str(want), "got": str(got)})

    extra = [_text(h.get("DOCNUM")) for h in heads if h is not head and _num(h.get("NETAMT")) == 0]
    if extra:
        diffs.append({"field": "placeholders", "want": "", "got": ",".join(extra)})
    check("party", draft.party_code, _text(head.get(spec["party"])))
    check("date", draft.day, _day(head.get("DOCDAT")))
    if draft.ref_no and check_ref:
        check("ref", draft.ref_no, _text(head.get(spec["ref"])))
    want = draft.totals()
    check("net", want["net"], _num(head.get("NETVAL")))
    check("vat", want["vat"], _num(head.get("VATAMT")))
    check("total", want["total"], _num(head.get("NETAMT")))
    lines = sorted((r for r in rows.get("STCRD", []) if not r.get("_del") and _text(r.get("DOCNUM")) == docnum),
                   key=lambda r: (_text(r.get("SEQNUM")).rjust(5), r.get("_row", 0)))
    check("line_count", len(draft.lines), len(lines))
    for index, (line, got) in enumerate(zip(draft.lines, lines), start=1):
        check(f"line{index}.item", line.item_code, _text(got.get("STKCOD")))
        if line.loc:
            check(f"line{index}.loc", line.loc, _text(got.get("LOCCOD")))
        check(f"line{index}.qty", _qty(line.qty), _qty(got.get("TRNQTY")))
        check(f"line{index}.price", money(line.unit_price), _num(got.get("UNITPR")))
        check(f"line{index}.amount", line.amount, _num(got.get("TRNVAL")))
    if want["vat"] != 0:
        vat_rows = [r for r in rows.get("ISVAT", []) if not r.get("_del") and _text(r.get("DOCNUM")) == docnum]
        # 有免税行时进项税记录的应税基数只记应税部分、免税部分记 AMTRAT0（原生，语料 158 本；10-29 TEST2 实测 RR0000086 / 087
        # 原来按整张税前比被误判不符）
        exempt = sum((line.amount for line in draft.lines if line.exempt), Decimal(0))
        if not vat_rows:
            diffs.append({"field": "isvat", "want": "1", "got": "0"})
        else:
            check("isvat.amt", want["net"] - exempt, _num(vat_rows[-1].get("AMT01")))
            check("isvat.vat", want["vat"], _num(vat_rows[-1].get("VAT01")))
            if exempt:
                check("isvat.exempt", exempt, _num(vat_rows[-1].get("AMTRAT0")))
    return {"ok": not diffs, "docnum": docnum, "diffs": diffs}
