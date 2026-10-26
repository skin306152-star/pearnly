"""录完读回核对：从账套「这张单录之前的行数」往后找新行，跟草稿单逐字段比。

金额口径按 Express 真单（companion/dbf_fields.py）：NETVAL = 税前、VATAMT = 税、NETAMT = 含税，赊单现单都一样。
明细看 STCRD（按 SEQNUM 顺序）：品码、仓、数量、单价、行金额。有税的单另看 ISVAT 的 AMT01 / VAT01。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Dict, List

from .draft import Draft, money

HEAD_TABLES = ("APTRN", "ARTRN")
BOOK_TABLES = ("APTRN", "ARTRN", "STCRD", "ISVAT", "GLJNL", "GLJNLIT", "ISRUN")


def _text(value) -> str:
    return str(value or "").strip()


def _num(value) -> Decimal:
    return money(value or 0)


def _day(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value:
        return dt.date.fromisoformat(value[:10])
    return None


def find_new_doc(draft: Draft, new_rows: Dict[str, List[Dict]]) -> Dict:
    """在新增行里找这张单的表头。返回 {"head": 行或 None, "extra_heads": [...]}。

    被负库存拦下时 Express 会留一张金额 0 的空单头、占一个号，这种也算「多出来的单头」报出来。
    """
    heads = [r for r in new_rows.get(draft.spec["head"], []) if not r.get("_del")
             and _text(r.get("DOCNUM")).upper().startswith(draft.doc_type)]
    if not heads:
        return {"head": None, "extra_heads": []}
    real = [r for r in heads if _num(r.get("NETAMT")) != 0] or heads
    return {"head": real[-1], "extra_heads": [_text(r.get("DOCNUM")) for r in heads if r is not real[-1]]}


def compare(draft: Draft, new_rows: Dict[str, List[Dict]]) -> Dict:
    """返回 {"ok": bool, "docnum": str, "diffs": [{"field", "want", "got"}], "notes": [...]}。"""
    found = find_new_doc(draft, new_rows)
    head = found["head"]
    diffs: List[Dict] = []
    notes: List[str] = []
    if head is None:
        return {"ok": False, "docnum": "", "diffs": [{"field": "head", "want": draft.doc_type, "got": "没找到新单"}],
                "notes": notes}
    docnum = _text(head.get("DOCNUM"))
    if found["extra_heads"]:
        diffs.append({"field": "extra_heads", "want": "[]", "got": ",".join(found["extra_heads"])})

    def check(name, want, got):
        if want != got:
            diffs.append({"field": name, "want": str(want), "got": str(got)})

    check("party", draft.party_code, _text(head.get(draft.spec["party"])))
    check("date", dt.date.fromisoformat(draft.date), _day(head.get("DOCDAT")))
    if draft.ref_no:
        check("ref", draft.ref_no, _text(head.get(draft.spec["ref"])))
    want = draft.totals()
    check("net", want["net"], _num(head.get("NETVAL")))
    check("vat", want["vat"], _num(head.get("VATAMT")))
    check("total", want["total"], _num(head.get("NETAMT")))

    lines = sorted((r for r in new_rows.get("STCRD", []) if not r.get("_del") and _text(r.get("DOCNUM")) == docnum),
                   key=lambda r: (_text(r.get("SEQNUM")).rjust(5), r.get("_row", 0)))
    check("line_count", len(draft.lines), len(lines))
    for index, (line, got) in enumerate(zip(draft.lines, lines), start=1):
        check(f"line{index}.item", line.item_code, _text(got.get("STKCOD")))
        if line.loc:
            check(f"line{index}.loc", line.loc, _text(got.get("LOCCOD")))
        check(f"line{index}.qty", Decimal(line.qty).normalize(), Decimal(str(got.get("TRNQTY") or 0)).normalize())
        check(f"line{index}.price", money(line.unit_price), _num(got.get("UNITPR")))
        check(f"line{index}.amount", line.amount, _num(got.get("TRNVAL")))

    if want["vat"] != 0:
        vat_rows = [r for r in new_rows.get("ISVAT", []) if not r.get("_del") and _text(r.get("DOCNUM")) == docnum]
        if not vat_rows:
            diffs.append({"field": "isvat", "want": "1 行", "got": "没有"})
        else:
            check("isvat.amt", want["net"], _num(vat_rows[-1].get("AMT01")))
            check("isvat.vat", want["vat"], _num(vat_rows[-1].get("VAT01")))
    else:
        notes.append("无税单，不查 ISVAT")
    return {"ok": not diffs, "docnum": docnum, "diffs": diffs, "notes": notes}
