#!/usr/bin/env python3
"""CC 写仓间调拨 RL（dbf_transfer）与作废 RL（dbf_void）的真账副本离线回归（多仓闭环 P2，10-30）。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码，挑一件在某个仓有正结存的库存品（没有，或照负库存规则核不过，就先用调整 JU
在副本里垫 1 个，10-31），从那个仓调 1 个到这套账的另一个仓（ISTAB 21）→ 核原生 RL 记法（实录 RL0000001–0000003，4.6 / 4.6b）：
- 单头 STTRN 一行：POSOPR 4、LOCCOD 调出、PEOPLE 调入、TRNVAL 0、NXTSEQ「  1」、DOCSTAT N；
- 明细 STCRD 一对：调出 4 / 调入 3、同行号、PEOPLE 互指、数量相等、单价金额 0、REIMBURSE \\x00；
- 两仓 STLOC：调出 −1、调入 +1（调入仓原来没有这件就新建一行）；STMAS 这件品的结存与货值不变；没有凭证；ISRUN 的 RL 号 +1；
- 写入集合以外的表逐字节不变（写入集合 = STTRN、STCRD、STLOC、ISRUN 及索引；STMAS、GLJNL 也必须不变）；
- 再作废：单头 C、NXTSEQ 空、那一对不在（删除标记）、两仓回到写前；
- 给了 ``--runner``：照正式 CC 重建索引，并核写入集合的 CDX 与 DBF 一致（``express_cdx_check``）。
账套没有 RL 单据簿、只有一个仓、没有 STLOC、没有库存品（或要垫货却没有可记账的 JU）时跳过并记原因。源账套只读（只 copytree 出来），工作副本用完删掉。

    python3 scripts/express-transfer-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_rl_reg \\
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

WRITE_SET = {"STTRN", "STCRD", "STLOC", "ISRUN"}


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
    global dbf, S, T, V, D, account_rules, fiscal_period, stock_guard, stock_location, cdx_reindex, cdx_check, with_runner
    import dbf  # noqa: F401
    from companion import account_rules, cdx_reindex, fiscal_period, stock_guard, stock_location  # noqa: F401
    from companion import dbf_stock as D  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_transfer as T  # noqa: F401
    from companion import dbf_void as V  # noqa: F401
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


def rows(acct, table, fields, deleted=False):
    path = Path(acct) / f"{table}.DBF"
    if not path.is_file():
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in t.field_names}
        source = (r for r in t if dbf.is_deleted(r)) if deleted else S.live_rows(t)
        return [{f: (getattr(r, f) if f in names else None) for f in fields} for r in source]
    finally:
        t.close()


def text(value):
    return str(value or "").strip()


def num(value):
    return Decimal(repr(float(value or 0)))


def digests(acct):
    return {p.name.upper(): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(acct).iterdir()
            if p.is_file() and p.stem.upper() not in WRITE_SET}


def places(acct, code):
    return {text(r["LOCCOD"]): num(r["LOCBAL"]) for r in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL")) if text(r["STKCOD"]) == code}


def counter(acct):
    return [text(r["DOCNUM"]) for r in rows(acct, "ISRUN", ("DOCTYP", "PREFIX", "DOCNUM"))
            if text(r["DOCTYP"]) == "RL" and text(r["PREFIX"]) == "RL"]


def check(acct: Path) -> dict:
    if T.book_row(acct) is None:
        return {"state": "skip", "why": "no RL book in ISRUN"}
    if not (acct / "STLOC.DBF").is_file():
        return {"state": "skip", "why": "no STLOC"}
    known = sorted(stock_location.warehouses(acct))
    if len(known) < 2:
        return {"state": "skip", "why": f"locations {known}"}
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    stock = [text(r["STKCOD"]) for r in rows(acct, "STMAS", ("STKCOD", "STKTYP")) if text(r["STKTYP"]) == "0" and text(r["STKCOD"])]
    if not stock:
        return {"state": "skip", "why": "no stock item"}
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    docdate = f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"
    pick = next(((text(r["STKCOD"]), text(r["LOCCOD"])) for r in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL"))
                 if text(r["STKCOD"]) in stock and text(r["LOCCOD"]) in known and num(r["LOCBAL"]) >= 1), None)
    topped = ""
    if not pick:
        pick = (stock[0], known[0])
        why = top_up(acct, *pick, docdate)
        if why:
            return {"state": "skip", "why": why}
        topped = pick[1]
    elif stock_guard.shortages(acct, [{"code": pick[0], "name": "", "qty": "1", "item_mode": "stock_sale"}], [pick[1]]):
        # 仓里有、但账套按全仓核且主档结存不够（各仓合计与 MREMBAL 对不上的老账）：CC 照 Express 规则停下，这里先垫 1 个再测
        why = top_up(acct, *pick, docdate)
        if why:
            return {"state": "skip", "why": why}
        topped = pick[1]
    code, source = pick
    target = next(loc for loc in known if loc != source)
    before = places(acct, code)
    master_before = [(num(r["MREMBAL"]), num(r["MREMVAL"])) for r in rows(acct, "STMAS", ("STKCOD", "MREMBAL", "MREMVAL"))
                     if text(r["STKCOD"]) == code]
    run_before = counter(acct)
    files_before = digests(acct)
    index_before = cdx_check.snapshot(cdx_reindex, acct, WRITE_SET) if with_runner else {}
    payload = {"payload_version": 14, "direction": "stock", "doctype": "RL", "docdate_be": docdate, "ref_no": "REG-RL",
               "remark": "reg transfer", "loccod": source, "to_loccod": target,
               "items": [{"code": code, "name": "", "qty": "1", "loccod": source}]}
    res = T.write_transfer(payload, acct, selected_dir=str(acct), make_backup=False)
    if not res.ok:
        return {"state": "fail", "why": f"RL {res.error_code}: {res.error}"}
    docnum = res.docnum
    heads = [r for r in rows(acct, "STTRN", ("DOCNUM", "POSOPR", "LOCCOD", "PEOPLE", "TRNVAL", "NXTSEQ", "DOCSTAT"))
             if text(r["DOCNUM"]) == docnum]
    if len(heads) != 1 or (text(heads[0]["POSOPR"]), text(heads[0]["LOCCOD"]), text(heads[0]["PEOPLE"]), text(heads[0]["DOCSTAT"])) \
            != ("4", source, target, "N") or num(heads[0]["TRNVAL"]) != 0 or str(heads[0]["NXTSEQ"]) != "  1":
        return {"state": "fail", "why": f"STTRN {heads}"}
    lines = sorted(([text(r["POSOPR"]), text(r["LOCCOD"]), text(r["PEOPLE"]), str(r["SEQNUM"]), num(r["XTRNQTY"]), num(r["XTRNVAL"]),
                     num(r["UNITPR"]), r["REIMBURSE"]]
                    for r in rows(acct, "STCRD", ("DOCNUM", "POSOPR", "LOCCOD", "PEOPLE", "SEQNUM", "XTRNQTY", "XTRNVAL", "UNITPR",
                                                  "REIMBURSE"))
                    if text(r["DOCNUM"]) == docnum), reverse=True)
    want = [["4", source, target, "  1"], ["3", target, source, "  1"]]
    if [line[:4] for line in lines] != want or any(line[5] or line[6] for line in lines) or lines[0][4] != lines[1][4] \
            or any(line[7] not in (None, "\x00") for line in lines):
        return {"state": "fail", "why": f"STCRD {lines}"}
    after = places(acct, code)
    if after.get(source) != before.get(source) - 1 or after.get(target) != before.get(target, Decimal(0)) + 1:
        return {"state": "fail", "why": f"STLOC {before} -> {after}"}
    if [r for r in rows(acct, "GLJNL", ("VOUCHER",)) if text(r["VOUCHER"]) == docnum]:
        return {"state": "fail", "why": "journal written"}
    run_after = counter(acct)
    if run_before and run_after and int(run_after[0]) != int(run_before[0]) + 1:
        return {"state": "fail", "why": f"ISRUN RL {run_before} -> {run_after}"}
    changed = sorted(name for name, digest in digests(acct).items() if files_before.get(name) != digest)
    if changed:
        return {"state": "fail", "why": f"tables outside the write set changed (STMAS / GLJNL must not): {changed}"}
    master_after = [(num(r["MREMBAL"]), num(r["MREMVAL"])) for r in rows(acct, "STMAS", ("STKCOD", "MREMBAL", "MREMVAL"))
                    if text(r["STKCOD"]) == code]
    if master_after != master_before:
        return {"state": "fail", "why": f"STMAS {master_before} -> {master_after}"}
    void = {"payload_version": 14, "direction": "stock", "action": "void", "doctype": "RL", "docnum": docnum,
            "items": [{"seq": 1, "code": code, "loccod": source, "qty": "1"}]}
    out = V.void_stock_entry(void, acct, selected_dir=str(acct), make_backup=False)
    if not out.ok:
        return {"state": "fail", "why": f"void {out.error_code}: {out.error}"}
    heads = [r for r in rows(acct, "STTRN", ("DOCNUM", "DOCSTAT", "NXTSEQ")) if text(r["DOCNUM"]) == docnum]
    if [(text(r["DOCSTAT"]), text(r["NXTSEQ"])) for r in heads] != [("C", "")]:
        return {"state": "fail", "why": f"void header {heads}"}
    if [r for r in rows(acct, "STCRD", ("DOCNUM",)) if text(r["DOCNUM"]) == docnum] or \
            len([r for r in rows(acct, "STCRD", ("DOCNUM",), deleted=True) if text(r["DOCNUM"]) == docnum]) != 2:
        return {"state": "fail", "why": "void: the pair is not marked deleted"}
    back = places(acct, code)
    if back.get(source) != before.get(source) or back.get(target, Decimal(0)) != before.get(target, Decimal(0)):
        return {"state": "fail", "why": f"STLOC after void {back} != {before}"}
    index_problems = cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, [docnum]) if with_runner else []
    if index_problems:
        return {"state": "fail", "why": "; ".join(index_problems)}
    changed = sorted(name for name, digest in digests(acct).items() if files_before.get(name) != digest)
    if changed:
        return {"state": "fail", "why": f"tables outside the write set changed after void: {changed}"}
    return {"state": "ok", "doc": docnum, "item": code, "from": source, "to": target, "new_target_row": target not in before,
            "topped_up_with_ju": bool(topped), "cdx_checked": sorted(index_before)}


def top_up(acct, code, loccod, docdate):
    """用调整 JU 给这件品在这个仓垫到这个仓与全仓合计都至少 1 个（主档结存可能本来是负的）；写前快照在这之后取，
    JU 动的 STMAS / 凭证不算进 RL 的写入集合。不能垫时回原因。"""
    row = D.book_row(acct, "JU")
    if row is None or not account_rules.postable_account(acct, row["account"]):
        return "no item with a positive location balance and no postable JU book to add one"
    total = sum((num(r["MREMBAL"]) for r in rows(acct, "STMAS", ("STKCOD", "MREMBAL")) if text(r["STKCOD"]) == code), Decimal(0))
    qty = max(Decimal(1), 1 - min(total, places(acct, code).get(loccod, Decimal(0))))
    res = D.write_stock_entry({"payload_version": 6, "direction": "stock", "doctype": "JU", "docdate_be": docdate, "ref_no": "REG-RL-JU",
                               "remark": "", "loccod": loccod, "items": [{"code": code, "name": "", "qty": format(qty, "f"),
                                                                          "unit_cost": "10", "loccod": loccod}]},
                              acct, selected_dir=str(acct), make_backup=False)
    if not res.ok:
        return f"JU to add stock {res.error_code}: {res.error}"
    if places(acct, code).get(loccod, Decimal(0)) < 1:
        return f"JU {res.docnum} did not put {code} at {loccod}"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
