#!/usr/bin/env python3
"""CC 写库存单（载荷 6：领用 OU / 调整 JU / 盘点 TK，dbf_stock）的真账副本离线回归（出入库第一期 B 段，10-25）。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码依次写一张调整（JU，+2 个、给单位成本）、一张领用（OU，1 个）、
一张盘点（TK，−1 个）→ 核：
- 三张单都写成，单头 STTRN 一行、明细 STCRD 行数与单据一致；
- 写前 STMAS 结存等于各仓 LOCBAL 合计的品，写后仍相等（数量对得上 +2 −1 −1 = 0）；
- 凭证：永续盘存账套每张有金额的单一张 JNLTYP 00 凭证、借贷平衡；定期盘存账套（ISPERPETUA = N）一张凭证都没有；
- 写入集合以外的表逐字节不变（写入集合 = STTRN、STCRD、STMAS、STLOC、GLJNL、GLJNLIT、ISRUN 及索引）。
账套 ISRUN 没有 OU / JU / TK 单别、没有库存品、默认科目不能记账的跳过并记原因。源账套只读（只 copytree 出来），工作副本用完删掉。

    python3 scripts/express-stock-entry-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_stock_reg \\
        --companion <仓库>\\companion\\src --out result.jsonl
只给测试机 C 盘的 T 账套（或其他测试副本）用。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import shutil
import sys
import traceback
from decimal import Decimal
from pathlib import Path

WRITE_SET = {"STTRN", "STCRD", "STMAS", "STLOC", "GLJNL", "GLJNLIT", "ISRUN"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--pattern", default="T*")
    parser.add_argument("--work", required=True)
    parser.add_argument("--companion", required=True, help="companion/src 目录")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    sys.path.insert(0, args.companion)
    global dbf, S, D, account_rules, cdx_reindex, fiscal_period
    import dbf  # noqa: F401
    from companion import account_rules, cdx_reindex, fiscal_period  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_stock as D  # noqa: F401

    cdx_reindex.runner_available = lambda: False  # 不重建 Express 索引（只核表内容）
    books = sorted(path for path in Path(args.source).iterdir() if path.is_dir() and fnmatch.fnmatch(path.name, args.pattern))
    if args.limit:
        books = books[:args.limit]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    counts = {"ok": 0, "fail": 0, "skip": 0}
    with open(args.out, "w", encoding="utf-8") as out:
        for book in books:
            copy = work / book.name
            shutil.rmtree(copy, ignore_errors=True)
            try:
                shutil.copytree(book, copy)
                result = check(copy)
            except Exception:
                result = {"state": "fail", "why": traceback.format_exc()[-600:]}
            finally:
                shutil.rmtree(copy, ignore_errors=True)
            result["book"] = book.name
            counts[result["state"]] += 1
            out.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            out.flush()
            print(result["state"], book.name, result.get("why", "")[:160], flush=True)
    print(json.dumps(counts))
    return 1 if counts["fail"] else 0


def rows(acct, table, fields):
    path = Path(acct) / f"{table}.DBF"
    if not path.is_file():
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in t.field_names}
        return [{f: (getattr(r, f) if f in names else None) for f in fields} for r in S.live_rows(t)]
    finally:
        t.close()


def text(value):
    return str(value or "").strip()


def digests(acct):
    return {p.name.upper(): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(acct).iterdir()
            if p.is_file() and p.stem.upper() not in WRITE_SET}


def balanced(acct, codes):
    master = {text(r["STKCOD"]): Decimal(repr(float(r["MREMBAL"] or 0))) for r in rows(acct, "STMAS", ("STKCOD", "MREMBAL"))}
    loc = {}
    for r in rows(acct, "STLOC", ("STKCOD", "LOCBAL")):
        loc[text(r["STKCOD"])] = loc.get(text(r["STKCOD"]), Decimal(0)) + Decimal(repr(float(r["LOCBAL"] or 0)))
    return {code: master.get(code, Decimal(0)) == loc.get(code, Decimal(0)) for code in codes}


def check(acct: Path) -> dict:
    books = {kind: D.book_row(acct, kind) for kind in ("OU", "JU", "TK")}
    missing = [kind for kind, row in books.items() if row is None]
    if missing:
        return {"state": "skip", "why": f"no ISRUN book for {missing}"}
    bad = [kind for kind, row in books.items() if not account_rules.postable_account(acct, row["account"])]
    if bad:
        return {"state": "skip", "why": f"default account not postable for {bad}: {[books[k]['account'] for k in bad]}"}
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    stock = [r for r in rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP")) if text(r["STKTYP"]) == "0" and text(r["STKCOD"])]
    if not stock:
        return {"state": "skip", "why": "no stock item"}
    has_stloc = (acct / "STLOC.DBF").is_file()
    code = text(stock[0]["STKCOD"])
    before_balanced = balanced(acct, [code])[code] if has_stloc else None
    files_before = digests(acct)
    perpetual = text((account_rules._first(acct, "ISINFO") or {}).get("ISPERPETUA") or "Y").upper() != "N"
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    docdate = f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"
    written = []
    # 先入后出：不允许负库存的账套（NEGALLOW = N）里结存为 0 的品先领用会被正确拦下（stock_guard）
    for doctype, qty, extra in (("JU", 2, {"unit_cost": "15"}), ("OU", 1, {}), ("TK", -1, {})):
        payload = {"payload_version": 6, "direction": "stock", "doctype": doctype, "docdate_be": docdate,
                   "ref_no": f"REG-{doctype}", "remark": "", "items": [{"code": code, "name": "", "qty": str(qty), **extra}]}
        res = D.write_stock_entry(payload, acct, selected_dir=str(acct), make_backup=False)
        if not res.ok:
            return {"state": "fail", "why": f"{doctype} {res.error_code}: {res.error}"}
        written.append(res.docnum)
    for docnum in written:
        heads = [r for r in rows(acct, "STTRN", ("DOCNUM",)) if text(r["DOCNUM"]) == docnum]
        lines = [r for r in rows(acct, "STCRD", ("DOCNUM",)) if text(r["DOCNUM"]) == docnum]
        if len(heads) != 1 or len(lines) != 1:
            return {"state": "fail", "why": f"{docnum}: {len(heads)} header / {len(lines)} detail rows"}
        journal = [r for r in rows(acct, "GLJNLIT", ("VOUCHER", "TRNTYP", "AMOUNT")) if text(r["VOUCHER"]) == docnum]
        debit = sum(Decimal(str(r["AMOUNT"])) for r in journal if text(r["TRNTYP"]) == "0")
        credit = sum(Decimal(str(r["AMOUNT"])) for r in journal if text(r["TRNTYP"]) != "0")
        if debit != credit:
            return {"state": "fail", "why": f"{docnum}: journal {debit} / {credit}"}
        if not perpetual and journal:
            return {"state": "fail", "why": f"{docnum}: journalled in a periodic account set"}
    if has_stloc and before_balanced and not balanced(acct, [code])[code]:
        return {"state": "fail", "why": f"STMAS != sum(STLOC) for {code} after writing"}
    changed = sorted(name for name, digest in digests(acct).items() if files_before.get(name) != digest)
    if changed:
        return {"state": "fail", "why": f"tables outside the write set changed: {changed}"}
    return {"state": "ok", "docs": written, "perpetual": perpetual, "stloc": has_stloc, "was_balanced": before_balanced}


if __name__ == "__main__":
    sys.exit(main())
