#!/usr/bin/env python3
"""CC 在库存单 / 调拨里给 Express 里没有的物料建存货（载荷 15 的 ``new_ok``，dbf_stock / dbf_transfer；多仓闭环 P3x，11-01）的真账副本离线回归。

对每本账套：整本复制到工作目录 → 用这份源码的 CC 写入代码：
- 调整 JU +2（新物料甲，带单位成本）→ 主档 STMAS 多出且只多出一条甲：库存品（STKTYP 0）、结存 2、建档日是今天；明细 STCRD 用这个码；
  逐行回报（``line_modes``）的码就是它；永续盘存账套的凭证借贷平衡；
- 再写一张 JU 甲 +1 → 不再建（同名认回同一条），结存 3；
- 领用 OU 新物料乙 1 个：账套允许负库存 → 建乙、结存 −1；不许负库存（ISINFO.NEGALLOW = N）→ ``STOCK_INSUFFICIENT``、整本逐字节不变（主会话 11-01）；
- 有能记账的 TK 单据簿时，盘点调整 TK：盘盈新物料丁 2 个 → 建丁、结存 2（Express 原生盘盈不取载荷单价，挂不上批次记 0 或均价）；
  盘亏新物料戊 1 个按负库存规则（同领用）；再用 CC 作废代码作废盘盈那张 → DOCSTAT C、丁结存回 0、档留着；
- 有 RL 单据簿、STLOC、两个以上仓时，调拨新物料丙 1 个（仓 1 → 仓 2）：同上按负库存规则，写成时 STMAS 多一条丙、两仓 −1 / +1；
- 写入集合以外的表逐字节不变（写入集合 = STTRN、STCRD、STMAS、STLOC、GLJNL、GLJNLIT、ISRUN 及索引）；
- 给了 ``--runner``：照正式 CC 重建索引，并核写入集合的 CDX 与 DBF 一致（``express_cdx_check``）。
账套里用得最多的存货科目组判不出是存货科目（或一件库存品都没有）时，CC 回 ``STOCK_ACC_GROUP_UNFIT``（ERP 让人选一次组）：这里照人
选的样子取这套账第一个合格的组（``catalog_probe.probe_isacc_candidates``）带 ``stock_acccod`` 再写，之后几张都带它；没有合格组的跳过。
账套没有 JU 单别、JU 默认科目不能记账、没有 ISPRD 时同样跳过并记原因。
源账套只读（只 copytree 出来），工作副本用完删掉。

    python3 scripts/express-new-item-regression.py --source C:\\PearnlyExpress\\70EXP --pattern "T*" --work C:\\PearnlyStress\\_newitem_reg \\
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

WRITE_SET = {"STTRN", "STCRD", "STMAS", "STLOC", "GLJNL", "GLJNLIT", "ISRUN"}
NEW_A, NEW_B, NEW_C, NEW_D, NEW_E = "REG ของใหม่ ก", "REG ของใหม่ ข", "REG ของใหม่ ค", "REG ของใหม่ ง", "REG ของใหม่ จ"


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
    # 测试机控制台是 cp874：原因里有中文（CC 的报错）会让 print 崩掉；`-I` 下 PYTHONIOENCODING 不生效，这里自己改
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    global dbf, S, D, T, V, account_rules, fiscal_period, stock_guard, stock_location, cdx_reindex, cdx_check, with_runner, catalog_probe
    import dbf  # noqa: F401
    from companion import account_rules, catalog_probe, cdx_reindex, fiscal_period, stock_guard, stock_location  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_stock as D  # noqa: F401
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


def num(value):
    return Decimal(repr(float(value or 0)))


def digests(acct, skip=WRITE_SET):
    return {p.name.upper(): hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(acct).iterdir()
            if p.is_file() and p.stem.upper() not in skip}


def items_named(acct, name):
    return [r for r in rows(acct, "STMAS", ("STKCOD", "STKDES", "STKTYP", "MREMBAL", "CREDAT")) if text(r["STKDES"]) == name]


def places(acct, code):
    return {text(r["LOCCOD"]): num(r["LOCBAL"]) for r in rows(acct, "STLOC", ("STKCOD", "LOCCOD", "LOCBAL")) if text(r["STKCOD"]) == code}


def entry(doctype, docdate, items, **extra):
    return {"payload_version": 15, "direction": "stock", "doctype": doctype, "docdate_be": docdate, "ref_no": f"REG-NEW-{doctype}",
            "remark": "", "items": items, **extra}


def new(name, qty, **extra):
    return {"code": "", "name": name, "qty": str(qty), "unit": "", "new_ok": True, **extra}


def balanced_journal(acct, docnum):
    journal = [r for r in rows(acct, "GLJNLIT", ("VOUCHER", "TRNTYP", "AMOUNT")) if text(r["VOUCHER"]) == docnum]
    debit = sum(Decimal(str(r["AMOUNT"])) for r in journal if text(r["TRNTYP"]) == "0")
    credit = sum(Decimal(str(r["AMOUNT"])) for r in journal if text(r["TRNTYP"]) != "0")
    return debit == credit


def refused_untouched(acct, res, label):
    """不许负库存的账套：要新建又减少的单整单不写、建档前就停，账套逐字节不变。"""
    if res.ok or res.error_code != stock_guard.ERR_STOCK_INSUFFICIENT:
        return f"{label}: expected {stock_guard.ERR_STOCK_INSUFFICIENT} in a book without negative stock, got {res.ok} {res.error_code} {res.error}"
    return ""


def check(acct: Path) -> dict:
    ju = D.book_row(acct, "JU")
    if ju is None or not account_rules.postable_account(acct, ju["account"]):
        return {"state": "skip", "why": "no postable JU book"}
    calendar = fiscal_period.load(acct)
    if not calendar:
        return {"state": "skip", "why": "no ISPRD"}
    if any(items_named(acct, name) for name in (NEW_A, NEW_B, NEW_C, NEW_D, NEW_E)):
        return {"state": "skip", "why": "the regression item names already exist"}
    first = calendar.periods[0]
    day = first.beg + dt.timedelta(days=min(14, (first.end - first.beg).days))
    docdate = f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"
    negative = stock_guard.negative_allowed(acct)
    perpetual = text((account_rules._first(acct, "ISINFO") or {}).get("ISPERPETUA") or "Y").upper() != "N"
    files_before = digests(acct)
    index_before = cdx_check.snapshot(cdx_reindex, acct, WRITE_SET) if with_runner else {}
    masters_before = len(rows(acct, "STMAS", ("STKCOD",)))
    written, group = [], {}
    whole_before = digests(acct, skip=set())

    res = D.write_stock_entry(entry("JU", docdate, [new(NEW_A, 2, unit_cost="10")]), acct, selected_dir=str(acct), make_backup=False)
    if not res.ok and res.error_code == "STOCK_ACC_GROUP_UNFIT":
        if digests(acct, skip=set()) != whole_before:
            return {"state": "fail", "why": "the refused JU changed the book"}
        fit = [row["code"] for row in catalog_probe.probe_isacc_candidates(acct) if row.get("fit")]
        if not fit:
            return {"state": "skip", "why": f"no fit stock group to choose: {res.error}"[:300]}
        group = {"stock_acccod": fit[0]}  # 照人在 ERP 里选了这一组
        res = D.write_stock_entry(entry("JU", docdate, [new(NEW_A, 2, unit_cost="10")], **group), acct, selected_dir=str(acct),
                                  make_backup=False)
    if not res.ok:
        return {"state": "fail", "why": f"JU new {res.error_code}: {res.error}"}
    written.append(res.docnum)
    made = items_named(acct, NEW_A)
    if len(made) != 1 or text(made[0]["STKTYP"]) != "0" or num(made[0]["MREMBAL"]) != 2 or made[0]["CREDAT"] != dt.date.today():
        return {"state": "fail", "why": f"STMAS for the new item: {made}"}
    code = text(made[0]["STKCOD"])
    if [text(r["STKCOD"]) for r in rows(acct, "STCRD", ("DOCNUM", "STKCOD")) if text(r["DOCNUM"]) == res.docnum] != [code]:
        return {"state": "fail", "why": "STCRD does not use the new code"}
    if [row.get("stkcod") for row in res.line_modes or []] != [code]:
        return {"state": "fail", "why": f"line_modes {res.line_modes} != {code}"}
    if perpetual and not balanced_journal(acct, res.docnum):
        return {"state": "fail", "why": f"{res.docnum}: journal not balanced"}

    res = D.write_stock_entry(entry("JU", docdate, [new(NEW_A, 1, unit_cost="10")], **group), acct, selected_dir=str(acct),
                              make_backup=False)
    if not res.ok:
        return {"state": "fail", "why": f"JU again {res.error_code}: {res.error}"}
    written.append(res.docnum)
    made = items_named(acct, NEW_A)
    if len(made) != 1 or num(made[0]["MREMBAL"]) != 3:
        return {"state": "fail", "why": f"the same name made another item or the balance is off: {made}"}

    ou = D.book_row(acct, "OU")
    issue = "no postable OU book"
    if ou is not None and account_rules.postable_account(acct, ou["account"]):
        snapshot = digests(acct, skip=set())
        res = D.write_stock_entry(entry("OU", docdate, [new(NEW_B, 1)], **group), acct, selected_dir=str(acct), make_backup=False)
        if negative:
            if not res.ok:
                return {"state": "fail", "why": f"OU new {res.error_code}: {res.error}"}
            written.append(res.docnum)
            made = items_named(acct, NEW_B)
            if len(made) != 1 or num(made[0]["MREMBAL"]) != -1:
                return {"state": "fail", "why": f"OU new item: {made}"}
            issue = "written"
        else:
            why = refused_untouched(acct, res, "OU new")
            if why or digests(acct, skip=set()) != snapshot:
                return {"state": "fail", "why": why or "OU refused but the book changed"}
            issue = "refused"

    count, voided = "no postable TK book", ""
    tk = D.book_row(acct, "TK")
    if tk is not None and account_rules.postable_account(acct, tk["account"]):
        res = D.write_stock_entry(entry("TK", docdate, [new(NEW_D, 2)], **group), acct, selected_dir=str(acct), make_backup=False)
        if not res.ok:
            return {"state": "fail", "why": f"TK gain new {res.error_code}: {res.error}"}
        written.append(res.docnum)
        made = items_named(acct, NEW_D)
        if len(made) != 1 or text(made[0]["STKTYP"]) != "0" or num(made[0]["MREMBAL"]) != 2:
            return {"state": "fail", "why": f"TK gain new item: {made}"}
        gain, code_d = res.docnum, text(made[0]["STKCOD"])
        snapshot = digests(acct, skip=set())
        res = D.write_stock_entry(entry("TK", docdate, [new(NEW_E, -1)], **group), acct, selected_dir=str(acct), make_backup=False)
        if negative:
            if not res.ok:
                return {"state": "fail", "why": f"TK loss new {res.error_code}: {res.error}"}
            written.append(res.docnum)
            made = items_named(acct, NEW_E)
            if len(made) != 1 or num(made[0]["MREMBAL"]) != -1:
                return {"state": "fail", "why": f"TK loss new item: {made}"}
            count = "written"
        else:
            why = refused_untouched(acct, res, "TK loss new")
            if why or digests(acct, skip=set()) != snapshot:
                return {"state": "fail", "why": why or "TK loss refused but the book changed"}
            count = "refused"
        res = V.void_stock_entry({"action": V.ACTION, "doctype": "TK", "docnum": gain, "items": [{"code": code_d, "qty": "2"}]},
                                 acct, selected_dir=str(acct), make_backup=False)
        if not res.ok:
            return {"state": "fail", "why": f"void TK {gain} {res.error_code}: {res.error}"}
        state = [text(r["DOCSTAT"]) for r in rows(acct, "STTRN", ("DOCNUM", "DOCSTAT")) if text(r["DOCNUM"]) == gain]
        made = items_named(acct, NEW_D)
        if state != [V.VOIDED] or len(made) != 1 or num(made[0]["MREMBAL"]) != 0:
            return {"state": "fail", "why": f"void TK {gain}: DOCSTAT {state}, item {made}"}
        voided = gain

    transfer = "skipped"
    known = sorted(stock_location.warehouses(acct)) if (acct / "STLOC.DBF").is_file() else []
    if T.book_row(acct) is not None and len(known) >= 2:
        snapshot = digests(acct, skip=set())
        payload = entry("RL", docdate, [new(NEW_C, 1, loccod=known[0])], loccod=known[0], to_loccod=known[1], **group)
        res = T.write_transfer(payload, acct, selected_dir=str(acct), make_backup=False)
        if negative:
            if not res.ok:
                return {"state": "fail", "why": f"RL new {res.error_code}: {res.error}"}
            written.append(res.docnum)
            made = items_named(acct, NEW_C)
            if len(made) != 1:
                return {"state": "fail", "why": f"RL new item: {made}"}
            moved = places(acct, text(made[0]["STKCOD"]))
            if moved.get(known[0]) != -1 or moved.get(known[1]) != 1:
                return {"state": "fail", "why": f"RL STLOC {moved}"}
            transfer = "written"
        else:
            why = refused_untouched(acct, res, "RL new")
            if why or digests(acct, skip=set()) != snapshot:
                return {"state": "fail", "why": why or "RL refused but the book changed"}
            transfer = "refused"

    created = len(rows(acct, "STMAS", ("STKCOD",))) - masters_before
    if created != 1 + (issue == "written") + (count in ("written", "refused")) + (count == "written") + (transfer == "written"):
        return {"state": "fail", "why": f"{created} items created"}
    index_problems = cdx_check.problems(cdx_reindex, dbf, S, acct, index_before, written) if with_runner else []
    if index_problems:
        return {"state": "fail", "why": "; ".join(index_problems)}
    changed = sorted(name for name, digest in digests(acct).items() if files_before.get(name) != digest)
    if changed:
        return {"state": "fail", "why": f"tables outside the write set changed: {changed}"}
    return {"state": "ok", "docs": written, "negative_allowed": negative, "chose_group": group.get("stock_acccod", ""), "issue": issue, "count": count, "voided": voided, "transfer": transfer, "perpetual": perpetual,
            "cdx_checked": sorted(index_before)}


if __name__ == "__main__":
    sys.exit(main())
