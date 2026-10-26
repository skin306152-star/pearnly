"""语料 → 草稿单。在测试机上跑（语料只在测试机，只读）；只用标准库 + dbfread。

语料：C:\\PearnlyStress\\语料\\06-录入推送\\有答案\\<公司>_<税号>\\答案\\<Express 账套>\\（会计手录的账 = 标准答案）。
做法：
1. ``survey``：只读列出各公司答案目录里的文件、CSV 表头、DBF 行数，回 JSON（先看清格式再定怎么取）；
2. ``build``：从答案里的 APTRN / ARTRN + STCRD（原表）取赊购 / 现购 / 赊销 / 现销单，筛掉带折扣、合计对不上、
   非标准税率的单，把对方和商品换成试点账套（69EXP\\test）里已有的主档、日期挪进试点账套的会计期间（保留日），
   数量、单价、行数、对方单号照原单 → 草稿单 JSONL。标准答案的税前 / 税 / 含税合计放进 expected，读回时逐字段对。

    python -m pilot.corpus survey  "C:\\PearnlyStress\\语料\\06-录入推送\\有答案" > survey.json
    python -m pilot.corpus build   "C:\\PearnlyStress\\语料\\06-录入推送\\有答案" masters.json --per-type 10 > drafts.jsonl

``masters.json`` 是试点账套主档（执行代理 book_rows 读出的 APMAS / ARMAS / STMAS / ISPRD 行，见 README）。
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import dbfread
from .draft import Draft, Line, money

TYPES = ("RR", "HP", "IV", "HS")
HEAD = {"RR": "APTRN", "HP": "APTRN", "IV": "ARTRN", "HS": "ARTRN"}


def _read(path: Path, since: int = 0) -> List[Dict]:
    return list(dbfread.rows(path.read_bytes(), since))


def survey(root: Path) -> Dict:
    out = []
    for company in sorted(p for p in root.iterdir() if p.is_dir()):
        answers = company / "答案"
        if not answers.is_dir():
            continue
        for book in sorted(p for p in answers.iterdir() if p.is_dir()):
            files = []
            for f in sorted(book.rglob("*")):
                if not f.is_file():
                    continue
                item = {"path": str(f.relative_to(book)), "bytes": f.stat().st_size}
                if f.suffix.lower() == ".csv":
                    with f.open(encoding="utf-8-sig", errors="replace") as fh:
                        item["header"] = next(csv.reader(fh), [])
                elif f.suffix.lower() == ".dbf":
                    with f.open("rb") as fh:
                        item["rows"] = dbfread.count(fh.read(8))
                files.append(item)
            out.append({"company": company.name, "book": book.name, "files": files})
    return {"root": str(root), "books": out}


def _find(book: Path, name: str) -> Optional[Path]:
    for f in book.rglob("*"):
        if f.is_file() and f.name.upper() == f"{name}.DBF":
            return f
    return None


def _text(v) -> str:
    return str(v or "").strip()


def answer_docs(book: Path) -> Iterable[Dict]:
    """一本答案账套里的四种单：表头 + 明细（STCRD 按 SEQNUM），带原单合计。"""
    stcrd = _find(book, "STCRD")
    if not stcrd:
        return
    lines = defaultdict(list)
    for r in _read(stcrd):
        if not r["_del"]:
            lines[_text(r.get("DOCNUM"))].append(r)
    for table in ("APTRN", "ARTRN"):
        path = _find(book, table)
        if not path:
            continue
        party_col, ref_col = ("SUPCOD", "REFNUM") if table == "APTRN" else ("CUSCOD", "YOUREF")
        for h in _read(path):
            docnum = _text(h.get("DOCNUM"))
            kind = docnum[:2].upper()
            if h["_del"] or kind not in TYPES or HEAD[kind] != table or _text(h.get("DOCSTAT")).upper() == "C":
                continue
            rows = sorted(lines.get(docnum, []), key=lambda r: _text(r.get("SEQNUM")).rjust(5))
            yield {"book": book.name, "docnum": docnum, "type": kind, "date": h.get("DOCDAT"),
                   "party": _text(h.get(party_col)), "ref": _text(h.get(ref_col)), "flgvat": _text(h.get("FLGVAT")),
                   "net": money(h.get("NETVAL") or 0), "vat": money(h.get("VATAMT") or 0),
                   "total": money(h.get("NETAMT") or 0), "disc": money(h.get("DISCAMT") or 0),
                   "lines": [{"item": _text(r.get("STKCOD")), "qty": r.get("TRNQTY") or 0,
                              "price": r.get("UNITPR") or 0, "amount": money(r.get("TRNVAL") or 0),
                              "disc": money(r.get("DISCAMT") or 0)} for r in rows]}


def usable(doc: Dict) -> str:
    """能不能照录：返回空串 = 可以；否则返回原因（报告里统计「语料里多少单不适合」）。"""
    if not doc["lines"]:
        return "没有明细"
    if doc["disc"] or any(l["disc"] for l in doc["lines"]):
        return "带折扣"
    if len(doc["lines"]) > 15:
        return "超过 15 行"
    gross = Decimal(0)
    for l in doc["lines"]:
        qty, price = Decimal(str(l["qty"])), Decimal(str(l["price"]))
        if qty <= 0 or price <= 0 or money(qty * price) != l["amount"]:
            return "行金额不等于数量 × 单价"
        gross += l["amount"]
    if doc["flgvat"] == "1":  # 价含税
        if gross != doc["total"] or doc["vat"] != money(gross * 7 / Decimal(107)):
            return "合计或税额对不上（含税价）"
    elif doc["flgvat"] == "2":
        if gross != doc["net"] or doc["vat"] != money(gross * Decimal("0.07")):
            return "合计或税额对不上（不含税价）"
    else:
        return "无税或特殊税率"
    return ""


def _pick(pool: List[str], key: str) -> str:
    return pool[int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % len(pool)]


def build(root: Path, masters: Dict, per_type: int) -> List[Draft]:
    suppliers = sorted(_text(r.get("SUPCOD")) for r in masters["APMAS"] if not r.get("_del") and r.get("SUPCOD"))
    customers = sorted(_text(r.get("CUSCOD")) for r in masters["ARMAS"] if not r.get("_del") and r.get("CUSCOD"))
    items = sorted(_text(r.get("STKCOD")) for r in masters["STMAS"]
                   if not r.get("_del") and r.get("STKCOD") and _text(r.get("STKTYP")) in ("0", ""))
    if not (suppliers and customers and items):
        raise SystemExit("试点账套主档不够（要有供应商、客户、库存品各至少 1 个）")
    period = masters["period"]  # {"beg": "2026-01-01", "end": "2026-12-31"}：试点账套能录的日期范围
    beg, end = dt.date.fromisoformat(period["beg"]), dt.date.fromisoformat(period["end"])
    picked: Dict[str, List[Draft]] = {t: [] for t in TYPES}
    skipped: Dict[str, int] = defaultdict(int)
    for company in sorted(p for p in root.iterdir() if p.is_dir()):
        for book in sorted(p for p in (company / "答案").glob("*") if p.is_dir()):
            for doc in answer_docs(book):
                if len(picked[doc["type"]]) >= per_type:
                    continue
                why = usable(doc)
                if why:
                    skipped[why] += 1
                    continue
                day = doc["date"] if isinstance(doc["date"], dt.date) else beg
                month = min(max(beg.month, day.month), end.month) if beg.year == end.year else beg.month
                date = dt.date(beg.year, month, min(day.day, 28))
                key = f"{company.name}/{doc['docnum']}"
                pool = suppliers if doc["type"] in ("RR", "HP") else customers
                lines = [Line(item_code=_pick(items, f"{company.name}/{l['item']}"),
                              qty=str(Decimal(str(l["qty"])).normalize()),
                              unit_price=str(money(l["price"]))) for l in doc["lines"]]
                picked[doc["type"]].append(Draft(
                    doc_id=f"{doc['type']}-{hashlib.sha1(key.encode()).hexdigest()[:8]}", doc_type=doc["type"],
                    date=date.isoformat(), party_code=_pick(pool, f"{company.name}/{doc['party']}"), lines=lines,
                    ref_no=doc["ref"][:15] if doc["type"] in ("RR", "HP") else "",
                    vat_included=doc["flgvat"] == "1",
                    expected={"net": str(doc["net"]), "vat": str(doc["vat"]), "total": str(doc["total"])},
                    source={"company": company.name, "book": doc["book"], "docnum": doc["docnum"]}))
    sys.stderr.write(json.dumps({"picked": {t: len(v) for t, v in picked.items()}, "skipped": skipped},
                                ensure_ascii=False) + "\n")
    return [d for t in TYPES for d in picked[t]]


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["survey"]:
        print(json.dumps(survey(Path(args[1])), ensure_ascii=False, indent=1, default=str))
        return 0
    if args[:1] == ["build"]:
        per = int(args[args.index("--per-type") + 1]) if "--per-type" in args else 10
        masters = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        for d in build(Path(args[1]), masters, per):
            print(json.dumps(d.to_json(), ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
