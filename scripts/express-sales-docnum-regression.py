#!/usr/bin/env python3
"""CC 写销货的单号取法（K2，11-23）在真账副本上的离线回归。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码写几张只有表头的销货（不带库存行），票面号分四种：
- 以赊销默认簿前缀开头（``IV`` + 序号）→ 单号 = 票面号，这本簿的计数器加一；
- 不以任何一本赊销簿前缀开头（``SSIVA02001``，11-16 69SINCER 那样）→ 单号 = 默认簿前缀 + ISRUN 原来的下一个号（号用过就往后跳），
  计数器记成单号之后那个号；
- 现销 HS 用 ``SSHS000001`` → 同上，用现销默认簿；
- 账套有第二本赊销簿（NLTTEST 的 IO、T9WNF 的 TS …）时，以那本前缀开头的票号 → 单号 = 票面号、只动那本的计数器。
每张都核：ARTRN.YOUREF 与 ISVAT.REFNUM（有这一行时）= 票面号；单号以这个单别某本 ISRUN 簿的前缀开头（Express 按前缀找 ISRUN，
找不到会弹 Message 43）。没有 IV 簿、没有可记账的现金 / 收入科目、没有客户档或会计期间的账套跳过并记原因。源账套只读（只 copytree 出来），
工作副本用完删掉。

    python scripts/express-sales-docnum-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_k2 \\
        --companion <仓库>\\companion\\src --out result.jsonl
只给测试机 C 盘的 T 账套（或其他测试副本）用。
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
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
    args = parser.parse_args(argv)
    sys.path.insert(0, args.companion)
    global dbf, S, WS, account_rules, fiscal_period
    import dbf  # noqa: F401
    from companion import account_rules, fiscal_period  # noqa: F401
    from companion import dbf_sales as WS  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401

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
            print(result["state"], book.name, str(result.get("why", ""))[:200], flush=True)
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


def be(day):
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def accounts(acct):
    """现金（ISINFO.ACCNUM00）、收入（科目表里第一个可记账的 4 字头）、销项税（名称带 ภาษีขาย 的可记账科目，没有就按不含税写）。"""
    cash = account_rules.default_account(acct, "ACCNUM00")
    chart = [r for r in rows(acct, "GLACC", ("ACCNUM", "ACCNAM", "ACCTYP")) if text(r["ACCTYP"]) == "0"]
    revenue = next((text(r["ACCNUM"]) for r in chart if text(r["ACCNUM"]).startswith("4")), "")
    vat = next((text(r["ACCNUM"]) for r in chart if "ภาษีขาย" in text(r["ACCNAM"])), "")
    return cash if account_rules.postable_account(acct, cash) else "", revenue, vat


def isrun(acct):
    return [{"doctyp": text(r["DOCTYP"]), "prefix": text(r["PREFIX"]), "running": text(r["DOCNUM"])}
            for r in rows(acct, "ISRUN", ("DOCTYP", "PREFIX", "DOCNUM")) if text(r["PREFIX"])]


def payload(acct, doctype, day, ticket, customer, cash, revenue, vat_acc):
    rate = 7 if vat_acc else 0
    base = Decimal("100.00")
    vat = (base * rate / 100).quantize(Decimal("0.01"))
    total = base + vat
    lines = [{"acc": cash, "side": "D", "amount": f"{total:.2f}", "desc": customer},
             {"acc": revenue, "side": "C", "amount": f"{base:.2f}", "desc": "รายได้จากการขาย"}]
    if rate:
        lines.append({"acc": vat_acc, "side": "C", "amount": f"{vat:.2f}", "desc": "ภาษีขาย"})
    return {"payload_version": 4, "direction": "sales", "doctype": doctype, "account_set": acct.name, "docdate_be": be(day),
            "vat_period_be": be(day)[:4] + "01", "ref_no": ticket,
            "customer": {"code": customer, "name": customer, "tax_id": "", "prename": "", "customer_new": False},
            "vat_rate": float(rate), "base_amount": f"{base:.2f}", "vat_amount": f"{vat:.2f}", "total_amount": f"{total:.2f}",
            "lines": lines}


def expect_native(acct, book, used):
    """原生留空单号时 Express 会给的号：簿前缀 + ISRUN 下一个号，号用过就往后跳。"""
    if not book["running"].isdigit():
        return None
    width, number = len(book["running"]), int(book["running"])
    while f"{book['prefix']}{str(number).zfill(width)}" in used:
        number += 1
    return f"{book['prefix']}{str(number).zfill(width)}", str(number + 1).zfill(width)


def check(acct: Path) -> dict:
    books = isrun(acct)
    iv = [b for b in books if b["doctyp"] == "IV"]
    hs = [b for b in books if b["doctyp"] == "HS"]
    iv_default = next((b for b in iv if b["prefix"] == "IV"), None)
    if iv_default is None:
        return {"state": "skip", "why": "no ISRUN book IV"}
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

    cases = [("IV", "IVK2-000001", "ticket", iv_default), ("IV", "SSIVA02001", "native", iv_default)]
    hs_default = next((b for b in hs if b["prefix"] == "HS"), hs[0] if hs else None)
    if hs_default is not None:
        cases.append(("HS", "SSHS000001", "native", hs_default))
    other = next((b for b in iv if b["prefix"] != "IV" and len(b["prefix"]) == 2 and b["prefix"].isalpha()), None)
    if other is not None:
        cases.append(("IV", f"{other['prefix']}K2-0001", "ticket", other))
    done = []
    for doctype, ticket, kind, book in cases:
        before = {(b["doctyp"], b["prefix"]): b["running"] for b in isrun(acct)}
        book = next(b for b in isrun(acct) if (b["doctyp"], b["prefix"]) == (book["doctyp"], book["prefix"]))
        used = {text(r["DOCNUM"]) for r in rows(acct, "ARTRN", ("DOCNUM",))}
        if kind == "native" and any(len(b["prefix"]) == 2 and ticket[:2] == b["prefix"] for b in isrun(acct) if b["doctyp"] == doctype):
            return {"state": "fail", "why": f"case {ticket} collides with a {doctype} book prefix"}
        want = ticket if kind == "ticket" else None
        native = expect_native(acct, book, used) if kind == "native" else None
        if kind == "native" and native is None:
            return {"state": "fail", "why": f"{doctype} book {book['prefix']} has no usable running number {book['running']!r}"}
        if native:
            want = native[0]
        res = WS.write_sale(payload(acct, doctype, day, ticket, customer, cash, revenue, vat_acc), acct,
                            allowed_sets=(acct.name,), selected_dir=str(acct), make_backup=False)
        if not res.ok:
            return {"state": "fail", "why": f"{doctype} {ticket}: {res.error_code} {res.error}", "done": done}
        if res.docnum != want:
            return {"state": "fail", "why": f"{doctype} {ticket}: docnum {res.docnum!r}, want {want!r}", "done": done}
        if not any(res.docnum.startswith(b["prefix"]) for b in isrun(acct) if b["doctyp"] == doctype):
            return {"state": "fail", "why": f"{doctype} {ticket}: docnum {res.docnum!r} starts with no {doctype} book prefix"}
        head = [r for r in rows(acct, "ARTRN", ("DOCNUM", "YOUREF")) if text(r["DOCNUM"]) == res.docnum]
        if len(head) != 1 or text(head[0]["YOUREF"]) != ticket:
            return {"state": "fail", "why": f"{doctype} {ticket}: ARTRN {head}"}
        vat_rows = [r for r in rows(acct, "ISVAT", ("DOCNUM", "REFNUM")) if text(r["DOCNUM"]) == res.docnum]
        if any(text(r["REFNUM"]) != ticket for r in vat_rows):
            return {"state": "fail", "why": f"{doctype} {ticket}: ISVAT {vat_rows}"}
        after = {(b["doctyp"], b["prefix"]): b["running"] for b in isrun(acct)}
        key = (book["doctyp"], book["prefix"])
        if kind == "native":
            want_running = native[1]
        else:
            current = before[key]
            want_running = str(int(current) + 1).zfill(len(current)) if current.isdigit() else current
        if after[key] != want_running:
            return {"state": "fail", "why": f"{doctype} {ticket}: ISRUN {key} {before[key]} -> {after[key]}, want {want_running}"}
        moved = [k for k in after if k != key and after[k] != before.get(k)]
        if moved:
            return {"state": "fail", "why": f"{doctype} {ticket}: other ISRUN books moved {moved}"}
        done.append({"doctype": doctype, "ticket": ticket, "docnum": res.docnum, "isvat": len(vat_rows), "book": book["prefix"]})
    return {"state": "ok", "done": done, "iv_books": [b["prefix"] for b in iv], "hs_books": [b["prefix"] for b in hs]}


if __name__ == "__main__":
    sys.exit(main())
