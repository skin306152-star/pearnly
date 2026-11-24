#!/usr/bin/env python3
"""直写无明细买卖单补一行「编码 = 科目码」服务品（11-24）的真账副本离线回归：新旧写法逐字段断言。

起因：直写的「只有单头」单（无明细 RR / HP / IV / HS）在 Express 里「แก้ไข → 保存」会被清零（测试机2 69EXP\\TEST 实测）。
现在无明细票补一行科目服务品（``amount_only.line``）。对每本账套：整本复制两份 → A 份按旧写法（补行关掉 = 只有单头）、B 份按新写法，
各写同一张无明细赊购 RR 与赊销 IV，断言：

- 单头（APTRN / ARTRN）、凭证（GLJNL）、分录（GLJNLIT 有效行）、税记录（ISVAT）、应付 / 应收（APBAL / ARBAL / APMAS / ARMAS）
  A、B 逐字段相同（NXTSEQ 两边都是 '1'）；
- 明细 STCRD：A 没有这张单的行；B 正好 1 行，STKCOD = 科目码、数量 1、净额 = 税前；
- 服务品：B 里有且只有一件编码 = 科目码的非库存品（原来就有的照用、不改；没有的新建 3 型、ACCCOD 经 ISACC 联到同一科目）；
- 同码已是库存品：B 回 ``ACCOUNT_CODE_IS_STOCK_ITEM``、整本不变（A 照旧写只有单头）；
- 除写入集合外，A、B 其余表逐字节相同。
给了 ``--runner`` 就照正式 CC 重建索引，并核写入集合的 CDX 与 DBF 一致（``express_cdx_check``）。
账套没有 ISPRD、读不出默认科目、没有供应商 / 客户时跳过并记原因。源账套只读（只 copytree 出来），工作副本用完删掉。

    python3 scripts/express-amount-only-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" \\
        --work C:\\PearnlyStress\\_amount_only --companion <仓库>\\companion\\src --out result.jsonl [--runner <cdx_reindex_runner.exe>]
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

WRITE_SET = {"ISRUN", "APTRN", "ARTRN", "ISVAT", "GLJNL", "GLJNLIT", "APBAL", "ARBAL", "APMAS", "ARMAS", "STCRD", "STBAL",
             "STMAS", "ISACC", "GLBAL"}
SAME_TABLES = (("APTRN", "DOCNUM"), ("ARTRN", "DOCNUM"), ("GLJNL", "VOUCHER"), ("GLJNLIT", "VOUCHER"), ("ISVAT", "DOCNUM"))
PARTY_TABLES = ("APBAL", "ARBAL", "APMAS", "ARMAS")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--pattern", default="T*")
    parser.add_argument("--work", required=True)
    parser.add_argument("--companion", required=True, help="companion/src 目录")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--runner", default="")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    global dbf, S, W, WS, P, AO, account_probe, fiscal_period, cdx_reindex, cdx_check, with_runner
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, fiscal_period  # noqa: F401
    from companion import amount_only as AO  # noqa: F401
    from companion import dbf_sales as WS  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_writer as W  # noqa: F401
    from companion import product_master as P  # noqa: F401
    import express_cdx_check as cdx_check  # noqa: F401
    with_runner = cdx_check.use_runner(cdx_reindex, args.runner)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    P.BACKFILL_FILE = work / "account_items.json"
    books = sorted(p for p in Path(args.source).iterdir() if p.is_dir() and fnmatch.fnmatch(p.name, args.pattern))
    if args.limit:
        books = books[:args.limit]
    counts = {"ok": 0, "fail": 0, "skip": 0}
    with open(args.out, "w", encoding="utf-8") as out:
        for book in books:
            old, new = work / (book.name + ".old"), work / (book.name + ".new")
            for path in (old, new):
                shutil.rmtree(path, ignore_errors=True)
            try:
                shutil.copytree(book, old)
                shutil.copytree(book, new)
                result = check(old, new, book.name)
            except Exception:
                result = {"state": "fail", "why": traceback.format_exc()[-800:]}
            finally:
                for path in (old, new):
                    shutil.rmtree(path, ignore_errors=True)
            result["book"] = book.name
            counts[result["state"]] += 1
            out.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            out.flush()
            print(result["state"], book.name, str(result.get("why", ""))[:200], flush=True)
    print(json.dumps(counts))
    return 1 if counts["fail"] else 0


def table_rows(acct, table, key=None, value=None, live=True):
    path = next((p for p in Path(acct).iterdir() if p.name.upper() == f"{table}.DBF"), None)
    if path is None:
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        out = []
        for r in (S.live_rows(t) if live else t):
            row = {f.upper(): _value(r, f) for f in t.field_names}
            if key is None or str(row.get(key) or "").strip() == value:
                out.append(row)
        return out
    finally:
        t.close()


def _value(record, field):
    """真实账套有坏字节（数值栏只有一个「.」等）：读不出的栏记成原始字节的文本，照样能比。"""
    try:
        return record[field]
    except (ValueError, TypeError):
        return "<unreadable>"


def text(value):
    return str(value or "").strip()


def digests(acct, skip=()):
    return {p.name.upper(): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(acct).iterdir()
            if p.is_file() and p.stem.upper() not in skip}


def docdate_of(acct):
    calendar = fiscal_period.load(acct)
    if not calendar:
        return ""
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def purchase(acct, docdate, accounts, supplier):
    base, vat = Decimal("1000.00"), Decimal("70.00")
    acc = accounts["fallback_acc"]
    return {"doctype": "RR", "account_set": Path(acct).name, "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
            "ref_no": "REG-AO-RR", "vat_rate": 7.0, "base_amount": f"{base:.2f}", "vat_amount": f"{vat:.2f}",
            "total_amount": f"{base + vat:.2f}", "srv_vattyp": "1",
            "supplier": {"code": supplier, "name": "", "tax_id": "", "prename": "", "supplier_new": False},
            "lines": [{"acc": acc, "side": "D", "amount": f"{base:.2f}", "desc": "ซื้อ"},
                      {"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"},
                      {"acc": accounts.get("ap_acc") or acc, "side": "C", "amount": f"{base + vat:.2f}", "desc": "เจ้าหนี้"}],
            "items": [], "items_status": "empty", "items_account": acc}


def sale(acct, docdate, accounts, customer):
    base, vat = Decimal("1000.00"), Decimal("70.00")
    acc = accounts["revenue_acc"]
    return {"direction": "sales", "doctype": "IV", "account_set": Path(acct).name, "docdate_be": docdate,
            "vat_period_be": docdate[:4] + "01", "ref_no": "REG-AO-IV", "vat_rate": 7.0, "base_amount": f"{base:.2f}",
            "vat_amount": f"{vat:.2f}", "total_amount": f"{base + vat:.2f}", "srv_vattyp": "1",
            "customer": {"code": customer, "name": "", "tax_id": "", "prename": "", "customer_new": False},
            "lines": [{"acc": accounts.get("ar_acc") or acc, "side": "D", "amount": f"{base + vat:.2f}", "desc": "ลูกหนี้"},
                      {"acc": acc, "side": "C", "amount": f"{base:.2f}", "desc": "รายได้"},
                      {"acc": accounts["vat_output_acc"], "side": "C", "amount": f"{vat:.2f}", "desc": "ภาษีขาย"}],
            "items": [], "items_status": "empty", "items_account": acc}


def write(kind, acct, payload):
    writer = W.write_purchase if kind == "RR" else WS.write_sale
    payload = dict(payload, account_set=Path(acct).name)  # 两份副本目录名不同：账套名闸按各自的目录名
    return writer(payload, acct, allowed_sets=(Path(acct).name,), selected_dir=str(acct), make_backup=True)


def old_write(kind, acct, payload):
    real = AO.line
    AO.line = lambda *_a, **_k: None  # 旧写法：不补行（只有单头）
    try:
        return write(kind, acct, payload)
    finally:
        AO.line = real


def comparable(row):
    return {k: v for k, v in row.items() if k not in ("CHGTIM",)}


def compare_doc(kind, old, new, docnum, account, made_before):
    problems = []
    for table, key in SAME_TABLES:
        a = [comparable(r) for r in table_rows(old, table, key, docnum)]
        b = [comparable(r) for r in table_rows(new, table, key, docnum)]
        if a != b:
            problems.append(f"{table} differs for {docnum}: old {len(a)} rows, new {len(b)} rows")
    for table in PARTY_TABLES:
        if table_rows(old, table) != table_rows(new, table):
            problems.append(f"{table} differs")
    if table_rows(old, "STCRD", "DOCNUM", docnum):
        problems.append(f"old write has STCRD rows for {docnum}")
    lines = table_rows(new, "STCRD", "DOCNUM", docnum)
    if len(lines) != 1 or text(lines[0].get("STKCOD")) != account or float(lines[0].get("TRNQTY") or 0) != 1.0:
        problems.append(f"new STCRD for {docnum}: {[(text(r.get('STKCOD')), r.get('TRNQTY')) for r in lines]}")
    items = [r for r in table_rows(new, "STMAS") if text(r.get("STKCOD")) == account]
    if len(items) != 1 or text(items[0].get("STKTYP")) not in P._NONSTOCK_TYPES:
        problems.append(f"items coded {account}: {[(text(r.get('STKCOD')), text(r.get('STKTYP'))) for r in items]}")
    elif made_before is not None and comparable(items[0]) != comparable(made_before):
        problems.append(f"the existing item {account} was changed")
    return problems


def check(old: Path, new: Path, name: str) -> dict:
    docdate = docdate_of(new)
    if not docdate:
        return {"state": "skip", "why": "no ISPRD"}
    accounts = account_probe.probe_default_accounts(new) or {}
    supplier = text((table_rows(new, "APMAS") or [{}])[0].get("SUPCOD"))
    customer = text((table_rows(new, "ARMAS") or [{}])[0].get("CUSCOD"))
    cases = []
    if accounts.get("fallback_acc") and accounts.get("vat_input_acc") and supplier:
        cases.append(("RR", purchase(new, docdate, accounts, supplier), accounts["fallback_acc"]))
    if accounts.get("revenue_acc") and accounts.get("vat_output_acc") and customer:
        cases.append(("IV", sale(new, docdate, accounts, customer), accounts["revenue_acc"]))
    if not cases:
        return {"state": "skip", "why": "no default accounts or parties"}
    index_before = cdx_check.snapshot(cdx_reindex, new, WRITE_SET) if with_runner else {}
    done, problems, docs = [], [], []
    for kind, payload, account in cases:
        existing = [r for r in table_rows(new, "STMAS") if text(r.get("STKCOD")) == account]
        whole_before = digests(new)
        a = old_write(kind, old, json.loads(json.dumps(payload)))
        b = write(kind, new, json.loads(json.dumps(payload)))
        if existing and text(existing[0].get("STKTYP")) not in P._NONSTOCK_TYPES:
            if b.ok or b.error_code != P.ERR_ACCOUNT_ITEM_STOCK or digests(new) != whole_before:
                problems.append(f"{kind}: stock item coded {account} but new write gave {b.ok} {b.error_code}")
            done.append(f"{kind}:stock_refused")
            continue
        if not (a.ok and b.ok):
            if not a.ok and not b.ok and a.error_code == b.error_code:
                done.append(f"{kind}:both_refused:{a.error_code}")
                continue
            problems.append(f"{kind}: old {a.ok} {a.error_code} / new {b.ok} {b.error_code} {b.error}"[:300])
            continue
        if a.docnum != b.docnum:
            problems.append(f"{kind}: docnum old {a.docnum} new {b.docnum}")
            continue
        problems += [f"{kind}: {p}" for p in compare_doc(kind, old, new, b.docnum, account, existing[0] if existing else None)]
        done.append(f"{kind}:{'reused' if existing else 'made'}")
        docs.append(b.docnum)
    outside_old = {k: v for k, v in digests(old, WRITE_SET).items()}
    outside_new = {k: v for k, v in digests(new, WRITE_SET).items()}
    if outside_old != outside_new:
        problems.append(f"tables outside the write set differ: {sorted(k for k in outside_new if outside_old.get(k) != outside_new[k])}")
    if with_runner and docs:
        problems += cdx_check.problems(cdx_reindex, dbf, S, new, index_before, docs)
    return {"state": "fail" if problems else "ok", "cases": done, "why": "; ".join(problems)[:1500], "docs": docs}


if __name__ == "__main__":
    sys.exit(main())
