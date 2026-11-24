# -*- coding: utf-8 -*-
"""实机回归（开发工具，不进安装包）：直写的无明细买卖单在 Express 里「แก้ไข → 不改 → 保存」不能被清零（11-24 缺陷）。

起因：以前直写只写单头的无明细单，人在 Express 里修改再存会被清零（金额、凭证、税记录都清掉，单据仍有效；测试机2 69EXP\\TEST
实测 RR691124-001、IV0000034）。现在直写补一行「编码 = 科目码」的服务品（``companion.amount_only``）。以后改直写写法都要过这一条。

对每种单（赊购 RR、赊销 IV）：用这份源码的直写写一张无明细单 → 读单头 / 凭证 / 分录（有效行）/ 税记录 → 在 Express 里打开这张、
点「แก้ไข」、什么都不改、点保存 → 再读 → 断言逐字段不变 → 原生作废（作废命令 + 确认）→ 断言单头 DOCSTAT = C。
只许测试账套（``BOOKS``），写前把要动的表拷一份快照到 ``<out>\\snapshot``。要在桌面已登录用户的交互会话里跑（同 ``lab_run.py``，
``lab_task.ps1`` 建一次性计划任务）。

    python edit_save_regression.py --src <仓库>\\companion\\src --book P:\\69EXP\\TEST --company "บริษัท Pearnly จำกัด" \\
        --supplier T001 --customer <客户码> --purchase-acc 53-05-07-00 --revenue-acc 42-08-00-00 --out C:\\...\\edit_save

输出 ``<out>\\result.json``（每种单：单号、前后各表、断言结果）与截图；退出码 0 = 全过。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
import types
from pathlib import Path

BOOKS = {r"p:\69exp\test", r"p:\69exp\test2"}  # 只许这两本测试账套（测试机上映射的 P: 盘）
PREV, EDIT, SAVE, VOID = 53509, 53505, 53507, 53515  # 浏览「上一张」、修改、保存、作废（配方 express-1.0.1.94 的命令号）
TABLES = ("APTRN", "ARTRN", "STCRD", "GLJNL", "GLJNLIT", "ISVAT", "APBAL", "ARBAL", "ISRUN", "STMAS", "ISACC")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    for name in ("--src", "--book", "--company", "--supplier", "--customer", "--purchase-acc", "--revenue-acc", "--out"):
        ap.add_argument(name, required=True)
    ap.add_argument("--exe", default="")
    args = ap.parse_args(argv)
    if args.book.rstrip("\\").lower() not in BOOKS:
        print(f"refused: {args.book} is not a test book")
        return 2
    sys.path.insert(0, args.src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    snap = out / "snapshot"
    snap.mkdir(exist_ok=True)
    for entry in os.listdir(args.book):
        if entry.upper().split(".")[0] in TABLES:
            shutil.copy2(os.path.join(args.book, entry), snap)
    results = []
    for kind in ("RR", "IV"):
        try:
            results.append(run_one(kind, args, out))
        except Exception:
            results.append({"kind": kind, "ok": False, "error": traceback.format_exc()[-1500:]})
    (out / "result.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps([(r["kind"], r["ok"], r.get("docnum"), r.get("problems") or r.get("error", "")[-200:]) for r in results],
                     ensure_ascii=False))
    return 0 if all(r["ok"] for r in results) else 1


def rows(book, table, key, value):
    import dbf

    path = next(Path(book) / e for e in os.listdir(book) if e.upper() == f"{table}.DBF")
    t = dbf.Table(str(path), codepage="cp874")
    t.open(dbf.READ_ONLY)
    try:
        return [{f: str(r[f]).strip() for f in t.field_names if f.upper() not in ("CHGTIM", "CHGDAT", "USERID")}
                for r in t if not dbf.is_deleted(r) and str(r[key]).strip() == value]
    finally:
        t.close()


# 断言只看管钱的栏：修改再存后金额、分录、税额、明细不能变（11-24 缺陷就是这些被清零）
MONEY = {"head": ("AMOUNT", "AFTDISC", "TOTAL", "VATAMT", "NETAMT", "NETVAL", "REMAMT", "DOCSTAT", "NXTSEQ"),
         "gljnl": ("VOUCHER", "DOCSTAT"), "gljnlit": ("ACCNUM", "TRNTYP", "AMOUNT"),
         "isvat": ("AMT01", "VAT01", "AMTRAT0", "DOCSTAT"), "stcrd": ("STKCOD", "TRNQTY", "NETVAL")}


def money(part_rows, fields):
    return sorted(tuple(str(r.get(f, "")) for f in fields) for r in part_rows)


def _changed(before, after):
    out = []
    for b, a in zip(before, after):
        out.append({k: [b.get(k), a.get(k)] for k in set(b) | set(a) if b.get(k) != a.get(k)})
    if len(before) != len(after):
        out.append({"rows": [len(before), len(after)]})
    return out


def state(book, kind, docnum):
    head = "APTRN" if kind == "RR" else "ARTRN"
    return {"head": rows(book, head, "docnum", docnum), "stcrd": rows(book, "STCRD", "docnum", docnum),
            "gljnl": rows(book, "GLJNL", "voucher", docnum), "gljnlit": rows(book, "GLJNLIT", "voucher", docnum),
            "isvat": rows(book, "ISVAT", "docnum", docnum)}


def payload(kind, args, accounts):
    base, vat, total = "500.00", "35.00", "535.00"
    common = {"account_set": args.book, "docdate_be": _today_be(), "vat_period_be": _today_be()[:4] + "01",
              "vat_rate": 7.0, "base_amount": base, "vat_amount": vat, "total_amount": total, "srv_vattyp": "1",
              "items": [], "items_status": "empty"}
    if kind == "RR":
        return {**common, "doctype": "RR", "ref_no": "REG-EDITSAVE", "supplier": {"code": args.supplier},
                "items_account": args.purchase_acc,
                "lines": [{"acc": args.purchase_acc, "side": "D", "amount": base},
                          {"acc": accounts["vat_input_acc"], "side": "D", "amount": vat},
                          {"acc": accounts["ap_acc"], "side": "C", "amount": total}]}
    return {**common, "direction": "sales", "doctype": "IV", "ref_no": "REG-EDITSAVE-IV", "customer": {"code": args.customer},
            "items_account": args.revenue_acc,
            "lines": [{"acc": accounts["ar_acc"], "side": "D", "amount": total},
                      {"acc": args.revenue_acc, "side": "C", "amount": base},
                      {"acc": accounts["vat_output_acc"], "side": "C", "amount": vat}]}


def _today_be():
    import datetime as dt

    day = dt.date.today()
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def run_one(kind, args, out):
    from companion import account_probe
    from companion import dbf_sales, dbf_writer
    from companion.express_operator import host as H
    from companion.express_operator.batch import BatchState, BatchStore

    accounts = account_probe.probe_default_accounts(args.book)
    write = dbf_writer.write_purchase if kind == "RR" else dbf_sales.write_sale
    res = write(payload(kind, args, accounts), args.book, allowed_sets=(args.book,), selected_dir=args.book,
                expected_company=args.company)
    if not res.ok:
        return {"kind": kind, "ok": False, "error": f"direct write: {res.error_code} {res.error}"}
    docnum = res.docnum
    before = state(args.book, kind, docnum)
    problems = []
    if len(before["stcrd"]) != 1:
        problems.append(f"direct write left {len(before['stcrd'])} detail lines")
    settings = types.SimpleNamespace(express_root=str(Path(args.book).parent), account_dir=args.book,
                                     express_exe=args.exe or str(Path(args.book).parent / "ExpressI.exe"),
                                     account_company=args.company)
    root = out / "operator"
    batch = BatchState.new(args.company, args.book, args.book, [])
    runner, _parts = H.express_factory(batch, BatchStore(root / "batches"), settings, root, lambda *_: None, member=None)
    engine, session = runner.engine, runner.session
    after = voided = None
    other = {}
    try:
        session.ensure_open()
        engine.begin(None, kind)
        engine.ensure_form(kind)
        for _ in range(30):
            if engine.docnum_text().strip() == docnum:
                break
            engine.act([{"do": "command", "cmd": PREV}, {"do": "wait", "ms": 900}])
        if engine.docnum_text().strip() != docnum:
            raise RuntimeError(f"could not browse to {docnum}")
        engine.act([{"do": "command", "cmd": EDIT}, {"do": "wait", "ms": 1500}])
        engine.act([{"do": "command", "cmd": SAVE}, {"do": "wait", "ms": 2500}])
        engine._auto_dialogs()
        (out / f"{kind}-saved.png").write_bytes(__import__("base64").b64decode(engine.screenshot("takeover")))
        after = state(args.book, kind, docnum)
        for part, fields in MONEY.items():
            if money(before[part], fields) != money(after[part], fields):
                problems.append(f"{part} changed after edit and save: {money(before[part], fields)} -> {money(after[part], fields)}")
        # 管钱以外 Express 存档时自己补 / 改的栏（直写与原生原有的差别，另行评估）：照实记下，不算失败
        other = {part: _changed(before[part], after[part]) for part in before if before[part] != after[part]}
        engine.act([{"do": "command", "cmd": VOID}, {"do": "wait", "ms": 1200}])
        if any("ยกเลิกเอกสาร" in " ".join(w.get("texts", [])) for w in engine._dialogs()):
            engine.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 1500}])
        voided = state(args.book, kind, docnum)
        if [h.get("DOCSTAT") for h in voided["head"]] != ["C"]:
            problems.append(f"native void left DOCSTAT {[h.get('DOCSTAT') for h in voided['head']]}")
    finally:
        try:
            if engine.in_edit():
                engine.abandon()
        except Exception:  # noqa: BLE001
            pass
        session.close()
    return {"kind": kind, "ok": not problems, "docnum": docnum, "problems": problems, "other_changes": other,
            "before": before, "after": after, "voided": voided}


if __name__ == "__main__":
    sys.exit(main())
