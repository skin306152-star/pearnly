# -*- coding: utf-8 -*-
"""写完顺手核对（数字会计员第 3 期第 6 条）：只防写入走样与挂错对方，不防判错。

金额、借贷平、ISVAT 在不在由各写路的 ``_verify`` 核，不符整单回滚；这里在它们通过之后，拿手上的载荷（经写路自己的现金 /
购货 / 应付应收科目换法之后、实际要写的那一份）比本次写入的行：

- 对方码（单据表头 SUPCOD / CUSCOD）；
- 各行科目（GLJNLIT 本凭证的 SEQIT + ACCNUM + 借贷，按多重集合比）；
- 日期（表头 DOCDAT、分录 VOUDAT、税记录 DOCDAT）；单别（表头与税记录的 RECTYP）；参考号（进项 REFNUM / 销项 YOUREF，
  税记录 REFNUM）；
- 税记录的税号与分店（载荷给了分店才比分店）。

另回实际挂到的对方档（APMAS / ARMAS）的税号与分店（``party``），服务端拿票面比（挂错对方）。

只报、不回滚、不重推（「推送不查重不拦」）：回 ``{"state": "ok" | "mismatch", "fields": [...], "party": {tax_id, branch}}``，
随回执 ``meta.verify`` 交回服务端；不带原始行。读本身出错记 ``unreadable``（宁严勿松，交给人看）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import dbf

from . import dbf_schema as S
from .logger import get_logger

log = get_logger(__name__)

STATE_OK = "ok"
STATE_MISMATCH = "mismatch"
# 回执里 fields 的取值（服务端白名单同一组，见 apps/pearnly_cowork/.../push/express/written_check.py `COMPANION_FIELDS`，
# tests/test_companion_contract.py 锁两边一致）
FIELDS = ("party_code", "party_master", "account", "date", "rectyp", "refnum", "vat_tax_id", "vat_branch",
          "pos_date", "unreadable")

# 两条写路各自的表与列
_SIDES = {
    "purchase": {"head": "APTRN", "party": "SUPCOD", "ref": "REFNUM", "master": "APMAS",
                 "vatrec": S.ISVAT_VATREC_INPUT},
    "sales": {"head": "ARTRN", "party": "CUSCOD", "ref": "YOUREF", "master": "ARMAS",
              "vatrec": S.ISVAT_VATREC_OUTPUT},
}


@dataclass
class Expected:
    """手上的载荷经写路换法之后要写的样子。``lines``：本凭证的分录 ``(SEQIT, ACCNUM, 借贷)``；``branch``：载荷里对方的
    分店（空 = 不比分店）；``since``：写前各表的行数（给了就只看之后追加的行，销项单号可能与软删的旧单同号）。"""

    side: str
    docnum: str
    party_code: str
    docdat: object
    rectyp: str
    refnum: str
    lines: Sequence[Tuple[str, str, str]] = ()
    tax_id: str = ""
    branch: str = ""
    has_vat: bool = False
    since: Dict[str, int] = field(default_factory=dict)


def _open(account_dir: Path, name: str):
    t = dbf.Table(str(Path(account_dir) / f"{name}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    return t


def _text(value) -> str:
    return str(value or "").strip()


def _day(value):
    """DBF 日期列与写路的 date 统一成 date 比（datetime 取日期部分）。"""
    if value is None:
        return None
    return value.date() if hasattr(value, "date") and callable(value.date) else value


def _rows(account_dir: Path, table: str, since: int, match) -> List:
    t = _open(account_dir, table)
    try:
        return [r for r in S.live_rows(t) if dbf.recno(r) >= since and match(r)]
    finally:
        t.close()


_MISSING = object()


def _get(row, column: str):
    """取一列；老表没有这一列回 `_MISSING`（不比）。"""
    try:
        return getattr(row, column)
    except Exception:  # noqa: BLE001 — dbf 缺列抛 FieldMissingError
        return _MISSING


def _branch5(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return S.orgnum_to_branch5(digits) if digits else ""


def _compare(account_dir: Path, want: Expected) -> Tuple[List[str], Dict[str, str]]:
    side = _SIDES[want.side]
    since = want.since or {}
    differ: List[str] = []
    heads = _rows(account_dir, side["head"], since.get(side["head"], 0), lambda r: _text(r.DOCNUM) == want.docnum)
    head = heads[0] if heads else None
    party_code = _text(getattr(head, side["party"], "")) if head is not None else ""
    if head is None:
        differ.append("party_code")
    else:
        if party_code != _text(want.party_code):
            differ.append("party_code")
        if _day(head.DOCDAT) != _day(want.docdat):
            differ.append("date")
        if _text(head.RECTYP) != _text(want.rectyp):
            differ.append("rectyp")
        if _text(getattr(head, side["ref"], "")) != _text(want.refnum):
            differ.append("refnum")

    lines = _rows(account_dir, "GLJNLIT", since.get("GLJNLIT", 0), lambda r: _text(r.VOUCHER) == want.docnum)
    written = Counter((_text(r.SEQIT), _text(r.ACCNUM), _text(r.TRNTYP)) for r in lines)
    planned = Counter((_text(a), _text(b), _text(c)) for a, b, c in want.lines)
    if written != planned:
        differ.append("account")
    if any(_day(r.VOUDAT) != _day(want.docdat) for r in lines) and "date" not in differ:
        differ.append("date")

    if want.has_vat:
        vats = _rows(account_dir, "ISVAT", since.get("ISVAT", 0),
                     lambda r: _text(r.DOCNUM) == want.docnum and _text(r.VATREC) == side["vatrec"])
        vat = vats[0] if vats else None
        if vat is not None:
            if _day(vat.DOCDAT) != _day(want.docdat) and "date" not in differ:
                differ.append("date")
            if _text(vat.RECTYP) != _text(want.rectyp) and "rectyp" not in differ:
                differ.append("rectyp")
            if _text(vat.REFNUM) != _text(want.refnum) and "refnum" not in differ:
                differ.append("refnum")
            if _text(vat.TAXID) != _text(want.tax_id):
                differ.append("vat_tax_id")
            wanted_branch, orgnum = _branch5(want.branch), _get(vat, "ORGNUM")
            if wanted_branch and orgnum is not _MISSING and S.orgnum_to_branch5(orgnum) != wanted_branch:
                differ.append("vat_branch")

    party: Dict[str, str] = {}
    if party_code:
        masters = _rows(account_dir, side["master"], 0, lambda r: _text(getattr(r, side["party"], "")) == party_code)
        if masters:
            master = masters[0]
            tax, orgnum = _get(master, "TAXID"), _get(master, "ORGNUM")
            party = {"tax_id": "" if tax is _MISSING else _text(tax),
                     "branch": "" if orgnum is _MISSING else S.orgnum_to_branch5(orgnum)}
        else:
            differ.append("party_master")
    return differ, party


def result(fields: Iterable[str], party: Optional[Dict[str, str]] = None) -> dict:
    """回执里的 ``verify``：字段去重、按 `FIELDS` 的顺序。"""
    found = set(fields or ())
    ordered = [name for name in FIELDS if name in found]
    return {"state": STATE_MISMATCH if ordered else STATE_OK, "fields": ordered, "party": dict(party or {})}


def check(account_dir, want: Expected, extra: Iterable[str] = ()) -> dict:
    """比一张刚写完的单（见文件头）；``extra``：别处已比出的不符（POS 转账凭证，`pos_journal.verify`）。绝不抛。"""
    try:
        differ, party = _compare(Path(account_dir), want)
    except Exception as exc:  # noqa: BLE001 — 核对只报不挡：读不出来就交给人看
        log.warning("write verify unreadable for %s: %s", want.docnum, exc)
        return result(["unreadable", *extra])
    return result([*differ, *extra], party)


__all__ = ["Expected", "FIELDS", "STATE_MISMATCH", "STATE_OK", "check", "result"]
