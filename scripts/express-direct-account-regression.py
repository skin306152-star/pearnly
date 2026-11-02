#!/usr/bin/env python3
"""CC 直接科目行照原生先有「编码 = 科目码」的商品（`product_master.ensure_account_item`，11-02）的真账副本离线回归。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码写两张现购 HP，明细是直接科目行（科目 = 这本账套的默认采购科目）：
- 第一张：主档里有且只有一件编码 = 这个科目码的商品；这本账套原来没有就是本次建的：非库存 3 型、ACCCOD 经 ISACC 联到同一科目；
  原来就有（会计建的 3 / 4 / 5 型）就照用、不动。明细这一行 STKCOD 就是它、按非库存商品行写（XTRNVAL = 净额）；
- 第二张：不再建，还是那一件；
- 同码已是库存品（0 型）的账套：CC 回 ``ACCOUNT_CODE_IS_STOCK_ITEM``、整本逐字节不变；
- 写入集合以外的表逐字节不变；给了 ``--runner``：照正式 CC 重建索引，并核写入集合的 CDX 与 DBF 一致（``express_cdx_check``）。
账套没有 ISPRD、读不出默认采购 / 进项税科目、没有现金科目或供应商时跳过并记原因。源账套只读（只 copytree 出来），工作副本用完删掉。

    python3 scripts/express-direct-account-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_op_lab\\da_reg \\
        --companion <仓库>\\companion\\src --out result.jsonl [--runner C:\\Users\\skin3\\AppData\\Local\\Programs\\CC\\cdx_reindex_runner.exe]
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

WRITE_SET = {"ISRUN", "APTRN", "ISVAT", "GLJNL", "GLJNLIT", "APBAL", "APMAS", "STCRD", "STBAL", "STMAS", "ISACC"}
NAME = "REG ค่าบริการ 2101"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--pattern", default="T*")
    parser.add_argument("--work", required=True)
    parser.add_argument("--companion", required=True, help="companion/src 目录")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--runner", default="", help="已安装 CC 的 cdx_reindex_runner.exe：给了就重建索引并核 CDX 与 DBF 一致")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 测试机控制台是 cp874
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    global dbf, S, W, P, account_probe, fiscal_period, cdx_reindex, cdx_check, with_runner
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, fiscal_period  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_writer as W  # noqa: F401
    from companion import product_master as P  # noqa: F401
    import express_cdx_check as cdx_check  # noqa: F401
    with_runner = cdx_check.use_runner(cdx_reindex, args.runner)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    P.BACKFILL_FILE = work / "account_items.json"  # 本机补建标记放工作目录，不碰这台电脑 CC 的
    books = sorted(path for path in Path(args.source).iterdir() if path.is_dir() and fnmatch.fnmatch(path.name, args.pattern))
    if args.limit:
        books = books[:args.limit]
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


def digests(acct, skip=WRITE_SET):
    return {p.name.upper(): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(acct).iterdir()
            if p.is_file() and p.stem.upper() not in skip}


def items(acct, code):
    return [r for r in rows(acct, "STMAS", ("STKCOD", "STKTYP", "ACCCOD", "CREBY")) if text(r["STKCOD"]) == code]


def payload(acct, docdate, ref, accounts, cash, supplier):
    amount = Decimal("1000.00")
    vat = (amount * Decimal("0.07")).quantize(Decimal("0.01"))
    total = amount + vat
    line = accounts["fallback_acc"]
    return {"doctype": "HP", "account_set": Path(acct).name, "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
            "ref_no": ref, "vat_rate": 7.0, "base_amount": f"{amount:.2f}", "vat_amount": f"{vat:.2f}", "total_amount": f"{total:.2f}",
            "supplier": {"code": supplier, "name": "", "tax_id": "", "prename": "", "supplier_new": False},
            "lines": [{"acc": line, "side": "D", "amount": f"{amount:.2f}", "desc": "ซื้อ"},
                      {"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"},
                      {"acc": cash, "side": "C", "amount": f"{total:.2f}", "desc": "เงินสด"}],
            "items": [{"name": NAME, "qty": "1", "unit_price": f"{total:.2f}", "amount": f"{amount:.2f}", "unit": "",
                       "item_mode": "direct_account"}],
            "items_status": "ok", "items_account": line}


def check(acct: Path) -> dict:
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    accounts = account_probe.probe_default_accounts(acct) or {}
    if not accounts.get("fallback_acc") or not accounts.get("vat_input_acc"):
        return {"state": "skip", "why": "no default purchase / input VAT account"}
    cash = text((rows(acct, "ISINFO", ("ACCNUM00",)) or [{}])[0].get("ACCNUM00"))
    supplier = text((rows(acct, "APMAS", ("SUPCOD",)) or [{}])[0].get("SUPCOD"))
    if not cash or not supplier:
        return {"state": "skip", "why": "no cash account or supplier"}
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    docdate = f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"
    code = accounts["fallback_acc"]
    before = items(acct, code)
    files_before = digests(acct)
    whole_before = digests(acct, skip=set())
    index_before = cdx_check.snapshot(cdx_reindex, acct, WRITE_SET) if with_runner else {}
    write = lambda ref: W.write_purchase(payload(acct, docdate, ref, accounts, cash, supplier), acct,
                                         allowed_sets=(acct.name,), selected_dir=str(acct), make_backup=True)
    if before and text(before[0]["STKTYP"]) not in P._NONSTOCK_TYPES:
        res = write("REG-DA-1")
        if res.ok or res.error_code != P.ERR_ACCOUNT_ITEM_STOCK:
            return {"state": "fail", "why": f"a stock item has code {code} but the write gave {res.ok} {res.error_code}"}
        if digests(acct, skip=set()) != whole_before:
            return {"state": "fail", "why": "the refused HP changed the book"}
        return {"state": "ok", "case": "stock_item_refused", "account": code}
    one = write("REG-DA-1")
    if not one.ok:
        return {"state": "fail", "why": f"first HP: {one.error_code} {one.error}"[:300]}
    after = items(acct, code)
    if len(after) != 1:
        return {"state": "fail", "why": f"{len(after)} items with code {code}"}
    item = after[0]
    if before:
        if (text(item["STKTYP"]), text(item["ACCCOD"])) != (text(before[0]["STKTYP"]), text(before[0]["ACCCOD"])):
            return {"state": "fail", "why": f"the existing item {code} was changed"}
        case = "reused_" + text(item["STKTYP"])
    else:
        link = [r for r in rows(acct, "ISACC", ("ACCCOD", "ACCNUM01")) if text(r["ACCCOD"]) == text(item["ACCCOD"])]
        if text(item["STKTYP"]) != "3" or not link or text(link[0]["ACCNUM01"]) != code:
            return {"state": "fail", "why": f"made item {code}: STKTYP {item['STKTYP']!r} link {link[:1]}"}
        case = "made"
    line = [r for r in rows(acct, "STCRD", ("DOCNUM", "STKCOD", "XTRNVAL", "NETVAL")) if text(r["DOCNUM"]) == one.docnum]
    if len(line) != 1 or text(line[0]["STKCOD"]) != code or float(line[0]["XTRNVAL"]) != float(line[0]["NETVAL"]):
        return {"state": "fail", "why": f"line {line}"}
    two = write("REG-DA-2")
    if not two.ok:
        return {"state": "fail", "why": f"second HP: {two.error_code} {two.error}"[:300]}
    if len(items(acct, code)) != 1:
        return {"state": "fail", "why": "the second HP made another item"}
    index_problems = cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, [one.docnum, two.docnum]) if with_runner else []
    if index_problems:
        return {"state": "fail", "why": "; ".join(index_problems)}
    changed = sorted(name for name, digest in digests(acct).items() if files_before.get(name) != digest)
    if changed:
        return {"state": "fail", "why": f"tables outside the write set changed: {changed}"}
    return {"state": "ok", "case": case, "account": code, "docs": [one.docnum, two.docnum], "cdx_checked": sorted(index_before)}


if __name__ == "__main__":
    sys.exit(main())
