#!/usr/bin/env python3
"""CC 写 Express 时总结存（STMAS.TOTBAL / TOTVAL）与对方余额（APMAS / ARMAS.BALANCE）新旧写法逐字段对比（11-07，
``docs/work/2026-11-07-cc-stock-balance-native-评估.md``）。真账副本离线跑，源账套只读。

分三步，每步一个进程（新旧两份 CC 源码不能在同一个进程里导入）：

1. ``write --companion <旧 src> --dest <工作目录>/old``、``write --companion <新 src> --dest <工作目录>/new``：每本账套整本复制到
   dest 下，在副本里给第一件库存品种上结存（T 账套是清空过的）：``--scenario apart`` 照 TEST 500603 种成总结存 45 / 货值 450、
   批次链 3 / 60（``TOTBAL ≠ MREMBAL``，S 盘 18% 的品如此），``same`` 种成两者都是 45 / 450；主仓结存同 45（有 STLOC 时）。新旧两边种法一模一样。
   再用那份源码在这件品上依次写赊购 RR（+5）、现购 HP（+1）、赊销 IV（−2）、现销 HS（−1）、领用 OU（−1，账套有 OU 单别时），
   同一天、同一个供应商 / 客户。每步结果写进 dest 下的 ``writes.jsonl``。
2. ``compare --old … --new … --out result.jsonl``：两份副本逐表、逐行（含删除标记行）、逐字段比，只许下面这些栏不同：
   - 总是该变：``STMAS.TOTBAL / TOTVAL / UNITPR``、``APMAS.BALANCE / LASRCV``、``ARMAS.BALANCE``；
   - 成本跟着变（只在这件品「总结存均价 ≠ 批次链均价」或「批次链 ≤ 0 而总结存 > 0」时许可）：STCRD 的成本 / 货值栏、STMAS / STCRD 的
     ``MREMVAL``、STLOC 的金额期桶、凭证分录金额、库存单单头货值。
   其余任何一栏不同都算失败。另核新写法：总结存的变化 = 本次数量合计、写前总结存 = 各仓合计的写后仍相等、供应商余额只加赊购那张的含税额、
   客户余额只加赊销那张的含税额。
写完的副本留在工作目录给人看，核完自己删。

    python scripts/express-stock-balance-regression.py write --source C:\\PearnlyExpress\\70EXP --pattern "T*" --companion <旧>\\companion\\src --dest W\\old
    python scripts/express-stock-balance-regression.py write --source C:\\PearnlyExpress\\70EXP --pattern "T*" --companion <新>\\companion\\src --dest W\\new
    python scripts/express-stock-balance-regression.py compare --old W\\old --new W\\new --companion <新>\\companion\\src --out result.jsonl
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

ALWAYS = {("STMAS", "TOTBAL"), ("STMAS", "TOTVAL"), ("STMAS", "UNITPR"), ("APMAS", "BALANCE"), ("APMAS", "LASRCV"),
          ("ARMAS", "BALANCE")}
COST = {("STCRD", "XUNITPR"), ("STCRD", "XTRNVAL"), ("STCRD", "MREMVAL"), ("STCRD", "UNITPR"), ("STCRD", "TRNVAL"),
        ("STCRD", "NETVAL"), ("STCRD", "LUNITPR"), ("STMAS", "MREMVAL"), ("GLJNLIT", "AMOUNT"), ("STTRN", "TRNVAL")}
STEPS = (("RR", 5, "50.00"), ("HP", 1, "10.00"), ("IV", 2, "40.00"), ("HS", 1, "20.00"), ("OU", 1, ""))
NET_QTY = {"RR": 5, "HP": 1, "IV": -2, "HS": -1, "OU": -1, "SVC": 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("write", "compare"))
    parser.add_argument("--source", default="")
    parser.add_argument("--pattern", default="T*")
    parser.add_argument("--companion", required=True, help="companion/src 目录")
    parser.add_argument("--dest", default="")
    parser.add_argument("--old", default="")
    parser.add_argument("--new", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--scenario", choices=("apart", "same"), default="apart")
    parser.add_argument("--userid", default="", help="write：这次写单的录入人（不给 = 写入函数默认的 PEARNLY）")
    parser.add_argument("--allow", default="", help="compare：另外许可不同的栏，逗号分隔 TABLE.FIELD（如录入人 APTRN.USERID）")
    parser.add_argument("--gl", action="store_true",
                        help="compare：分录口径改了（进货库存行照原生直接记科目组科目、销货收入照科目组 ACCNUM03）——凭证明细不逐行比，"
                             "改核每个科目的净额与写法")
    parser.add_argument("--service", action="store_true",
                        help="write：另写一张现销 HS（步骤名 SVC），一行挂这本账里 Express 原有的服务品（科目组配了能记账的收入科目的优先）")
    args = parser.parse_args(argv)
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    global dbf, S
    import dbf  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    return write_all(args) if args.mode == "write" else compare_all(args)


# ── 读表 ────────────────────────────────────────────────────────────────────────


def rows(acct, table, fields=None, deleted=False):
    path = Path(acct) / f"{table}.DBF"
    if not path.is_file():
        return []
    t = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        names = list(fields or t.field_names)
        out = []
        for rec in (t if deleted else S.live_rows(t)):
            item = {"_del": bool(dbf.is_deleted(rec))} if deleted else {}
            for name in names:
                try:
                    value = rec[name]
                except Exception:
                    value = None
                item[name] = value.strip() if isinstance(value, str) else value
            out.append(item)
        return out
    finally:
        t.close()


def num(value) -> Decimal:
    return Decimal(repr(float(value or 0)))


def be(day: dt.date) -> str:
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


# ── 写 ──────────────────────────────────────────────────────────────────────────


def write_all(args) -> int:
    global W, WS, D, fiscal_period, account_probe, stock_lookup
    from companion import account_probe, cdx_reindex, fiscal_period, stock_lookup  # noqa: F401
    from companion import dbf_sales as WS  # noqa: F401
    from companion import dbf_stock as D  # noqa: F401
    from companion import dbf_writer as W  # noqa: F401
    import express_cdx_check as cdx_check
    cdx_check.use_runner(cdx_reindex, "")  # 只比表内容，不重建索引
    books = sorted(p for p in Path(args.source).iterdir() if p.is_dir() and fnmatch.fnmatch(p.name, args.pattern))
    if args.limit:
        books = books[:args.limit]
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    with open(dest / "writes.jsonl", "w", encoding="utf-8") as out:
        for book in books:
            copy = dest / book.name
            shutil.rmtree(copy, ignore_errors=True)
            try:
                shutil.copytree(book, copy)
                result = write_book(copy, args.scenario, args.userid, args.service)
            except Exception:
                result = {"state": "fail", "why": traceback.format_exc()[-600:]}
            result["book"] = book.name
            out.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            out.flush()
            print(result.get("state"), book.name, str(result.get("why", ""))[:160], flush=True)
    return 0


def _update(acct, table, match, fields):
    t = dbf.Table(str(Path(acct) / f"{table}.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_WRITE)
    try:
        names = {n.upper() for n in t.field_names}
        for rec in S.live_rows(t):
            if all(str(rec[k] or "").strip() == v for k, v in match.items()):
                with rec as r:
                    for k, v in fields.items():
                        if k in names:
                            r[k] = v
    finally:
        t.close()


def _seed(acct, main, scenario):
    """在副本里给一件库存品种上结存，返回 (品码, 品名)；有主仓行的品优先。"""
    stock = [r for r in rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP")) if str(r["STKTYP"]) == "0" and r["STKCOD"]]
    if not stock:
        return None
    has_stloc = (Path(acct) / "STLOC.DBF").is_file()
    at_main = {r["STKCOD"]: r for r in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL")) if r["LOCCOD"] == main} if has_stloc else {}
    pick = next((r for r in stock if r["STKCOD"] in at_main), None)
    if pick is None and has_stloc:
        return None  # 主仓还没有任何品的行：CC 会新建，种不出「总结存 = 各仓合计」的起点，跳过
    pick = pick or stock[0]
    lots = (3.0, 60.0) if scenario == "apart" else (45.0, 450.0)
    _update(acct, "STMAS", {"STKCOD": pick["STKCOD"]},
            {"TOTBAL": 45.0, "TOTVAL": 450.0, "MREMBAL": lots[0], "MREMVAL": lots[1], "UNITPR": 10.0, "NEGALLOW": "Y"})
    if has_stloc:
        others = sum((num(r["LOCBAL"]) for r in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL"))
                      if r["STKCOD"] == pick["STKCOD"] and r["LOCCOD"] != main), Decimal(0))
        _update(acct, "STLOC", {"STKCOD": pick["STKCOD"], "LOCCOD": main}, {"LOCBAL": float(Decimal(45) - others)})
    return pick["STKCOD"], pick["STKDES"] or pick["STKCOD"]


def write_book(acct: Path, scenario: str, userid: str = "", service: bool = False) -> dict:
    info = rows(acct, "ISINFO", ("MAINLOC", "ACCNUM00"))
    main = (info[0]["MAINLOC"] if info else "") or ""
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    accounts = account_probe.probe_default_accounts(acct)
    need = ("ap_acc", "vat_input_acc", "fallback_acc", "ar_acc", "revenue_acc", "vat_output_acc")
    if any(not accounts.get(name) for name in need):
        return {"state": "skip", "why": f"default accounts missing: {[n for n in need if not accounts.get(n)]}"}
    cash = str((info[0].get("ACCNUM00") if info else "") or "").strip()
    seeded = _seed(acct, main, scenario)
    if seeded is None:
        return {"state": "skip", "why": "no stock item (or none in the main warehouse yet)"}
    code, name = seeded
    before = [r for r in rows(acct, "STMAS", ("STKCOD", "TOTBAL", "TOTVAL", "MREMBAL", "MREMVAL")) if r["STKCOD"] == code][0]
    suppliers = [r["SUPCOD"] for r in rows(acct, "APMAS", ("SUPCOD",)) if r["SUPCOD"]]
    customers = [r["CUSCOD"] for r in rows(acct, "ARMAS", ("CUSCOD",)) if r["CUSCOD"]]
    if not suppliers or not customers:
        return {"state": "skip", "why": "no supplier or customer"}
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    # 写前的对方余额（不变量按它核；旧代码那份副本未必是写前——旧代码也可能加余额）
    party_before = {table: float(num(next((r["BALANCE"] for r in rows(acct, table, (key, "BALANCE")) if r[key] == code), 0)))
                    for table, key, code in (("APMAS", "SUPCOD", suppliers[0]), ("ARMAS", "CUSCOD", customers[0]))}
    steps = []
    for doctype, qty, amount in STEPS:
        if doctype == "OU":
            if D.book_row(acct, "OU") is None:
                steps.append({"doctype": doctype, "skip": "no OU book"})
                continue
            payload = {"payload_version": 6, "direction": "stock", "doctype": "OU", "docdate_be": be(day),
                       "ref_no": "REG-OU", "items": [{"code": code, "name": name[:50], "qty": str(qty), "unit": ""}]}
            res = D.write_stock_entry(payload, acct, selected_dir=str(acct), make_backup=False, today=day,
                                      **({"userid": userid} if userid else {}))
        else:
            body = payload_for(doctype, be(day), f"REG-{doctype}", accounts, cash, name, code, qty, Decimal(amount),
                               supplier=suppliers[0], customer=customers[0])
            writer = W.write_purchase if doctype in ("RR", "HP") else WS.write_sale
            res = writer(body, acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False,
                         **({"userid": userid} if userid else {}))
        steps.append({"doctype": doctype, "ok": res.ok, "docnum": res.docnum, "error": res.error_code or res.error})
    picked = _service_item(acct, accounts["revenue_acc"]) if service else None
    if service and picked is None:
        steps.append({"doctype": "SVC", "skip": "no service item"})
    elif picked:
        body = payload_for("HS", be(day), "REG-SVC", accounts, cash, picked["STKDES"], "", 1, Decimal("30.00"),
                           supplier=suppliers[0], customer=customers[0])
        body["items"][0].update({"item_mode": "non_stock_item", "code": ""})
        res = WS.write_sale(body, acct, allowed_sets=("DATAT",), selected_dir=str(acct), make_backup=False,
                            **({"userid": userid} if userid else {}))
        steps.append({"doctype": "SVC", "ok": res.ok, "docnum": res.docnum, "error": res.error_code or res.error,
                      "service": picked["STKCOD"]})
    return {"state": "written", "item": code, "revenue_acc": accounts["revenue_acc"], "apart": scenario == "apart", "supplier": suppliers[0], "customer": customers[0],
            "main": main, "steps": steps, "party_before": party_before,
            "before": {k: float(num(before[k])) for k in ("TOTBAL", "TOTVAL", "MREMBAL", "MREMVAL")}}


def _service_item(acct, revenue_acc):
    """这本账里 Express 原有的一件服务品（STKTYP 3 / 4 / 5，有品名）：科目组的收入科目 ACCNUM03 能记账且 ≠ 载荷默认收入的优先。"""
    postable = {r["ACCNUM"] for r in rows(acct, "GLACC", ("ACCNUM", "ACCTYP")) if str(r["ACCTYP"] or "") == "0"}
    groups = {r["ACCCOD"]: r.get("ACCNUM03") or "" for r in rows(acct, "ISACC", ("ACCCOD", "ACCNUM03"))}
    items = [r for r in rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP", "ACCCOD"))
             if str(r["STKTYP"]) in ("3", "4", "5") and r["STKCOD"] and r["STKDES"]]
    return next((r for r in items if groups.get(r["ACCCOD"]) in postable and groups.get(r["ACCCOD"]) != revenue_acc),
                items[0] if items else None)


def payload_for(doctype, docdate, ref, accounts, cash, name, code, qty, amount, *, supplier, customer):
    vat = (amount * Decimal("0.07")).quantize(Decimal("0.01"))
    total = amount + vat
    item = {"name": name[:50], "code": code, "qty": str(qty), "unit_price": str(amount / qty), "amount": f"{amount:.2f}",
            "unit": "", "item_mode": "stock_item" if doctype in ("RR", "HP") else "stock_sale"}
    base = {"doctype": doctype, "account_set": "DATAT", "docdate_be": docdate, "vat_period_be": docdate[:4] + "01",
            "ref_no": ref, "vat_rate": 7.0, "base_amount": f"{amount:.2f}", "vat_amount": f"{vat:.2f}",
            "total_amount": f"{total:.2f}", "items": [item], "items_status": "ok"}
    if doctype in ("RR", "HP"):
        party = accounts["ap_acc"] if doctype == "RR" else (cash or accounts["ap_acc"])
        base.update({"supplier": {"code": supplier, "name": "", "tax_id": "", "prename": "", "supplier_new": False},
                     "lines": [{"acc": accounts["fallback_acc"], "side": "D", "amount": f"{amount:.2f}", "desc": "ซื้อ"},
                               {"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{vat:.2f}", "desc": "ภาษีซื้อ"},
                               {"acc": party, "side": "C", "amount": f"{total:.2f}", "desc": "เจ้าหนี้"}],
                     "items_account": accounts["fallback_acc"]})
    else:
        party = accounts["ar_acc"] if doctype == "IV" else (cash or accounts["ar_acc"])
        base.update({"direction": "sales",
                     "customer": {"code": customer, "name": "", "tax_id": "", "prename": "", "customer_new": False},
                     "lines": [{"acc": party, "side": "D", "amount": f"{total:.2f}", "desc": "ลูกหนี้"},
                               {"acc": accounts["revenue_acc"], "side": "C", "amount": f"{amount:.2f}", "desc": "รายได้"},
                               {"acc": accounts["vat_output_acc"], "side": "C", "amount": f"{vat:.2f}", "desc": "ภาษีขาย"}],
                     "items_account": accounts["revenue_acc"]})
    return base


# ── 比 ──────────────────────────────────────────────────────────────────────────


def compare_all(args) -> int:
    old_dir, new_dir = Path(args.old), Path(args.new)
    old_runs = {r["book"]: r for r in map(json.loads, open(old_dir / "writes.jsonl", encoding="utf-8"))}
    new_runs = {r["book"]: r for r in map(json.loads, open(new_dir / "writes.jsonl", encoding="utf-8"))}
    counts = {"ok": 0, "fail": 0, "skip": 0}
    extra = {tuple(item.strip().upper().split(".", 1)) for item in args.allow.split(",") if "." in item}
    with open(args.out, "w", encoding="utf-8") as out:
        for book in sorted(new_runs):
            try:
                result = compare_book(old_dir / book, new_dir / book, old_runs.get(book) or {}, new_runs[book], extra, args.gl)
            except Exception:
                result = {"state": "fail", "why": traceback.format_exc()[-600:]}
            result["book"] = book
            counts[result["state"]] += 1
            out.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            print(result["state"], book, str(result.get("why", ""))[:200], flush=True)
    print(json.dumps(counts))
    return 1 if counts["fail"] else 0


def _cost_differs(run) -> bool:
    b = run.get("before") or {}
    tot, mrem = Decimal(str(b.get("TOTBAL", 0))), Decimal(str(b.get("MREMBAL", 0)))
    if tot > 0 and mrem <= 0:
        return True
    if tot > 0 and mrem > 0:
        return Decimal(str(b["TOTVAL"])) / tot != Decimal(str(b["MREMVAL"])) / mrem
    return False


def _gl_net(acct: Path, vouchers) -> dict:
    """{凭证号: {科目: 借减贷}}（活行）。"""
    out = {}
    for r in rows(acct, "GLJNLIT", ("VOUCHER", "ACCNUM", "TRNTYP", "AMOUNT", "SEQIT")):
        voucher = str(r["VOUCHER"] or "").strip()
        if voucher not in vouchers:
            continue
        sign = 1 if str(r["TRNTYP"] or "").strip() == "0" else -1
        acc = str(r["ACCNUM"] or "").strip()
        out.setdefault(voucher, {})[acc] = out.get(voucher, {}).get(acc, Decimal(0)) + sign * num(r["AMOUNT"])
    return out


def _gl_problems(old: Path, new: Path, new_run: dict) -> list:
    """进货库存行照原生（11-07）：新写法没有 SEQIT 7 / 8；永续账套每个科目的净额与旧写法相同（余额不变）；定期账套库存行记到科目组的
    购货科目 ACCNUM02（不能记账时账套默认 ACCNUM18），凭证合计不变。"""
    problems = []
    vouchers = {s.get("docnum") for s in new_run.get("steps", []) if s.get("ok") and s.get("doctype") in ("RR", "HP")}
    for r in rows(new, "GLJNLIT", ("VOUCHER", "SEQIT")):
        if str(r["VOUCHER"] or "").strip() in vouchers and str(r["SEQIT"] or "").strip() in ("7", "8"):
            problems.append(f"reclass row SEQIT {r['SEQIT']} in {r['VOUCHER']}")
            break
    info = (rows(new, "ISINFO", ("ISPERPETUA",)) or [{}])[0]
    periodic = str(info.get("ISPERPETUA") or "").strip().upper() == "N"
    a, b = _gl_net(old, vouchers), _gl_net(new, vouchers)
    for voucher in vouchers:
        x, y = a.get(voucher, {}), b.get(voucher, {})
        if abs(sum(y.values(), Decimal(0))) >= Decimal("0.005"):
            problems.append(f"{voucher} not balanced")
        if not periodic and any(abs(x.get(k, Decimal(0)) - y.get(k, Decimal(0))) >= Decimal("0.005") for k in set(x) | set(y)):
            problems.append(f"{voucher} account nets differ in a perpetual book: {dict(x)} vs {dict(y)}")
    return problems


def _revenue_problems(old: Path, new: Path, new_run: dict) -> list:
    """销货收入照科目组（11-08）：新写法每张销货单的收入贷方（SEQIT 5）= 逐行「物料科目组的 ACCNUM03（能记账时）否则载荷收入科目」
    按科目合计；收入合计与旧写法相同。"""
    problems = []
    vouchers = {s.get("docnum") for s in new_run.get("steps", []) if s.get("ok") and s.get("doctype") in ("IV", "HS", "SVC")}
    payload_acc = new_run.get("revenue_acc") or ""
    postable = {r["ACCNUM"] for r in rows(new, "GLACC", ("ACCNUM", "ACCTYP")) if str(r["ACCTYP"] or "") == "0"}
    groups = {r["ACCCOD"]: r.get("ACCNUM03") or "" for r in rows(new, "ISACC", ("ACCCOD", "ACCNUM03"))}
    item_group = {r["STKCOD"]: r["ACCCOD"] for r in rows(new, "STMAS", ("STKCOD", "ACCCOD"))}
    expected = {}
    for r in rows(new, "STCRD", ("DOCNUM", "STKCOD", "NETVAL")):
        voucher = str(r["DOCNUM"] or "").strip()
        if voucher not in vouchers:
            continue
        acc = groups.get(item_group.get(r["STKCOD"], ""), "")
        acc = acc if acc in postable else payload_acc
        expected.setdefault(voucher, {})[acc] = expected.get(voucher, {}).get(acc, Decimal(0)) + num(r["NETVAL"])
    def credits(acct):
        out = {}
        for r in rows(acct, "GLJNLIT", ("VOUCHER", "ACCNUM", "TRNTYP", "AMOUNT", "SEQIT")):
            voucher = str(r["VOUCHER"] or "").strip()
            if voucher in vouchers and str(r["SEQIT"] or "").strip() == "5" and str(r["TRNTYP"] or "").strip() == "1":
                acc = str(r["ACCNUM"] or "").strip()
                out.setdefault(voucher, {})[acc] = out.get(voucher, {}).get(acc, Decimal(0)) + num(r["AMOUNT"])
        return out
    a, b = credits(old), credits(new)
    for voucher in vouchers:
        x, y, want = a.get(voucher, {}), b.get(voucher, {}), expected.get(voucher, {})
        if abs(sum(x.values(), Decimal(0)) - sum(y.values(), Decimal(0))) >= Decimal("0.005"):
            problems.append(f"{voucher} revenue total differs: {dict(x)} vs {dict(y)}")
        if set(y) != set(want) or any(abs(y[k] - want[k]) >= Decimal("0.005") for k in y):
            problems.append(f"{voucher} revenue {dict(y)} != item groups {dict(want)}")
    return problems


def compare_book(old: Path, new: Path, old_run: dict, new_run: dict, extra=frozenset(), gl=False) -> dict:
    if new_run.get("state") != "written":
        return {"state": "skip", "why": new_run.get("why", "")}
    problems = []
    old_steps = [(s["doctype"], s.get("ok"), s.get("docnum")) for s in old_run.get("steps", [])]
    new_steps = [(s["doctype"], s.get("ok"), s.get("docnum")) for s in new_run.get("steps", [])]
    guard_changed = [n for o, n in zip(old_steps, new_steps) if o != n]
    old_ok = {s["doctype"]: s.get("ok") for s in old_run.get("steps", [])}
    # 新旧都写不进的（账套默认科目不能记账等，与本次改动无关）不算失败，只有旧的写得进、新的写不进才算
    failed_new = [s for s in new_run["steps"] if s.get("ok") is False and old_ok.get(s["doctype"]) is not False]
    if failed_new:
        problems.append(f"new write failed: {failed_new}")
    cost = _cost_differs(new_run)
    diffs, unexpected = {}, []
    for path in sorted(p for p in new.iterdir() if p.suffix.upper() == ".DBF"):
        table = path.stem.upper()
        if gl and table == "GLJNLIT":
            continue  # 分录口径改了：不逐行比，下面按科目净额核
        a, b = rows(old, table, deleted=True), rows(new, table, deleted=True)
        if len(a) != len(b):
            if not guard_changed:
                unexpected.append(f"{table} rows {len(a)} != {len(b)}")
            continue
        for x, y in zip(a, b):
            for field in y:
                if x.get(field) == y.get(field):
                    continue
                if isinstance(x.get(field), float) and isinstance(y.get(field), float) and abs(x[field] - y[field]) < 1e-6:
                    continue
                key = (table, field)
                diffs[f"{table}.{field}"] = diffs.get(f"{table}.{field}", 0) + 1
                if key in ALWAYS or key in extra or (cost and (key in COST or (table == "STLOC" and field.startswith("VAL")))):
                    continue
                if not guard_changed:
                    unexpected.append(f"{table}.{field}")
    if unexpected:
        problems.append(f"unexpected differences: {sorted(set(unexpected))[:8]}")
    if gl:
        problems.extend(_gl_problems(old, new, new_run))
        problems.extend(_revenue_problems(old, new, new_run))
    # 新写法的不变量
    code = new_run["item"]
    master = [r for r in rows(new, "STMAS", ("STKCOD", "TOTBAL", "TOTVAL", "UNITPR")) if r["STKCOD"] == code][0]
    done = [s["doctype"] for s in new_run["steps"] if s.get("ok")]
    expect = Decimal(str(new_run["before"]["TOTBAL"])) + sum(NET_QTY[d] for d in done)
    if abs(num(master["TOTBAL"]) - expect) >= Decimal("0.0001"):
        problems.append(f"TOTBAL {master['TOTBAL']} != before + moves {expect}")
    if num(master["TOTBAL"]) > 0 and abs(num(master["UNITPR"]) - num(master["TOTVAL"]) / num(master["TOTBAL"])) > Decimal("0.0001"):
        problems.append(f"UNITPR {master['UNITPR']} != TOTVAL / TOTBAL")
    stloc = sum((num(r["LOCBAL"]) for r in rows(new, "STLOC", ("STKCOD", "LOCBAL")) if r["STKCOD"] == code), Decimal(0))
    stloc_old_src = sum((num(r["LOCBAL"]) for r in rows(old, "STLOC", ("STKCOD", "LOCBAL")) if r["STKCOD"] == code), Decimal(0))
    was_equal = (new / "STLOC.DBF").is_file() and abs(stloc - sum(NET_QTY[d] for d in done)
                                                      - Decimal(str(new_run["before"]["TOTBAL"]))) < Decimal("0.0001")
    if was_equal and abs(stloc - num(master["TOTBAL"])) >= Decimal("0.0001"):
        problems.append(f"TOTBAL {master['TOTBAL']} != sum STLOC {stloc}")
    for table, key, party, doctype, field in (("APMAS", "SUPCOD", new_run["supplier"], "RR", "BALANCE"),
                                             ("ARMAS", "CUSCOD", new_run["customer"], "IV", "BALANCE")):
        after = [r for r in rows(new, table, (key, field)) if r[key] == party]
        if not after or field not in after[0] or after[0][field] is None:
            continue
        start = Decimal(str((new_run.get("party_before") or {}).get(table, 0)))
        amount = {"RR": Decimal("53.50"), "IV": Decimal("42.80")}[doctype] if doctype in done else Decimal(0)
        if abs(num(after[0][field]) - start - amount) >= Decimal("0.005"):
            problems.append(f"{table}.{field} moved {num(after[0][field]) - start} != {amount}")
    return {"state": "fail" if problems else "ok", "why": "; ".join(problems), "item": code, "apart": new_run.get("apart"),
            "cost_differs": cost, "guard_changed": guard_changed, "diffs": diffs, "stloc": float(stloc),
            "stloc_old": float(stloc_old_src), "totbal": master["TOTBAL"]}


if __name__ == "__main__":
    sys.exit(main())
