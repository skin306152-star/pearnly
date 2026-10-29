#!/usr/bin/env python3
"""CC 直写进货单服务品进项税口径（APTRN.SRV_VATTYP，service_vat，10-29）的真账副本离线回归。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码写几张服务品进货 → 核：
- 这本没有赊购历史（T 账套是清空的）、载荷没带人选的口径：赊购单 RR 不写，回 SERVICE_VAT_BASIS_CHOICE，账套一个字节都不变；
- 载荷带人选的 ``srv_vattyp``（cash_basis）：写成，价格类型 2、SRV_VATTYP '2'；
- 同一家再来一张、不带选择：仍不写（Pearnly 自己写的单不算历史）；
- 账套里补一张 Express 人录的赊购单（另一家供应商、口径 2）当历史：那家的服务品赊购单不带选择也写成 '2'；
- 现购单 HP：SRV_VATTYP '1'；没有税的赊购单：价格类型 0、SRV_VATTYP '-'；都不看历史、不问；
- 写入集合以外的表逐字节不变；给了 ``--runner`` 照正式 CC 重建索引并核 CDX 与 DBF 一致。
明细用这本账套已有的非库存品（服务品）按码写，不建主档。源账套只读（只 copytree 出来），工作副本用完删掉。不连网络、不碰服务器。

    python3 scripts/express-service-vat-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" \\
        --work C:\\PearnlyStress\\_srv_reg --companion <仓库>\\companion\\src --out result.jsonl [--runner <cdx_reindex_runner.exe>]
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
    global dbf, S, W, fiscal_period, account_probe, cdx_reindex, cdx_check, service_vat, with_runner
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, fiscal_period, service_vat  # noqa: F401
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
            if path.is_file() and path.suffix.upper() in (".DBF", ".FPT", ".CDX")}


def be(day: dt.date) -> str:
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def payload(doctype, docdate, ref, accounts, supplier, service, vat, chosen=""):
    base = Decimal("100")
    total = base + vat
    lines = [{"acc": accounts["fallback_acc"], "side": "D", "amount": f"{base:.2f}", "desc": "ค่าบริการ"}]
    if vat:
        lines.append({"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"})
    lines.append({"acc": accounts["ap_acc"], "side": "C", "amount": f"{total:.2f}", "desc": "เจ้าหนี้"})
    out = {"doctype": doctype, "account_set": "DATAT", "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
           "ref_no": ref, "vat_rate": 7.0 if vat else 0.0, "base_amount": f"{base:.2f}", "vat_amount": f"{vat:.2f}",
           "total_amount": f"{total:.2f}", "items_status": "ok", "items_account": accounts["fallback_acc"], "lines": lines,
           "items": [{"name": service["STKDES"][:50] or service["STKCOD"], "code": service["STKCOD"], "qty": "1",
                      "unit_price": f"{base:.2f}", "amount": f"{base:.2f}", "unit": "", "item_mode": "non_stock_item"}],
           "supplier": {"code": supplier, "name": "", "tax_id": "", "prename": "", "supplier_new": False}}
    if chosen:
        out[service_vat.PAYLOAD_KEY] = chosen
    return out


def seed_history(acct, supplier):
    """补一张 Express 人录的赊购单当历史（只写工作副本）：RECTYP 3、价格类型 2、口径 2、不是 Pearnly 写的。"""
    t = dbf.Table(str(acct / "APTRN.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_WRITE)
    try:
        t.append({"RECTYP": "3", "DOCNUM": "ZZHIST-001", "SUPCOD": supplier, "FLGVAT": "2", "SRV_VATTYP": "2",
                  "USERID": "ACC", "DOCSTAT": "N"})
    finally:
        t.close()


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
    if len(suppliers) < 2:
        return {"state": "skip", "why": "fewer than two suppliers"}
    history = service_vat.history(acct)
    if history:
        return {"state": "skip", "why": f"book already has credit purchase history: {len(history)} suppliers"}
    first = calendar.periods[0]
    day = be(first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days)))
    service, problems, docnums = services[0], [], []
    files_before = digests(acct)
    touched = {*S.PURCHASE_BACKUP_TABLES, *S.LOCATION_TABLES}
    index_before = cdx_check.snapshot(cdx_reindex, acct, touched) if with_runner else {}
    seven = Decimal("7")

    def write(ref, doctype, supplier, vat, chosen=""):
        acc = dict(accounts)
        if doctype == "HP" and cash:
            acc["ap_acc"] = cash  # 现购贷现金：账套默认现金科目 ISINFO.ACCNUM00（同原生）
        return W.write_purchase(payload(doctype, day, ref, acc, supplier, service, vat, chosen), acct,
                                allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False)

    def expect(res, ref, flgvat, srv):
        if not res.ok:
            problems.append(f"{ref} {res.error_code}: {res.error}")
            return
        docnums.append(res.docnum)
        head = rows(acct, "APTRN", ("FLGVAT", "SRV_VATTYP"), DOCNUM=res.docnum)[0]
        if (head["FLGVAT"], head["SRV_VATTYP"]) != (flgvat, srv):
            problems.append(f"{ref} FLGVAT/SRV {head['FLGVAT']!r}/{head['SRV_VATTYP']!r} != {flgvat!r}/{srv!r}")

    res = write("SV-NONE", "RR", suppliers[0], seven)
    if res.ok or res.error_code != service_vat.CHOICE_CODE:
        problems.append(f"SV-NONE expected {service_vat.CHOICE_CODE}, got ok={res.ok} {res.error_code}")
    if digests(acct) != files_before:
        problems.append("SV-NONE changed the book")
    expect(write("SV-CHOSEN", "RR", suppliers[0], seven, "cash_basis"), "SV-CHOSEN", "2", "2")
    res = write("SV-AGAIN", "RR", suppliers[0], seven)
    if res.ok or res.error_code != service_vat.CHOICE_CODE:
        problems.append(f"SV-AGAIN (Pearnly-written row is no history) got ok={res.ok} {res.error_code}")
    expect(write("SV-HP", "HP", suppliers[0], seven), "SV-HP", "1", "1")
    expect(write("SV-NOVAT", "RR", suppliers[0], Decimal("0")), "SV-NOVAT", "0", "-")
    seed_history(acct, suppliers[1])
    expect(write("SV-HIST", "RR", suppliers[1], seven), "SV-HIST", "2", "2")
    written = {name.upper() + ext for name in (*S.PURCHASE_BACKUP_TABLES, *S.LOCATION_TABLES) for ext in (".DBF", ".FPT", ".CDX")}
    files_after = digests(acct)
    untouched = [name for name in files_before if name not in written and files_after.get(name) != files_before[name]]
    if untouched:
        problems.append(f"tables outside the write set changed: {untouched[:5]}")
    if with_runner and docnums:
        problems.extend(cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, docnums))
    return {"state": "fail" if problems else "ok", "why": "; ".join(problems), "docnums": docnums,
            "vat_initem": (info[0].get("VAT_INITEM") if info else None), "cdx_checked": sorted(index_before)}


if __name__ == "__main__":
    sys.exit(main())
