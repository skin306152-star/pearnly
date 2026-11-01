#!/usr/bin/env python3
"""代录开工前建主档（``express_operator.masters``，主会话 10-27 ③）的真账副本离线回归。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 代码只建主档，不写单据：
- 进货（RR）：新供应商 + 一个新非库存品（服务 / 费用行）；
- 进货（RR）：这本账已有的供应商 + 一个新库存品（账里没有库存品可抄结构时，同直写一样要人选存货科目组，记作 ``stock_group``）；
- 销货（IV）：新客户 + 一个新非库存品（收入行）。
核：
- 建好的码写回载荷，且在主档表里（活行）查得到；同一张再来一次全部找到、不再建、账套逐字节不变；
- 只动主档表（APMAS / ARMAS / STMAS / ISACC 及其索引），交易表与 ISRUN 等其余表逐字节不变；
- 给了 ``--runner``：照正式 CC 重建索引，核这几张表 CDX 与 DBF 一致（``express_cdx_check``），并按 Express 自己的索引
  查得到新建的码（商品 STMAS1、对方档按代码那条标签）。
源账套只读（只 copytree 出来）；工作副本用完删掉。不连网络、不碰服务器。

    python3 scripts/express-operator-masters-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" \\
        --work C:\\PearnlyStress\\_masters_reg --companion <仓库>\\companion\\src --out result.jsonl [--runner <cdx_reindex_runner.exe>]
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
from types import SimpleNamespace

MASTERS = ("APMAS", "ARMAS", "STMAS", "ISACC")


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
    global dbf, S, masters, account_probe, cdx_reindex, consistency, fiscal_period, cdx_check, with_runner
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, consistency, fiscal_period  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion.express_operator import masters  # noqa: F401
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


def live_codes(acct, table, field):
    path = acct / f"{table}.DBF"
    if not path.is_file():
        return set()
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        return {str(getattr(r, field) or "").strip() for r in S.live_rows(t)}
    finally:
        t.close()


def digests(acct):
    return {path.name.upper(): hashlib.sha256(path.read_bytes()).hexdigest() for path in acct.iterdir()
            if path.is_file() and path.suffix.upper() in (".DBF", ".FPT")}


def be(day: dt.date) -> str:
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def payload(doctype, docdate, accounts, items, *, party):
    amount = sum(Decimal(item["amount"]) for item in items)
    vat = (amount * Decimal("0.07")).quantize(Decimal("0.01"))
    total = amount + vat
    base = {"doctype": doctype, "account_set": "DATAT", "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
            "ref_no": "OP-MASTERS", "vat_rate": 7.0, "base_amount": f"{amount:.2f}", "vat_amount": f"{vat:.2f}",
            "total_amount": f"{total:.2f}", "items": items, "items_status": "ok", "entry_mode": "operator"}
    if doctype == "RR":
        base.update({"supplier": party,
                     "lines": [{"acc": accounts["fallback_acc"], "side": "D", "amount": f"{amount:.2f}", "desc": "ซื้อ"},
                               {"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"},
                               {"acc": accounts["ap_acc"], "side": "C", "amount": f"{total:.2f}", "desc": "เจ้าหนี้"}],
                     "items_account": accounts["fallback_acc"]})
    else:
        base.update({"direction": "sales", "customer": party,
                     "lines": [{"acc": accounts["ar_acc"], "side": "D", "amount": f"{total:.2f}", "desc": "ลูกหนี้"},
                               {"acc": accounts["revenue_acc"], "side": "C", "amount": f"{amount:.2f}", "desc": "รายได้"},
                               {"acc": accounts["vat_output_acc"], "side": "C", "amount": f"{vat:.2f}", "desc": "ภาษีขาย"}],
                     "items_account": accounts["revenue_acc"]})
    return base


def item(name, mode, amount="100.00"):
    return {"name": name, "qty": "1", "unit_price": amount, "amount": amount, "unit": "", "item_mode": mode}


def party(name, *, code=""):
    return {"code": code, "name": name, "tax_id": "", "prename": "", "supplier_new": not code, "customer_new": not code}


def index_misses(acct, created, side_table):
    """按 Express 自己的索引查新建的码：商品 STMAS1；对方档取键表达式就是代码列的那条标签。"""
    misses = []
    for made in created:
        if made["kind"] == "item":
            table, tag = cdx_reindex.TAG_STMAS_CODE
        else:
            table = side_table
            column = "SUPCOD" if table == "APMAS" else "CUSCOD"
            _records, tags = cdx_reindex.tags(acct, table)
            tag = next((name for name, key in tags if key.strip().upper() == column), "")
            if not tag:
                continue
        if not cdx_reindex.seek(acct, table, tag, made["code"]).found:
            misses.append(f"{table}.{tag} cannot find {made['code']}")
    return misses


def check(acct: Path) -> dict:
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    accounts = account_probe.probe_default_accounts(acct)
    need = ("ap_acc", "vat_input_acc", "fallback_acc", "ar_acc", "revenue_acc", "vat_output_acc")
    if any(not accounts.get(name) for name in need):
        return {"state": "skip", "why": f"default accounts missing: {[n for n in need if not accounts.get(n)]}"}
    suppliers = sorted(code for code in live_codes(acct, "APMAS", "SUPCOD") if code)
    if not suppliers:
        return {"state": "skip", "why": "no supplier"}
    first = calendar.periods[0]
    day = be(first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days)))
    settings = SimpleNamespace(account_dir=str(acct), account_set="DATAT", account_company=consistency.read_company(acct))
    docs = {
        "service_purchase": payload("RR", day, accounts, [item("ค่าบริการทดสอบ PEARNLY OP", "non_stock_item")],
                                    party=party("ผู้ขายทดสอบ PEARNLY OP")),
        "stock_purchase": payload("RR", day, accounts, [item("สินค้าทดสอบ PEARNLY OP", "stock_item")],
                                  party=party("", code=suppliers[0])),
        "service_sale": payload("IV", day, accounts, [item("รายได้ทดสอบ PEARNLY OP", "non_stock_item")],
                                party=party("ลูกค้าทดสอบ PEARNLY OP")),
    }
    files_before = digests(acct)
    index_before = cdx_check.snapshot(cdx_reindex, acct, MASTERS) if with_runner else {}
    problems, results, closed = [], {}, []
    for label, doc in docs.items():
        ready = masters.prepare(doc, settings, before_create=lambda: closed.append(label))
        if not ready.ok:
            if label == "stock_purchase" and ready.error_code == "STOCK_ACC_GROUP_UNFIT":
                results[label] = "stock_group"  # 同直写：账里没有库存品可抄结构，要人选一次存货科目组
                continue
            problems.append(f"{label}: {ready.error_code} {ready.error}"[:200])
            continue
        results[label] = [f"{c['kind']}:{c['code']}" for c in ready.created]
        side_table = "APMAS" if doc["doctype"] == "RR" else "ARMAS"
        side = ready.payload["supplier" if side_table == "APMAS" else "customer"]["code"]
        if side not in live_codes(acct, side_table, "SUPCOD" if side_table == "APMAS" else "CUSCOD"):
            problems.append(f"{label}: party {side!r} not in {side_table}")
        for line in ready.payload["items"]:
            if line.get("code") not in live_codes(acct, "STMAS", "STKCOD"):
                problems.append(f"{label}: item {line.get('code')!r} not in STMAS")
        if with_runner:
            problems.extend(f"{label}: {miss}" for miss in index_misses(acct, ready.created, side_table))
        snapshot = digests(acct)
        again = masters.prepare(doc, settings, before_create=lambda: closed.append(label + ":again"))
        if not again.ok or again.created or digests(acct) != snapshot:
            problems.append(f"{label}: second run created again or changed the book ({again.error_code} {again.created})")
    if closed and any(label.endswith(":again") for label in closed):
        problems.append(f"Express closed although nothing was built: {closed}")
    files_after = digests(acct)
    written = {name + ext for name in MASTERS for ext in (".DBF", ".FPT")}
    outside = [name for name in files_before if name not in written and files_after.get(name) != files_before[name]]
    if outside:
        problems.append(f"tables outside the master set changed: {outside[:5]}")
    if with_runner:
        problems.extend(cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, []))
    return {"state": "fail" if problems else "ok", "why": "; ".join(problems), "created": results,
            "cdx_checked": sorted(index_before)}


if __name__ == "__main__":
    sys.exit(main())
