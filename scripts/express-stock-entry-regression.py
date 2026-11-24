#!/usr/bin/env python3
"""CC 写库存单（载荷 6：领用 OU / 调整 JU / 盘点 TK，dbf_stock）的真账副本离线回归（出入库第一期 B 段，10-25）。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码依次写一张调整（JU，+2 个、给单位成本）、一张领用（OU，1 个）、
一张盘点（TK，−1 个）、一张盘盈（TK，+1 个）→ 核：
- 盘点 TK 照 Express 原生（实录第 9 条）：POSOPR ':'、FLAG 5、单头货值 0；盘盈单价取主仓最近一次进货那一批的成本并挂上
  （RDOCNUM / MLOTNUM），挂不上的先进先出账套记 0；
- 四张单都写成，单头 STTRN 一行、明细 STCRD 行数与单据一致；
- 写前 STMAS 总结存 TOTBAL 等于各仓 LOCBAL 合计的品，写后仍相等（数量对得上 +2 −1 −1 = 0；11-07 f35a3f66f 起总结存是 TOTBAL，
  批次链 MREMBAL 在分仓计价账套抄这个仓的链，11-19 f1f5f504a，不再拿来对各仓合计）；
- 四张单的顺序（+2 −1 −1）要求起始结存不小于 0：第一件库存品要照负库存规则核（stock_guard：账套不许负库存或品标了 N，品标 Y 不核）
  且 Express 核的那个结存（按仓核 = 主仓 LOCBAL，按全仓核 = 总结存 TOTBAL）为负时，顺次取第一件核得过的（都没有就跳过并记原因）
  ——那样的品原生同样领不出，写入器拒写是对的（11-23 T9GIS 盘亏 −1 被拒）；
- 凭证：永续盘存账套每张有金额的单一张 JNLTYP 00 凭证、借贷平衡；定期盘存账套（ISPERPETUA = N）一张凭证都没有；
- 写入集合以外的表逐字节不变（写入集合 = STTRN、STCRD、STMAS、STLOC、GLJNL、GLJNLIT、ISRUN 及索引）；
- 再倒着逐张作废（载荷 8，dbf_void）：单头标 C、明细不在，商品结存与各仓结存回到写前；
- 给了 ``--runner``：照正式 CC 重建索引，并核写入集合的 CDX 与 DBF 一致（``express_cdx_check``）。
账套 ISRUN 没有 OU / JU / TK 单别、没有库存品、默认科目不能记账的跳过并记原因。源账套只读（只 copytree 出来），工作副本用完删掉。

    python3 scripts/express-stock-entry-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_stock_reg \\
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

# GLBAL：有凭证时科目余额对应格标待重算（glbal_mark，照 Express 原生，11-12）
WRITE_SET = {"STTRN", "STCRD", "STMAS", "STLOC", "GLJNL", "GLJNLIT", "ISRUN", "GLBAL"}


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
    global dbf, S, D, V, account_rules, cdx_reindex, fiscal_period, stock_guard
    import dbf  # noqa: F401
    from companion import account_rules, cdx_reindex, fiscal_period, stock_guard  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_stock as D  # noqa: F401
    from companion import dbf_void as V  # noqa: F401

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


def total(r):
    """主档总结存 TOTBAL（老表没有这栏时按批次链 MREMBAL，同 ``express_masters``）。"""
    return Decimal(repr(float((r["TOTBAL"] if r.get("TOTBAL") is not None else r["MREMBAL"]) or 0)))


def startable(acct, row):
    """这件品从现有结存起做 +2 −1 −1 不会被负库存规则拦（照 stock_guard：品 NEGALLOW Y 不核、N 必核、空跟账套；
    按仓核看主仓 LOCBAL，按全仓核看总结存）。"""
    flag = text(row.get("NEGALLOW")).upper()
    if flag == "Y" or (flag != "N" and stock_guard.negative_allowed(acct)):
        return True
    code = text(row["STKCOD"])
    if stock_guard.by_warehouse(acct) and (acct / "STLOC.DBF").is_file():
        main = account_rules.main_location(acct)
        start = sum((Decimal(repr(float(r["LOCBAL"] or 0))) for r in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL"))
                     if text(r["STKCOD"]) == code and (text(r["LOCCOD"]) or main) == main), Decimal(0))
    else:
        start = total(row)
    return start >= 0


def balanced(acct, codes):
    master = {text(r["STKCOD"]): total(r) for r in rows(acct, "STMAS", ("STKCOD", "TOTBAL", "MREMBAL"))}
    loc = {}
    for r in rows(acct, "STLOC", ("STKCOD", "LOCBAL")):
        loc[text(r["STKCOD"])] = loc.get(text(r["STKCOD"]), Decimal(0)) + Decimal(repr(float(r["LOCBAL"] or 0)))
    return {code: master.get(code, Decimal(0)) == loc.get(code, Decimal(0)) for code in codes}


def pack_check(acct, has_stloc, docdate):
    """带包装单位（STMAS SQUCOD / PQUCOD / CQUCOD 系数不是 1）的库存品：用那个包装单位写一张调整 JU 1 个，核 STCRD 记成
    TFACTOR = 系数、XTRNQTY = 系数，商品结存与主仓结存都只多「1 × 系数」（验收 10-25：包装单位按 1 写）。没有这种品的账套不核。"""
    fields = ("STKCOD", "STKTYP", "QUCOD", "SQUCOD", "SFACTOR", "PQUCOD", "PFACTOR", "CQUCOD", "CFACTOR")
    units = {text(r["TYPCOD"]): text(r["TYPDES"]) for r in rows(acct, "ISTAB", ("TABTYP", "TYPCOD", "TYPDES"))
             if text(r["TABTYP"]) == "20"}
    for r in rows(acct, "STMAS", fields):
        if text(r["STKTYP"]) != "0":
            continue
        for unit_field, factor_field in (("SQUCOD", "SFACTOR"), ("PQUCOD", "PFACTOR"), ("CQUCOD", "CFACTOR")):
            factor = Decimal(repr(float(r[factor_field] or 0)))
            code = text(r[unit_field])
            # 包装单位与库存单位同码的（主档自相矛盾）按库存单位写，不在这里核（stock_units）
            if code and factor > 0 and factor != 1 and units.get(code) and code != text(r["QUCOD"]):
                stkcod = text(r["STKCOD"])
                before = master_balance(acct, stkcod)
                located_before = located(acct, stkcod) if has_stloc else 0
                payload = {"payload_version": 6, "direction": "stock", "doctype": "JU", "docdate_be": docdate,
                           "ref_no": "REG-PACK", "remark": "", "items": [{"code": stkcod, "name": "", "qty": "1",
                                                                          "unit": units[code], "unit_cost": "10"}]}
                res = D.write_stock_entry(payload, acct, selected_dir=str(acct), make_backup=False)
                if not res.ok:
                    return {"why": f"pack JU {stkcod} {res.error_code}: {res.error}"}
                line = [x for x in rows(acct, "STCRD", ("DOCNUM", "TFACTOR", "XTRNQTY", "TRNQTY")) if text(x["DOCNUM"]) == res.docnum][0]
                if (Decimal(repr(float(line["TFACTOR"]))), Decimal(repr(float(line["XTRNQTY"])))) != (factor, factor):
                    return {"why": f"pack JU {stkcod}: TFACTOR {line['TFACTOR']} XTRNQTY {line['XTRNQTY']} != {factor}"}
                after = master_balance(acct, stkcod)
                if after[0][0] - before[0][0] != factor:
                    return {"why": f"pack JU {stkcod}: STMAS {before} -> {after}, expected +{factor}"}
                if has_stloc and located(acct, stkcod) - located_before != factor:
                    return {"why": f"pack JU {stkcod}: STLOC {located_before} -> {located(acct, stkcod)}, expected +{factor}"}
                return {"pack": {"item": stkcod, "unit": code, "factor": str(factor), "docnum": res.docnum}}
    return {}


def located(acct, code):
    """这件品各仓结存合计（不带仓库码的行写主仓，一张单只动一个仓，所以合计的变化就是那个仓的变化）。"""
    return sum((Decimal(repr(float(r["LOCBAL"] or 0))) for r in rows(acct, "STLOC", ("STKCOD", "LOCBAL"))
                if text(r["STKCOD"]) == code), Decimal(0))


def master_balance(acct, code):
    """[(总结存 TOTBAL, 总货值 TOTVAL, 批次链 MREMBAL, MREMVAL)]：包装单位核总结存的增量；作废后四栏都要回到写前。"""
    def dec(value):
        return Decimal(repr(float(value or 0)))
    return [(total(r), dec(r["TOTVAL"] if r["TOTVAL"] is not None else r["MREMVAL"]), dec(r["MREMBAL"]), dec(r["MREMVAL"]))
            for r in rows(acct, "STMAS", ("STKCOD", "TOTBAL", "TOTVAL", "MREMBAL", "MREMVAL")) if text(r["STKCOD"]) == code]


def location_balances(acct, code):
    return sorted((text(r["LOCCOD"]), Decimal(repr(float(r["LOCBAL"] or 0))))
                  for r in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL")) if text(r["STKCOD"]) == code)


def latest_receipt(acct, code, day=None):
    """这件品主仓最近一次进货那一行（STCRD POSOPR 0、数量为正、日期不晚于盘点日 ``day``，按日期取最后的）→ (成本, 单号, 行号, 日期)；
    没有回 None。"""
    main = account_rules.main_location(acct)
    found = None
    for r in rows(acct, "STCRD", ("STKCOD", "LOCCOD", "POSOPR", "XTRNQTY", "XTRNVAL", "DOCNUM", "SEQNUM", "DOCDAT")):
        if text(r["STKCOD"]) != code or text(r["POSOPR"]) != "0" or (text(r["LOCCOD"]) or main) != main:
            continue
        qty = Decimal(repr(float(r["XTRNQTY"] or 0)))
        if qty > 0 and r["DOCDAT"] and (day is None or r["DOCDAT"] <= day) and (found is None or r["DOCDAT"] >= found[3]):
            found = (Decimal(repr(float(r["XTRNVAL"] or 0))) / qty, text(r["DOCNUM"]), str(r["SEQNUM"] or "").ljust(3)[:3], r["DOCDAT"])
    return found


def count_gain(acct, docnum, lot, given=None):
    """盘盈 TK 照 Express 原生（实录第 9 条）：POSOPR ':'、FLAG 5、单头货值 0；单价取最近一次进货那一批的成本并挂上（RDOCNUM / MLOTNUM），
    挂不上的先进先出账套记 0。不符回原因。"""
    line = [r for r in rows(acct, "STCRD", ("DOCNUM", "POSOPR", "FLAG", "XUNITPR", "RDOCNUM", "MLOTNUM", "PHYBAL"))
            if text(r["DOCNUM"]) == docnum][0]
    head = [r for r in rows(acct, "STTRN", ("DOCNUM", "POSOPR", "TRNVAL")) if text(r["DOCNUM"]) == docnum][0]
    if text(line["POSOPR"]) != ":" or text(head["POSOPR"]) != ":" or Decimal(repr(float(head["TRNVAL"] or 0))) != 0:
        return f"TK {docnum}: POSOPR {line['POSOPR']!r} / {head['POSOPR']!r}, header TRNVAL {head['TRNVAL']}"
    if line["FLAG"] is not None and text(line["FLAG"]) != "5":
        return f"TK {docnum}: FLAG {line['FLAG']!r}"
    fifo = text((account_rules._first(acct, "ISINFO") or {}).get("COSMTD")).upper() == "F"
    cost = Decimal(repr(float(line["XUNITPR"] or 0))).quantize(Decimal("0.01"))
    want = lot[0].quantize(Decimal("0.01")) if lot else Decimal("0.00") if fifo else None  # 平均成本账套挂不上批次：商品均价（待核）
    if given is not None:
        want = Decimal(given).quantize(Decimal("0.01"))  # 载荷带了人填 / 改过的单价就用它（主会话 11-10 定第 5 点）
    if want is not None and cost != want:
        return f"TK {docnum}: unit cost {cost} != lot {lot}"
    # Express 拼 RDOCNUM 是「单号 C(12) + 行号原样 C(3)」（存量 CC 单的行号左对齐）
    if lot and line["RDOCNUM"] is not None and str(line["RDOCNUM"] or "").rstrip() != f"{lot[1]:<12}{lot[2]}".rstrip():
        return f"TK {docnum}: RDOCNUM {line['RDOCNUM']!r} != lot {lot[1]} {lot[2]}"
    return ""


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
    stock = [r for r in rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP", "TOTBAL", "MREMBAL", "NEGALLOW"))
             if text(r["STKTYP"]) == "0" and text(r["STKCOD"])]
    if not stock:
        return {"state": "skip", "why": "no stock item"}
    if not startable(acct, stock[0]):
        stock = [r for r in stock if startable(acct, r)]
        if not stock:
            return {"state": "skip", "why": "every stock item would be refused by the negative stock rule (balance below zero)"}
    has_stloc = (acct / "STLOC.DBF").is_file()
    code = text(stock[0]["STKCOD"])
    before_balanced = balanced(acct, [code])[code] if has_stloc else None
    files_before = digests(acct)
    balance_before = master_balance(acct, code)
    locations_before = location_balances(acct, code) if has_stloc else None
    index_before = cdx_check.snapshot(cdx_reindex, acct, WRITE_SET) if with_runner else {}
    perpetual = text((account_rules._first(acct, "ISINFO") or {}).get("ISPERPETUA") or "Y").upper() != "N"
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    docdate = f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"
    written = []
    # 先入后出：不允许负库存的账套（NEGALLOW = N）里结存为 0 的品先领用会被正确拦下（stock_guard）
    steps = (("JU", 2, {"unit_cost": "15"}), ("OU", 1, {}), ("TK", -1, {}), ("TK", 1, {"unit_cost": "99"}))
    lot = latest_receipt(acct, code, day)
    for doctype, qty, extra in steps:
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
    # 作废（载荷 8，dbf_void）：倒着逐张作废，单头标 C、明细不在、结存回到写前
    gain = count_gain(acct, written[-1], lot, given=steps[-1][2].get("unit_cost"))
    if gain:
        return {"state": "fail", "why": gain}
    detail = native_check.problems(dbf, S, acct, written)  # 明细照原生记法：行号右对齐、单位与倍数（10-26）
    if detail:
        return {"state": "fail", "why": "; ".join(detail[:5])}
    for docnum, (doctype, qty) in reversed(list(zip(written, [(step[0], step[1]) for step in steps]))):
        void = {"payload_version": 8, "direction": "stock", "action": "void", "doctype": doctype, "docnum": docnum,
                "items": [{"code": code, "qty": str(qty)}]}
        res = V.void_stock_entry(void, acct, selected_dir=str(acct), make_backup=False)
        if not res.ok:
            return {"state": "fail", "why": f"void {docnum} {res.error_code}: {res.error}"}
        heads = [r for r in rows(acct, "STTRN", ("DOCNUM", "DOCSTAT")) if text(r["DOCNUM"]) == docnum]
        if [text(r["DOCSTAT"]) for r in heads] != ["C"] or [r for r in rows(acct, "STCRD", ("DOCNUM",)) if text(r["DOCNUM"]) == docnum]:
            return {"state": "fail", "why": f"void {docnum}: header {heads} or detail rows left"}
    if master_balance(acct, code) != balance_before:
        return {"state": "fail", "why": f"STMAS balance {master_balance(acct, code)} != {balance_before} after voiding"}
    if has_stloc and location_balances(acct, code) != locations_before:
        return {"state": "fail", "why": f"STLOC {location_balances(acct, code)} != {locations_before} after voiding"}
    index_problems = cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, written) if with_runner else []
    if index_problems:
        return {"state": "fail", "why": "; ".join(index_problems)}
    pack = pack_check(acct, has_stloc, docdate)
    if pack.get("why"):
        return {"state": "fail", "why": pack["why"]}
    changed = sorted(name for name, digest in digests(acct).items() if files_before.get(name) != digest)
    if changed:
        return {"state": "fail", "why": f"tables outside the write set changed: {changed}"}
    return {"state": "ok", "docs": written, "perpetual": perpetual, "stloc": has_stloc, "was_balanced": before_balanced, "pack": pack.get("pack"),
            "cdx_checked": sorted(index_before)}


if __name__ == "__main__":
    sys.exit(main())
