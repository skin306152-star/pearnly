#!/usr/bin/env python3
"""CC 写收银跨天退货单 SR（载荷 10，dbf_refund，B3b）的真账副本离线回归（10-26）。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码先写一张现销日汇总 HS（卖 2 个），次日写一张挂这张 HS 的退货单 SR（退 1 个）→ 核：
- HS、SR 都写成；SR 的 ARTRN 一行（RECTYP 5、SONUM = 原 HS），STCRD 一行（RDOCNUM = 原单号左对齐、行号右对齐到 15 位，RETSTK Y）；
- 商品结存：卖 2 退 1，写完比写前少 1；有 STLOC 的，写前 STMAS 等于各仓合计的品写后仍相等；
- SR 的凭证借贷平衡；有销项税的账套 ISVAT 记了这张 SR；
- 同一份退货载荷再写一次只认回那张 SR（写前判重），不多写一张；再退 2 个（只剩 1 个可退）被拒，账套一个字节都不变；
- 写入集合以外的表逐字节不变；给了 ``--runner`` 照正式 CC 重建索引并核 CDX 与 DBF 一致。
没有 HS / SR 单别、没有可记账的现金 / 收入科目、没有客户档、没有能卖的库存品的账套跳过并记原因。源账套只读（只 copytree 出来），工作副本用完删掉。

    python3 scripts/express-refund-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_refund_reg \\
        --companion <仓库>\\companion\\src --out result.jsonl [--runner C:\\Users\\skin3\\AppData\\Local\\Programs\\CC\\cdx_reindex_runner.exe]
只给测试机 C 盘的 T 账套（或其他测试副本）用。
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import fnmatch
import hashlib
import json
import shutil
import sys
import traceback
from decimal import Decimal
from pathlib import Path


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
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    global dbf, S, WS, R, D, account_rules, cdx_reindex, fiscal_period, WRITE_SET
    import dbf  # noqa: F401
    from companion import account_rules, cdx_reindex, fiscal_period  # noqa: F401
    from companion import dbf_refund as R  # noqa: F401
    from companion import dbf_sales as WS  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_stock as D  # noqa: F401

    WRITE_SET = set(S.SALES_BACKUP_TABLES) | set(S.MASTER_EXTRA_TABLES) | set(S.LOCATION_TABLES) | {"STTRN", "ARMAS"}
    global cdx_check, with_runner
    import express_cdx_check as cdx_check  # noqa: F401
    with_runner = cdx_check.use_runner(cdx_reindex, args.runner)
    books = sorted(path for path in Path(args.source).iterdir() if path.is_dir() and fnmatch.fnmatch(path.name, args.pattern))
    if args.limit:
        books = books[:args.limit]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    counts = {"ok": 0, "fail": 0, "skip": 0}
    with open(args.out, "w", encoding="utf-8") as out:
        for book in books:
            copy_dir = work / book.name
            shutil.rmtree(copy_dir, ignore_errors=True)
            try:
                shutil.copytree(book, copy_dir)
                result = check(copy_dir)
            except Exception:
                result = {"state": "fail", "why": traceback.format_exc()[-600:]}
            finally:
                shutil.rmtree(copy_dir, ignore_errors=True)
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


def dec(value):
    return Decimal(repr(float(value or 0)))


def digests(acct, only_outside=True):
    return {p.name.upper(): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(acct).iterdir()
            if p.is_file() and (not only_outside or p.stem.upper() not in WRITE_SET)}


def master_balance(acct, code):
    return sum((dec(r["MREMBAL"]) for r in rows(acct, "STMAS", ("STKCOD", "MREMBAL")) if text(r["STKCOD"]) == code), Decimal(0))


def located(acct, code):
    return sum((dec(r["LOCBAL"]) for r in rows(acct, "STLOC", ("STKCOD", "LOCBAL")) if text(r["STKCOD"]) == code), Decimal(0))


def be(day):
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def accounts(acct):
    """现金（ISINFO.ACCNUM00）、收入（科目表里第一个可记账的 4 字头）、销项税（名称带 ภาษีขาย 的可记账科目，没有就按不含税写）。"""
    cash = account_rules.default_account(acct, "ACCNUM00")
    chart = [r for r in rows(acct, "GLACC", ("ACCNUM", "ACCNAM", "ACCTYP")) if text(r["ACCTYP"]) == "0"]
    revenue = next((text(r["ACCNUM"]) for r in chart if text(r["ACCNUM"]).startswith("4")), "")
    vat = next((text(r["ACCNUM"]) for r in chart if "ภาษีขาย" in text(r["ACCNAM"])), "")
    return cash if account_rules.postable_account(acct, cash) else "", revenue, vat


def sellable(acct, has_stloc, day):
    """结存够卖 2 个的库存品；没有就用调整 JU 先垫 2 个（有 JU 单别且科目可记账时）。"""
    stock = [r for r in rows(acct, "STMAS", ("STKCOD", "STKTYP", "MREMBAL")) if text(r["STKTYP"]) == "0" and text(r["STKCOD"])]
    if not stock:
        return None, "no stock item"
    main = account_rules.main_location(acct)
    for r in stock:
        code = text(r["STKCOD"])
        if dec(r["MREMBAL"]) >= 2 and (not has_stloc or any(text(x["STKCOD"]) == code and text(x["LOCCOD"]) == main
                                                            and dec(x["LOCBAL"]) >= 2
                                                            for x in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL")))):
            return code, ""
    row = D.book_row(acct, "JU")
    if row is None or not account_rules.postable_account(acct, row["account"]):
        return None, "no item with stock and no postable JU book to add some"
    code = text(stock[0]["STKCOD"])
    res = D.write_stock_entry({"payload_version": 6, "direction": "stock", "doctype": "JU", "docdate_be": be(day), "ref_no": "REG-SR-JU",
                               "remark": "", "items": [{"code": code, "name": "", "qty": "2", "unit_cost": "10"}]},
                              acct, selected_dir=str(acct), make_backup=False)
    return (code, "") if res.ok else (None, f"JU to add stock {res.error_code}: {res.error}")


def item_of(acct, code):
    """载荷里这件品的名称与库存单位名（STMAS.STKDES、ISTAB 20 号表里 QUCOD 的名称）。"""
    master = next(r for r in rows(acct, "STMAS", ("STKCOD", "STKDES", "QUCOD")) if text(r["STKCOD"]) == code)
    units = {text(r["TYPCOD"]): text(r["TYPDES"]) for r in rows(acct, "ISTAB", ("TABTYP", "TYPCOD", "TYPDES"))
             if text(r["TABTYP"]) == "20"}
    return {"name": text(master["STKDES"]) or code, "code": code, "unit": units.get(text(master["QUCOD"]), "")}


def money(base, rate):
    base = Decimal(base)
    vat = (base * Decimal(rate) / 100).quantize(Decimal("0.01"))
    return format(base, "f"), format(vat, "f"), format(base + vat, "f")


def sale_payload(acct, day, code, customer, cash, revenue, vat_acc, rate):
    base, vat, total = money("200.00", rate)
    lines = [{"acc": cash, "side": "D", "amount": total, "desc": customer},
             {"acc": revenue, "side": "C", "amount": base, "desc": "รายได้จากการขาย"}]
    if rate:
        lines.append({"acc": vat_acc, "side": "C", "amount": vat, "desc": "ภาษีขาย"})
    return {"payload_version": 4, "direction": "sales", "doctype": "HS", "account_set": acct.name, "docdate_be": be(day),
            "vat_period_be": be(day)[:4] + "01", "ref_no": "REG-SI-000001",
            "customer": {"code": customer, "name": customer, "tax_id": "", "prename": "", "customer_new": False},
            "vat_rate": float(rate), "base_amount": base, "vat_amount": vat, "total_amount": total, "lines": lines,
            "items": [{**item_of(acct, code), "qty": "2", "unit_price": "100.00", "amount": base,
                       "item_mode": "stock_sale", "loccod": account_rules.main_location(acct)}],
            "items_status": "ok", "items_account": revenue,
            "pos": {"first": "REG-SI-000001", "last": "REG-SI-000001", "count": 1, "docnum": "auto", "cash": total, "transfers": []}}


def refund_payload(acct, day, code, customer, cash, revenue, vat_acc, rate, against, against_total, qty="1"):
    base, vat, total = money(format(Decimal(qty) * 100, "f"), rate)
    lines = [{"acc": revenue, "side": "D", "amount": base, "desc": "ลดหนี้/รับคืน", "role": "refund"}]
    if rate:
        lines.append({"acc": vat_acc, "side": "D", "amount": vat, "desc": "ภาษีขาย", "role": "output_vat"})
    lines.append({"acc": cash, "side": "C", "amount": total, "desc": customer, "role": "cash"})
    return {"payload_version": 10, "direction": "sales", "doctype": "SR", "account_set": acct.name, "docdate_be": be(day),
            "vat_period_be": be(day)[:4] + "01", "ref_no": f"REG-SI-00000{qty}",
            "customer": {"code": customer, "name": customer, "tax_id": "", "prename": "", "customer_new": False},
            "vat_rate": float(rate), "base_amount": base, "vat_amount": vat, "total_amount": total, "lines": lines,
            "items": [{**item_of(acct, code), "qty": qty, "unit_price": "100.00", "amount": base,
                       "item_mode": "stock_sale", "loccod": account_rules.main_location(acct)}],
            "items_status": "ok", "items_account": revenue,
            "pos": {"first": f"REG-SI-00000{qty}", "last": f"REG-SI-00000{qty}", "count": 1, "docnum": "auto", "cash": total,
                    "transfers": []},
            "refund": {"against": against, "against_total": against_total, "cntyp": 3, "retstk": "Y"}}


def write(acct, payload):
    return WS.write_sale(payload, acct, allowed_sets=(acct.name,), selected_dir=str(acct), make_backup=False)


def check(acct: Path) -> dict:
    for prefix in ("HS", "SR"):
        if not account_rules.book(acct, prefix):
            return {"state": "skip", "why": f"no ISRUN book {prefix}"}
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    cash, revenue, vat_acc = accounts(acct)
    if not cash or not revenue:
        return {"state": "skip", "why": f"no postable cash ({cash or '-'}) or revenue account"}
    customer = next((text(r["CUSCOD"]) for r in rows(acct, "ARMAS", ("CUSCOD",)) if text(r["CUSCOD"])), "")
    if not customer:
        return {"state": "skip", "why": "no customer in ARMAS"}
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days - 1))
    has_stloc = (acct / "STLOC.DBF").is_file()
    code, why = sellable(acct, has_stloc, day)
    if not code:
        return {"state": "skip", "why": why}
    rate = 7 if vat_acc else 0
    balance_before = master_balance(acct, code)
    was_balanced = has_stloc and balance_before == located(acct, code)
    files_before = digests(acct)
    index_before = cdx_check.snapshot(cdx_reindex, acct, WRITE_SET) if with_runner else {}
    sale = write(acct, sale_payload(acct, day, code, customer, cash, revenue, vat_acc, rate))
    if not sale.ok:
        return {"state": "fail", "why": f"HS {sale.error_code}: {sale.error}"}
    hs_total = text(next(r["NETAMT"] for r in rows(acct, "ARTRN", ("DOCNUM", "NETAMT")) if text(r["DOCNUM"]) == sale.docnum))
    refund = refund_payload(acct, day + dt.timedelta(days=1), code, customer, cash, revenue, vat_acc, rate, sale.docnum,
                            format(dec(hs_total), "f"))
    sr = write(acct, refund)
    if not sr.ok:
        return {"state": "fail", "why": f"SR {sr.error_code}: {sr.error}"}
    heads = [r for r in rows(acct, "ARTRN", ("DOCNUM", "RECTYP", "SONUM")) if text(r["DOCNUM"]) == sr.docnum]
    if [(text(r["RECTYP"]), text(r["SONUM"])) for r in heads] != [("5", sale.docnum)]:
        return {"state": "fail", "why": f"{sr.docnum} ARTRN {heads}"}
    lines = [r for r in rows(acct, "STCRD", ("DOCNUM", "RDOCNUM", "RETSTK")) if text(r["DOCNUM"]) == sr.docnum]
    if [(str(r["RDOCNUM"] or "").rstrip(), text(r["RETSTK"])) for r in lines] != [(R.rdocnum(sale.docnum, 1), "Y")]:
        return {"state": "fail", "why": f"{sr.docnum} STCRD {lines}"}
    if master_balance(acct, code) != balance_before - 1:
        return {"state": "fail", "why": f"STMAS {balance_before} -> {master_balance(acct, code)}, expected -1 (sold 2, returned 1)"}
    if was_balanced and master_balance(acct, code) != located(acct, code):
        return {"state": "fail", "why": f"STMAS != sum(STLOC) for {code} after the refund"}
    journal = [r for r in rows(acct, "GLJNLIT", ("VOUCHER", "TRNTYP", "AMOUNT")) if text(r["VOUCHER"]) == sr.docnum]
    debit = sum(dec(r["AMOUNT"]) for r in journal if text(r["TRNTYP"]) == "0")
    credit = sum(dec(r["AMOUNT"]) for r in journal if text(r["TRNTYP"]) != "0")
    if not journal or debit != credit:
        return {"state": "fail", "why": f"{sr.docnum} journal {debit} / {credit}"}
    if rate and not [r for r in rows(acct, "ISVAT", ("DOCNUM", "RECTYP")) if text(r["DOCNUM"]) == sr.docnum]:
        return {"state": "fail", "why": f"{sr.docnum} has no ISVAT row"}
    # 判重与退超：都不能改账套一个字节
    all_before = digests(acct, only_outside=False)
    again = write(acct, copy.deepcopy(refund))
    if not again.ok or again.docnum != sr.docnum:
        return {"state": "fail", "why": f"same refund again: {again.ok} {again.docnum} {again.error_code}"}
    over = write(acct, refund_payload(acct, day + dt.timedelta(days=1), code, customer, cash, revenue, vat_acc, rate, sale.docnum,
                                      format(dec(hs_total), "f"), qty="2"))
    if over.ok or over.error_code != R.ERR_EXCEEDS:
        return {"state": "fail", "why": f"refunding 2 more: {over.ok} {over.error_code} {over.error}"}
    touched = sorted(name for name, digest in digests(acct, only_outside=False).items() if all_before.get(name) != digest)
    if touched:
        return {"state": "fail", "why": f"the repeat or the refused refund changed {touched}"}
    index_problems = cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, [sale.docnum, sr.docnum]) if with_runner else []
    if index_problems:
        return {"state": "fail", "why": "; ".join(index_problems)}
    changed = sorted(name for name, digest in digests(acct).items() if files_before.get(name) != digest)
    if changed:
        return {"state": "fail", "why": f"tables outside the write set changed: {changed}"}
    return {"state": "ok", "docs": [sale.docnum, sr.docnum], "item": code, "vat": bool(rate), "stloc": has_stloc,
            "cdx_checked": sorted(index_before)}


if __name__ == "__main__":
    sys.exit(main())
