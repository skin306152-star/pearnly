#!/usr/bin/env python3
"""CC 直写进货单免税记法的真账副本离线回归（主会话 10-29：整单免税照 Express 原生）。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码写四张进货 → 核原生记法：
- 整单免税的赊购 RR / 现购 HP（全是免税行、带 ``vat_fields`` AMT01 0 + AMTRAT0 全额）：价格类型 2 / 1、VATAMT 0、单头 AMTRAT0 =
  税前、明细 VATCOD '0' 且 NETVAL = TRNVAL、SRV_VATTYP '1'、不写进项税记录（原生主流）；
- 混合税率的赊购 RR（应税 100 + 免税 50）：价格类型 2、单头 AMTRAT0 50、进项税记录 AMT01 100 / VAT01 7 / AMTRAT0 50、免税行 VATCOD '0'；
- 供应商不开税票的现购 HP（没有免税行、没带 vat_fields）：价格类型 0、没有进项税记录（与改前相同）；
- 没被写的表逐字节不变（写入集合 = 进货备份表 + STLOC，另有索引文件）；给了 ``--runner`` 照正式 CC 重建索引并核 CDX 与 DBF 一致。
明细用这本账套已有的非库存品（服务品）按码写，不建主档。源账套只读（只 copytree 出来）；工作副本用完删掉。不连网络、不碰服务器。

    python3 scripts/express-exempt-purchase-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" \\
        --work C:\\PearnlyStress\\_exempt_reg --companion <仓库>\\companion\\src --out result.jsonl [--runner <cdx_reindex_runner.exe>]
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
    global dbf, S, W, fiscal_period, account_probe, cdx_reindex, cdx_check, with_runner
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, fiscal_period  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_writer as W  # noqa: F401
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
            print(result["state"], book.name, result.get("why", "")[:200], flush=True)
    print(json.dumps(counts))
    return 1 if counts["fail"] else 0


def rows(acct, table, fields, **where):
    path = acct / f"{table}.DBF"
    if not path.is_file():
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        names = {name.upper() for name in t.field_names}
        out = []
        for rec in S.live_rows(t):
            item = {}
            for field in (*fields, *where):
                value = rec[field] if field.upper() in names else None
                item[field] = value.strip() if isinstance(value, str) else value
            if all(str(item.get(k) or "") == str(v) for k, v in where.items()):
                out.append(item)
        return out
    finally:
        t.close()


def digests(acct):
    return {path.name.upper(): hashlib.sha256(path.read_bytes()).hexdigest() for path in acct.iterdir()
            if path.is_file() and path.suffix.upper() in (".DBF", ".FPT")}


def be(day: dt.date) -> str:
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def money(value) -> Decimal:
    return Decimal(repr(float(value or 0))).quantize(Decimal("0.01"))


def payload(doctype, docdate, ref, accounts, supplier, items, base, vat, *, vat_fields=None):
    total = base + vat
    debit, credit = accounts["fallback_acc"], accounts["ap_acc"]
    lines = [{"acc": debit, "side": "D", "amount": f"{base:.2f}", "desc": "ซื้อ"}]
    if vat:
        lines.append({"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"})
    lines.append({"acc": credit, "side": "C", "amount": f"{total:.2f}", "desc": "เจ้าหนี้"})
    out = {"doctype": doctype, "account_set": "DATAT", "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
           "ref_no": ref, "vat_rate": 7.0, "base_amount": f"{base:.2f}", "vat_amount": f"{vat:.2f}",
           "total_amount": f"{total:.2f}", "items": items, "items_status": "ok", "items_account": debit, "lines": lines,
           "supplier": {"code": supplier, "name": "", "tax_id": "", "prename": "", "supplier_new": False}}
    if vat_fields:
        out["vat_fields"] = vat_fields
    return out


def item(code, name, amount, exempt=False):
    return {"name": name[:50], "code": code, "qty": "1", "unit_price": f"{amount:.2f}", "amount": f"{amount:.2f}",
            "unit": "", "item_mode": "non_stock_item", **({"vat_exempt": True} if exempt else {})}


def check(acct: Path) -> dict:
    info = rows(acct, "ISINFO", ("ACCNUM00", "VAT_INITEM"))
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    accounts = account_probe.probe_default_accounts(acct)
    need = ("ap_acc", "vat_input_acc", "fallback_acc")
    if any(not accounts.get(name) for name in need):
        return {"state": "skip", "why": f"default accounts missing: {[n for n in need if not accounts.get(n)]}"}
    cash = str((info[0].get("ACCNUM00") if info else "") or "").strip()
    services = [r for r in rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP")) if str(r["STKTYP"]) in ("3", "4", "5") and r["STKCOD"]]
    if not services:
        return {"state": "skip", "why": "no non-stock item"}
    suppliers = [row["SUPCOD"] for row in rows(acct, "APMAS", ("SUPCOD",)) if row["SUPCOD"]]
    if not suppliers:
        return {"state": "skip", "why": "no supplier"}
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    a, b = services[0], services[min(1, len(services) - 1)]
    files_before = digests(acct)
    touched = {*S.PURCHASE_BACKUP_TABLES, *S.LOCATION_TABLES}
    index_before = cdx_check.snapshot(cdx_reindex, acct, touched) if with_runner else {}
    whole = {"AMT01": "0.00", "AMTRAT0": "150.00"}
    cases = [
        ("RR", "2", "EX-RR", [item(a["STKCOD"], a["STKDES"], Decimal("150"), True)], Decimal("150"), Decimal("0"), whole, None),
        ("HP", "1", "EX-HP", [item(a["STKCOD"], a["STKDES"], Decimal("150"), True)], Decimal("150"), Decimal("0"), whole, None),
        ("RR", "2", "MIX-RR", [item(a["STKCOD"], a["STKDES"], Decimal("100")), item(b["STKCOD"], b["STKDES"], Decimal("50"), True)],
         Decimal("150"), Decimal("7"), {"AMT01": "100.00", "AMTRAT0": "50.00"}, (Decimal("100"), Decimal("7"), Decimal("50"))),
        ("HP", "0", "NV-HP", [item(a["STKCOD"], a["STKDES"], Decimal("150"))], Decimal("150"), Decimal("0"), None, None),
    ]
    problems, docnums, zero_col = [], [], None
    for doctype, flgvat, ref, items, base, vat, fields, record in cases:
        acc = dict(accounts)
        if doctype == "HP" and cash:
            acc["ap_acc"] = cash  # 现购贷现金：账套默认现金科目 ISINFO.ACCNUM00（同原生）
        res = W.write_purchase(payload(doctype, be(day), ref, acc, suppliers[0], items, base, vat, vat_fields=fields),
                               acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False)
        if not res.ok:
            problems.append(f"{ref} {res.error_code}: {res.error}")
            continue
        docnums.append(res.docnum)
        head = rows(acct, "APTRN", ("FLGVAT", "VATAMT", "AMTRAT0", "NETAMT", "SRV_VATTYP"), DOCNUM=res.docnum)[0]
        zero_col = head["AMTRAT0"] is not None
        if head["FLGVAT"] != flgvat:
            problems.append(f"{ref} FLGVAT {head['FLGVAT']!r} != {flgvat!r}")
        if money(head["VATAMT"]) != vat:
            problems.append(f"{ref} VATAMT {head['VATAMT']} != {vat}")
        want_zero = Decimal(fields["AMTRAT0"]) if fields else Decimal(0)
        if zero_col and money(head["AMTRAT0"]) != want_zero:
            problems.append(f"{ref} head AMTRAT0 {head['AMTRAT0']} != {want_zero}")
        if fields and head["SRV_VATTYP"] not in (None, "1"):
            problems.append(f"{ref} SRV_VATTYP {head['SRV_VATTYP']!r} != '1'")
        lines = rows(acct, "STCRD", ("VATCOD", "TRNVAL", "NETVAL", "SEQNUM"), DOCNUM=res.docnum)
        lines.sort(key=lambda r: int(str(r["SEQNUM"] or 0).strip() or 0))
        for it, line in zip(items, lines):
            if it.get("vat_exempt") and ((line["VATCOD"] not in (None, "0")) or money(line["NETVAL"]) != money(line["TRNVAL"])):
                problems.append(f"{ref} exempt line VATCOD {line['VATCOD']!r} NETVAL {line['NETVAL']} TRNVAL {line['TRNVAL']}")
        vats = rows(acct, "ISVAT", ("AMT01", "VAT01", "AMTRAT0"), DOCNUM=res.docnum)
        if record is None:
            if vats:
                problems.append(f"{ref} unexpected ISVAT rows {len(vats)}")
        else:
            got = [(money(v["AMT01"]), money(v["VAT01"]), money(v["AMTRAT0"]) if v["AMTRAT0"] is not None else None) for v in vats]
            want = [(record[0], record[1], record[2] if vats and vats[0]["AMTRAT0"] is not None else None)]
            if got != want:
                problems.append(f"{ref} ISVAT {got} != {want}")
    written = {name.upper() + ext for name in (*S.PURCHASE_BACKUP_TABLES, *S.LOCATION_TABLES) for ext in (".DBF", ".FPT")}
    files_after = digests(acct)
    untouched = [name for name in files_before if name not in written and files_after.get(name) != files_before[name]]
    if untouched:
        problems.append(f"tables outside the write set changed: {untouched[:5]}")
    if with_runner and docnums:
        problems.extend(cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, docnums))
    return {"state": "fail" if problems else "ok", "why": "; ".join(problems), "docnums": docnums, "amtrat0_column": zero_col,
            "vat_initem": (info[0].get("VAT_INITEM") if info else None), "cdx_checked": sorted(index_before)}


if __name__ == "__main__":
    sys.exit(main())
