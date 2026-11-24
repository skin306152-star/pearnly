#!/usr/bin/env python3
"""CC 写 Express 后 STLOC（各仓结存）的真账副本离线回归（出入库第一期，10-25）。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码写一张**不带仓库码**的进货（RR，库存品 5 个）和一张销货（IV，
2 个），就是 Cowork 推送的样子（``--cash``：现购 HP / 现销 HS，价含税）→ 核：
- 主仓（ISINFO.MAINLOC）那一行 LOCBAL 先 +5 再 −2，期桶 QTY{期} 同样增减，最后进货 / 销货 / 变动日记上；
- 新写的明细照 Express 原生记法：SEQNUM 右对齐、TQUCOD 不空、TFACTOR > 0（同账套原生明细的行号写法一并统计）；
- STLOC 其他行一行不变；没被写的表逐字节不变（写入集合 = 进货 / 销货备份表 + STLOC，另有索引文件）；
- 写前这件品 STMAS.TOTBAL（总结存）等于各仓 LOCBAL 合计的，写后仍相等（Express 的「STMAS = Σ STLOC」；11-07 前误核 MREMBAL）；
- 物料组的存货科目不在科目表（``ITEM_GROUP_ACCOUNT_MISSING``，原生 1155 拒存）时：重拷副本按正式路径（带写前备份）再写一次，
  必须同样拒写、账套逐字节不变，记跳过；
- 给了 ``--runner``：照正式 CC 重建索引，并核写过的表 CDX 与 DBF 一致（``express_cdx_check``）。
源账套只读（只 copytree 出来）；工作副本用完删掉。不连网络、不碰服务器。

    python3 scripts/express-stloc-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_stloc_reg \\
        --companion <仓库>\\companion\\src --out result.jsonl
只给测试机 C 盘的 T 账套（或其他测试副本）用；--source 指向局域网真账也只读，但别这么做（拷贝大、占带宽）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import shutil
import sys
import traceback
from decimal import Decimal
from pathlib import Path

SOURCES = {}  # 工作副本名 → 源账套（拒写时重拷一份，按正式路径带写前备份再写一次）
CASH = False  # --cash：写现购 HP / 现销 HS


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--pattern", default="T*")
    parser.add_argument("--work", required=True)
    parser.add_argument("--companion", required=True, help="companion/src 目录")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--runner", default="", help="已安装 CC 的 cdx_reindex_runner.exe：给了就重建索引并核 CDX 与 DBF 一致")
    parser.add_argument("--cash", action="store_true", help="写现购 HP / 现销 HS（价含税，FLGVAT 1）代替赊购 RR / 赊销 IV")
    args = parser.parse_args(argv)
    global CASH
    CASH = args.cash
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    global dbf, S, W, WS, fiscal_period, account_probe, cdx_reindex
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, fiscal_period  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_sales as WS  # noqa: F401
    from companion import dbf_writer as W  # noqa: F401

    global cdx_check, with_runner
    import express_cdx_check as cdx_check  # noqa: F401
    global native_check
    import express_native_detail_check as native_check  # noqa: F401
    with_runner = cdx_check.use_runner(cdx_reindex, args.runner)
    books = sorted(path for path in Path(args.source).iterdir() if path.is_dir() and fnmatch.fnmatch(path.name, args.pattern))
    if args.limit:
        books = books[:args.limit]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    # 正式路径的写前备份放进工作目录，不进这台电脑 CC 自己的备份目录
    os.environ["PEARNLY_WRITE_BACKUP_DIR"] = str(work / "_write_backups")
    SOURCES.update({book.name: book for book in books})
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
    path = acct / f"{table}.DBF"
    if not path.is_file():
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        out = []
        for rec in S.live_rows(t):
            item = {}
            for field in fields:
                try:
                    value = rec[field]
                except Exception:
                    value = None
                item[field] = value.strip() if isinstance(value, str) else value
            out.append(item)
        return out
    finally:
        t.close()


def stloc_rows(acct):
    path = acct / "STLOC.DBF"
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        names = list(t.field_names)
        out = []
        for rec in S.live_rows(t):
            item = {}
            for name in names:
                try:
                    item[name] = rec[name]
                except Exception:
                    item[name] = None
            out.append(item)
        return out
    finally:
        t.close()


def key(row):
    return (str(row.get("STKCOD") or "").strip(), str(row.get("LOCCOD") or "").strip())


def digests(acct):
    return {path.name.upper(): hashlib.sha256(path.read_bytes()).hexdigest() for path in acct.iterdir()
            if path.is_file() and path.suffix.upper() in (".DBF", ".FPT")}


def be(day: dt.date) -> str:
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def check(acct: Path) -> dict:
    if not (acct / "STLOC.DBF").is_file():
        return {"state": "skip", "why": "no STLOC"}
    info = rows(acct, "ISINFO", ("MAINLOC", "ACCNUM00"))
    main = (info[0]["MAINLOC"] if info else "") or ""
    if not main:
        return {"state": "skip", "why": "no ISINFO.MAINLOC"}
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    accounts = account_probe.probe_default_accounts(acct)
    if CASH:  # 现购 / 现销记现金：账套默认现金科目 ISINFO.ACCNUM00
        accounts["ap_acc"] = accounts["ar_acc"] = str((info[0].get("ACCNUM00") if info else "") or "").strip()
    need = ("ap_acc", "vat_input_acc", "fallback_acc", "ar_acc", "revenue_acc", "vat_output_acc")
    if any(not accounts.get(name) for name in need):
        return {"state": "skip", "why": f"default accounts missing: {[n for n in need if not accounts.get(n)]}"}
    stock = [row for row in rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP", "TOTBAL")) if str(row["STKTYP"]) == "0"
             and row["STKCOD"]]
    before_rows = stloc_rows(acct)
    at_main = {key(row)[0] for row in before_rows if key(row)[1] == main}
    pick = next((row for row in stock if row["STKCOD"] in at_main), None) or (stock[0] if stock else None)
    if pick is None:
        return {"state": "skip", "why": "no stock item"}
    suppliers = [row["SUPCOD"] for row in rows(acct, "APMAS", ("SUPCOD",)) if row["SUPCOD"]]
    customers = [row["CUSCOD"] for row in rows(acct, "ARMAS", ("CUSCOD",)) if row["CUSCOD"]]
    if not suppliers or not customers:
        return {"state": "skip", "why": "no supplier or customer"}
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    code, name = pick["STKCOD"], pick["STKDES"] or pick["STKCOD"]
    sums_before = {}
    for row in before_rows:
        k = key(row)
        if k[0] == code:
            sums_before[k[1]] = row
    total_before = sum(Decimal(repr(float(r.get("LOCBAL") or 0))) for r in sums_before.values())
    master_before = Decimal(repr(float(pick["TOTBAL"] or 0)))
    files_before = digests(acct)
    touched = {*S.PURCHASE_BACKUP_TABLES, *S.SALES_BACKUP_TABLES, *S.LOCATION_TABLES}
    index_before = cdx_check.snapshot(cdx_reindex, acct, touched) if with_runner else {}

    purchase = payload("HP" if CASH else "RR", be(day), "PO-STLOC", accounts, name, code, 5, Decimal("50.00"), supplier=suppliers[0])
    res = W.write_purchase(purchase, acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False)
    if not res.ok:
        # 物料组的存货 / 购货科目不在这套账的科目表里：原生 Express 存单报 1155 拒存，CC 同样整单不写（11-08 ERR_GROUP_ACCOUNT），
        # 是账套数据的事，记跳过。这个拒写发生在写到一半（正式路径靠写前备份整单回滚），回放不带备份会留下半截：
        # 重拷一份副本，按正式路径带写前备份再写一次，必须同样拒写且账套一字节不动
        if res.error_code == W.ERR_GROUP_ACCOUNT:
            shutil.rmtree(acct, ignore_errors=True)
            shutil.copytree(SOURCES[acct.name], acct)
            fresh = digests(acct)
            again = W.write_purchase(purchase, acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=True)
            if again.ok or again.error_code != W.ERR_GROUP_ACCOUNT or digests(acct) != fresh:
                return {"state": "fail", "why": f"purchase refused {res.error_code}; with the backup: {again.ok} {again.error_code}, "
                                                f"book unchanged {digests(acct) == fresh}"}
            return {"state": "skip", "why": f"purchase {res.error_code} (rolled back, book unchanged): {res.error}"[:300]}
        return {"state": "fail", "why": f"purchase {res.error_code}: {res.error}"}
    sale = payload("HS" if CASH else "IV", be(day), "IV-STLOC", accounts, name, code, 2, Decimal("40.00"), customer=customers[0])
    res2 = WS.write_sale(sale, acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False)
    if not res2.ok:
        return {"state": "fail", "why": f"sale {res2.error_code}: {res2.error}"}

    after_rows = stloc_rows(acct)
    after = {key(row): row for row in after_rows}
    before = {key(row): row for row in before_rows}
    problems = []
    target = (code, main)
    old, new = before.get(target), after.get(target)
    if new is None:
        problems.append("main STLOC row missing after write")
    else:
        delta = Decimal(repr(float(new.get("LOCBAL") or 0))) - Decimal(repr(float((old or {}).get("LOCBAL") or 0)))
        if delta != 3:
            problems.append(f"LOCBAL delta {delta} != 3")
        period = calendar.locate(day)
        field = f"QTY{period.suffix}"
        bucket = Decimal(repr(float(new.get(field) or 0))) - Decimal(repr(float((old or {}).get(field) or 0)))
        if bucket != 3:
            problems.append(f"{field} delta {bucket} != 3")
        for date_field in ("LPURDAT", "LSELDAT", "LMOVDAT"):
            got = new.get(date_field)
            if got is None or got < day:
                problems.append(f"{date_field} {got} not set to {day}")
    for k, row in before.items():
        if k != target and after.get(k) != row:
            problems.append(f"other STLOC row changed {k}")
            break
    if set(after) - set(before) - {target}:
        problems.append(f"unexpected new STLOC rows {sorted(set(after) - set(before) - {target})[:3]}")
    written = {name.upper() + ext for name in (*S.PURCHASE_BACKUP_TABLES, *S.SALES_BACKUP_TABLES, *S.LOCATION_TABLES)
               for ext in (".DBF", ".FPT")}
    files_after = digests(acct)
    untouched = [name for name in files_before if name not in written and files_after.get(name) != files_before[name]]
    if untouched:
        problems.append(f"tables outside the write set changed: {untouched[:5]}")
    if with_runner:
        problems.extend(cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, [res.docnum, res2.docnum]))
    # 明细照原生记法（10-26 验收 B3b 实录点 ①）：行号右对齐、单位与倍数带上；同账套原生明细的行号写法作对照
    problems.extend(native_check.problems(dbf, S, acct, [res.docnum, res2.docnum]))
    native = native_check.native_alignment(dbf, S, acct, {res.docnum, res2.docnum})
    master_after = [r for r in rows(acct, "STMAS", ("STKCOD", "TOTBAL")) if r["STKCOD"] == code]
    total_after = sum(Decimal(repr(float(r.get("LOCBAL") or 0))) for k, r in after.items() if k[0] == code)
    consistent_before = abs(master_before - total_before) < Decimal("0.0001")
    if consistent_before and master_after:
        if abs(Decimal(repr(float(master_after[0]["TOTBAL"] or 0))) - total_after) >= Decimal("0.0001"):
            problems.append(f"STMAS {master_after[0]['TOTBAL']} != sum STLOC {total_after} after write (was equal)")
    return {"state": "fail" if problems else "ok", "why": "; ".join(problems), "item": code, "main": main,
            "stmas_equal_before": consistent_before, "docnums": [res.docnum, res2.docnum], "native_seqnum": native,
            "cdx_checked": sorted(index_before)}


def payload(doctype, docdate, ref, accounts, name, code, qty, amount, *, supplier=None, customer=None):
    vat = (amount * Decimal("0.07")).quantize(Decimal("0.01"))
    total = amount + vat
    item = {"name": name[:50], "code": code, "qty": str(qty), "unit_price": str(amount / qty), "amount": f"{amount:.2f}",
            "unit": "", "item_mode": "stock_item" if doctype in ("RR", "HP") else "stock_sale"}
    base = {"doctype": doctype, "account_set": "DATAT", "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
            "ref_no": ref, "vat_rate": 7.0, "base_amount": f"{amount:.2f}", "vat_amount": f"{vat:.2f}",
            "total_amount": f"{total:.2f}", "items": [item], "items_status": "ok"}
    if doctype in ("RR", "HP"):
        base.update({"supplier": {"code": supplier, "name": "", "tax_id": "", "prename": "", "supplier_new": False},
                     "lines": [{"acc": accounts["fallback_acc"], "side": "D", "amount": f"{amount:.2f}", "desc": "ซื้อ"},
                               {"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"},
                               {"acc": accounts["ap_acc"], "side": "C", "amount": f"{total:.2f}", "desc": "เจ้าหนี้"}],
                     "items_account": accounts["fallback_acc"]})
    else:
        base.update({"direction": "sales",
                     "customer": {"code": customer, "name": "", "tax_id": "", "prename": "", "customer_new": False},
                     "lines": [{"acc": accounts["ar_acc"], "side": "D", "amount": f"{total:.2f}", "desc": "ลูกหนี้"},
                               {"acc": accounts["revenue_acc"], "side": "C", "amount": f"{amount:.2f}", "desc": "รายได้"},
                               {"acc": accounts["vat_output_acc"], "side": "C", "amount": f"{vat:.2f}", "desc": "ภาษีขาย"}],
                     "items_account": accounts["revenue_acc"]})
    return base


if __name__ == "__main__":
    sys.exit(main())
