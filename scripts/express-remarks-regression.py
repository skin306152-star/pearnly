#!/usr/bin/env python3
"""CC 写 Express 单据行附加说明（按台号码，ARTRNRM，载荷 17）的真账副本离线回归（评估 docs/work/2026-11-02-vehicle-artrnrm-评估.md）。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码写
- 一张进货（RR，``--cash`` 时 HP）两行：第 1 行库存品 1 台、带两条车号说明，第 2 行不带；
- 一张销货（IV，``--cash`` 时 HS）同样两行，号码挂在第 2 行；
- 账套有领用 OU 单据簿时，一张带一条 S/N 说明的领用，再照原生作废；
核：
- ARTRNRM 新增的行照原生字节：DOCNUM = 写出的单号、SEQNUM = 行号右对齐 3 位补到 8 位、REMARK 原文（cp874）、顺序同载荷；
- 领用作废后它的行说明打了删除标记，别的单的说明一条不动；
- 写入集合（进货 / 销货备份表 + STLOC + ARTRNRM，领用另加库存单的表）以外的表逐字节不变；
- 给了 ``--runner``：照正式 CC 重建索引，核写过的表 CDX 与 DBF 一致、新单在 ARTRNRM 的 DOCNUM 标签里查得到（``express_cdx_check``）。
源账套只读（只 copytree 出来）；工作副本用完删掉。不连网络、不碰服务器。

    python scripts/express-remarks-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_remarks_reg \\
        --companion <仓库>\\companion\\src --runner <CC 安装目录>\\cdx_reindex_runner.exe --out result.jsonl
只给测试机 C 盘的 T 账套（或其他测试副本）用。
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import shutil
import sys
import traceback
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAR = ["เลขตัวถัง MP1TFR41JTG032993", "เลขเครื่อง FUK575 สี ELG"]
PHONE = ["S/N 352948094653751  352948094652259"]


def _stloc_module():
    spec = importlib.util.spec_from_file_location("express_stloc_regression", HERE / "express-stloc-regression.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--pattern", default="T*")
    parser.add_argument("--work", required=True)
    parser.add_argument("--companion", required=True, help="companion/src 目录")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--runner", default="", help="已安装 CC 的 cdx_reindex_runner.exe：给了就重建索引并核 CDX 与 DBF 一致")
    parser.add_argument("--cash", action="store_true", help="写现购 HP / 现销 HS 代替赊购 RR / 赊销 IV")
    args = parser.parse_args(argv)
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(HERE))
    global dbf, S, W, WS, R, ST, V, fiscal_period, account_probe, cdx_reindex, cdx_check, base, with_runner, CASH
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, fiscal_period  # noqa: F401
    from companion import dbf_remarks as R  # noqa: F401
    from companion import dbf_sales as WS  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_stock as ST  # noqa: F401
    from companion import dbf_void as V  # noqa: F401
    from companion import dbf_writer as W  # noqa: F401

    import express_cdx_check as cdx_check  # noqa: F401
    base = _stloc_module()
    CASH = args.cash
    with_runner = cdx_check.use_runner(cdx_reindex, args.runner)
    base.dbf, base.S, base.fiscal_period, base.account_probe = dbf, S, fiscal_period, account_probe
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


def remark_rows(acct: Path):
    """ARTRNRM 全部行（含删除标记）：[(DOCNUM, SEQNUM 原字节, REMARK, 删除)]。"""
    path = acct / f"{R.TABLE}.DBF"
    if not path.is_file():
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        return [((r.DOCNUM or "").strip(), R._raw_seqnum(r), (r.REMARK or "").rstrip(), dbf.is_deleted(r)) for r in t]
    finally:
        t.close()


def check(acct: Path) -> dict:
    if not (acct / f"{R.TABLE}.DBF").is_file():
        return {"state": "skip", "why": "no ARTRNRM"}
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    info = base.rows(acct, "ISINFO", ("ACCNUM00",))
    accounts = account_probe.probe_default_accounts(acct)
    if CASH:
        accounts["ap_acc"] = accounts["ar_acc"] = str((info[0].get("ACCNUM00") if info else "") or "").strip()
    need = ("ap_acc", "vat_input_acc", "fallback_acc", "ar_acc", "revenue_acc", "vat_output_acc")
    if any(not accounts.get(name) for name in need):
        return {"state": "skip", "why": f"default accounts missing: {[n for n in need if not accounts.get(n)]}"}
    stock = [row for row in base.rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP")) if str(row["STKTYP"]) == "0" and row["STKCOD"]]
    if len(stock) < 2:
        return {"state": "skip", "why": "fewer than two stock items"}
    suppliers = [row["SUPCOD"] for row in base.rows(acct, "APMAS", ("SUPCOD",)) if row["SUPCOD"]]
    customers = [row["CUSCOD"] for row in base.rows(acct, "ARMAS", ("CUSCOD",)) if row["CUSCOD"]]
    if not suppliers or not customers:
        return {"state": "skip", "why": "no supplier or customer"}
    import datetime as dt

    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    (code1, name1), (code2, name2) = [(row["STKCOD"], row["STKDES"] or row["STKCOD"]) for row in stock[:2]]
    before = remark_rows(acct)
    files_before = base.digests(acct)
    touched = {*S.PURCHASE_BACKUP_TABLES, *S.SALES_BACKUP_TABLES, *S.LOCATION_TABLES, R.TABLE}
    has_issue = ST.book_row(acct, "OU") is not None and bool(accounts.get("fallback_acc"))
    if has_issue:
        touched |= set(ST.TABLES)
    index_before = cdx_check.snapshot(cdx_reindex, acct, touched) if with_runner else {}
    problems, docnums = [], []

    purchase = two_lines(base.payload("HP" if CASH else "RR", base.be(day), "PO-REMARK", accounts, name1, code1, 2,
                                      Decimal("100.00"), supplier=suppliers[0]), name2, code2, first_remarks=CAR)
    res = W.write_purchase(purchase, acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False)
    if not res.ok:
        return {"state": "fail", "why": f"purchase {res.error_code}: {res.error}"}
    docnums.append(res.docnum)
    sale = two_lines(base.payload("HS" if CASH else "IV", base.be(day), "IV-REMARK", accounts, name1, code1, 1,
                                  Decimal("80.00"), customer=customers[0]), name2, code2, second_remarks=CAR)
    res2 = WS.write_sale(sale, acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False)
    if not res2.ok:
        return {"state": "fail", "why": f"sale {res2.error_code}: {res2.error}"}
    docnums.append(res2.docnum)
    added = [row for row in remark_rows(acct) if row not in before]
    want = [(res.docnum, "  1     ", text, False) for text in CAR] + [(res2.docnum, "  2     ", text, False) for text in CAR]
    if added != want:
        problems.append(f"ARTRNRM rows {added} != {want}")

    issue = None
    if has_issue:
        issue = ST.write_stock_entry({"payload_version": 17, "direction": "stock", "doctype": "OU", "docdate_be": base.be(day),
                                      "ref_no": "IS-REMARK", "items": [{"code": code1, "name": name1, "qty": "1", "unit": "",
                                                                        "remarks": PHONE}]},
                                     acct, selected_dir=str(acct), make_backup=False)
        if not issue.ok:
            problems.append(f"issue {issue.error_code}: {issue.error}")
            issue = None
    if issue is not None:
        docnums.append(issue.docnum)
        voided = V.void_stock_entry({"payload_version": 17, "direction": "stock", "action": "void", "doctype": "OU",
                                     "docnum": issue.docnum, "items": [{"seq": 1, "code": code1, "loccod": issue_loc(acct, issue.docnum),
                                                                        "qty": "1"}]},
                                    acct, selected_dir=str(acct), make_backup=False)
        if not voided.ok:
            problems.append(f"void {voided.error_code}: {voided.error}")
        mine = [row for row in remark_rows(acct) if row[0] == issue.docnum]
        if mine != [(issue.docnum, "  1     ", PHONE[0], True)]:
            problems.append(f"voided issue remarks {mine}")
        kept = [row for row in remark_rows(acct) if row[0] in (res.docnum, res2.docnum)]
        if any(row[3] for row in kept):
            problems.append("voiding the issue deleted another document's remarks")

    written = {name.upper() + ext for name in touched for ext in (".DBF", ".FPT")}
    files_after = base.digests(acct)
    untouched = [name for name in files_before if name not in written and files_after.get(name) != files_before[name]]
    if untouched:
        problems.append(f"tables outside the write set changed: {untouched[:5]}")
    if with_runner:
        problems.extend(cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, docnums))
    return {"state": "fail" if problems else "ok", "why": "; ".join(problems), "docnums": docnums,
            "issue": bool(issue), "cdx_checked": sorted(index_before)}


def issue_loc(acct: Path, docnum: str) -> str:
    rows = [row for row in base.rows(acct, "STCRD", ("DOCNUM", "LOCCOD")) if row["DOCNUM"] == docnum]
    return rows[0]["LOCCOD"] if rows else ""


def two_lines(payload, name2, code2, *, first_remarks=None, second_remarks=None):
    """进货 / 销货载荷改成两行：原来那一行（数量、金额不变），再加一行第二件库存品 1 个 10.00；说明挂在给定那一行。"""
    item = payload["items"][0]
    extra = {**item, "name": name2[:50], "code": code2, "qty": "1", "unit_price": "10.00", "amount": "10.00"}
    if first_remarks:
        item["remarks"] = list(first_remarks)
    if second_remarks:
        extra["remarks"] = list(second_remarks)
    payload["items"] = [item, extra]
    amount = Decimal(payload["base_amount"]) + Decimal("10.00")
    vat = (amount * Decimal("0.07")).quantize(Decimal("0.01"))
    total = amount + vat
    payload.update(base_amount=f"{amount:.2f}", vat_amount=f"{vat:.2f}", total_amount=f"{total:.2f}")
    sides = {"D": [], "C": []}
    for line in payload["lines"]:
        sides[line["side"]].append(line)
    if payload["doctype"] in ("RR", "HP"):
        sides["D"][0]["amount"], sides["D"][1]["amount"], sides["C"][0]["amount"] = f"{amount:.2f}", f"{vat:.2f}", f"{total:.2f}"
    else:
        sides["D"][0]["amount"], sides["C"][0]["amount"], sides["C"][1]["amount"] = f"{total:.2f}", f"{amount:.2f}", f"{vat:.2f}"
    payload["payload_version"] = 17
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
