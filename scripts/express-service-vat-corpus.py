#!/usr/bin/env python3
"""语料真实进货单逐字段对 Express 答案（10-29：服务品进项税口径 service_vat、整单 / 部分免税）。只读语料、只写 T 账套的工作副本。

语料是测试机 ``06-录入推送\\有答案\\<公司>\\答案\\<账套>\\原表``（会计在 Express 里手录的账 = 答案）；同一家公司的清空测试账套在
``C:\\PearnlyExpress\\70EXP``（答案账套 67XXXX / 68XXXX / 69XXXX 对 T7XXXX / T8XXXX / T9XXXX）。每本挑几张：
- 带服务品的赊购单（价格类型 1 / 2，口径 1 与 2 各几张）；
- 整单免税、部分免税的进货单（有零税额 AMTRAT0 的）。
对每张：口径历史取答案账套里这张单**之前**的单（按日期、单号；作废的、Pearnly 写的不算），照 CC 的规则判；用答案原样造载荷
（金额、明细、免税行），写进 T 账套副本，读回逐栏比：单头 FLGVAT / SRV_VATTYP / 金额栏 / AMTRAT0，明细 VATCOD / TRNVAL / NETVAL，
进项税记录有无与金额。历史定不了的（要问）记一笔「问」，再按答案的口径当作人选的写一遍比其余栏。

    python3 scripts/express-service-vat-corpus.py --corpus <有答案目录> --books C:\\PearnlyExpress\\70EXP --work C:\\PearnlyStress\\_srv_cmp \\
        --companion <仓库>\\companion\\src --out result.jsonl --runner <cdx_reindex_runner.exe> [--per-kind 2]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import struct
import sys
import traceback
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

HEAD = ("RECTYP", "DOCNUM", "DOCDAT", "SUPCOD", "FLGVAT", "VATRAT", "AMOUNT", "AFTDISC", "TOTAL", "NETVAL", "AMTRAT0",
        "VATAMT", "NETAMT", "SRV_VATTYP", "DOCSTAT", "DISCAMT", "REFNUM", "USERID")
MONEY = ("AMOUNT", "AFTDISC", "TOTAL", "NETVAL", "AMTRAT0", "VATAMT", "NETAMT")


def raw(path: Path, want) -> list:
    """只读 DBF 的几栏（坏字节替换、删除行跳过）。"""
    data = path.read_bytes()
    count, hlen, rlen = struct.unpack("<IHH", data[4:12])
    fields, pos, off = [], 32, 1
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("ascii", "replace").upper()
        kind, size = chr(data[pos + 11]), data[pos + 16]
        fields.append((name, kind, off, size))
        off += size
        pos += 32
    picked = [f for f in fields if f[0] in want]
    out = []
    for index in range(count):
        rec = data[hlen + index * rlen: hlen + (index + 1) * rlen]
        if not rec or rec[:1] == b"*":
            continue
        row = {}
        for name, kind, o, size in picked:
            cell = rec[o:o + size]
            if kind in "NF":
                try:
                    row[name] = float(cell.strip() or 0)
                except ValueError:
                    row[name] = 0.0
            elif kind == "B":
                row[name] = struct.unpack("<d", cell)[0] if size == 8 else 0.0
            else:
                row[name] = cell.decode("cp874", "replace").strip()
        out.append(row)
    return out


def table(folder: Path, name: str):
    for cand in (f"{name}.DBF", f"{name.lower()}.dbf", f"{name}.dbf"):
        if (folder / cand).is_file():
            return folder / cand
    return None


def d2(value) -> Decimal:
    return Decimal(repr(float(value or 0))).quantize(Decimal("0.01"))


def be(day: dt.date) -> str:
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--books", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--companion", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--per-kind", type=int, default=2)
    parser.add_argument("--runner", required=True, help="已安装 CC 的 cdx_reindex_runner.exe（CC 写完要重建索引，没有它整单不写）")
    args = parser.parse_args(argv)
    sys.path.insert(0, args.companion)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    global dbf, S, W, fiscal_period, account_probe, cdx_reindex, service_vat
    import dbf  # noqa: F401
    from companion import account_probe, cdx_reindex, fiscal_period, service_vat  # noqa: F401
    from companion import dbf_schema as S  # noqa: F401
    from companion import dbf_writer as W  # noqa: F401
    import express_cdx_check as cdx_check
    cdx_check.use_runner(cdx_reindex, args.runner)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    totals = defaultdict(int)
    with open(args.out, "w", encoding="utf-8") as out:
        for answer in sorted(Path(args.corpus).glob("*/答案/*/原表")):
            book = answer.parent.name
            tbook = Path(args.books) / ("T" + book[1:]) if len(book) > 2 and book[:2].isdigit() else None
            if not tbook or not tbook.is_dir() or not table(answer, "APTRN"):
                continue
            copy = work / tbook.name
            shutil.rmtree(copy, ignore_errors=True)
            try:
                shutil.copytree(tbook, copy)
                for rec in compare_book(answer, copy, args.per_kind):
                    rec.update(answer=f"{answer.parent.parent.parent.name}/{book}", tbook=tbook.name)
                    totals[rec["kind"] + ":" + rec["outcome"]] += 1
                    out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                    out.flush()
            except Exception:
                totals["book_error"] += 1
                out.write(json.dumps({"answer": book, "error": traceback.format_exc()[-500:]}, ensure_ascii=False) + "\n")
            finally:
                shutil.rmtree(copy, ignore_errors=True)
    print(json.dumps(dict(sorted(totals.items())), ensure_ascii=False))
    return 0


def compare_book(answer: Path, copy: Path, per_kind: int) -> list:
    info = raw(table(answer, "ISINFO"), {"VAT_INITEM"}) if table(answer, "ISINFO") else []
    mode = "initem" if info and info[0].get("VAT_INITEM") == "Y" else "whole"
    types = {r["STKCOD"]: r["STKTYP"] for r in raw(table(answer, "STMAS"), {"STKCOD", "STKTYP"})}
    heads = [h for h in raw(table(answer, "APTRN"), set(HEAD)) if h["RECTYP"] in ("1", "3") and h["DOCSTAT"] != "C"]
    heads.sort(key=lambda h: (h.get("DOCDAT") or "", h["DOCNUM"]))
    nums = {h["DOCNUM"] for h in heads}
    lines = defaultdict(list)
    for r in raw(table(answer, "STCRD"), {"DOCNUM", "SEQNUM", "STKCOD", "TRNQTY", "TRNVAL", "NETVAL", "VATCOD"}):
        if r["DOCNUM"] in nums:
            lines[r["DOCNUM"]].append(r)
    vats = defaultdict(list)
    if table(answer, "ISVAT"):
        for r in raw(table(answer, "ISVAT"), {"DOCNUM", "AMT01", "VAT01", "AMTRAT0", "VATREC"}):
            if r["DOCNUM"] in nums and r.get("VATREC", "P") != "S":
                vats[r["DOCNUM"]].append(r)
    t_types = {r["STKCOD"] for r in raw(table(copy, "STMAS"), {"STKCOD"})}
    t_sups = {r["SUPCOD"] for r in raw(table(copy, "APMAS"), {"SUPCOD"})}
    usable = [h for h in heads if lines[h["DOCNUM"]] and (h["DISCAMT"] or 0) == 0 and h["SUPCOD"] in t_sups
              and all(l["STKCOD"] in t_types for l in lines[h["DOCNUM"]])]
    picks = []
    for kind, test in (
            ("service_1", lambda h: h["RECTYP"] == "3" and h["FLGVAT"] in ("1", "2") and h["SRV_VATTYP"] == "1"
             and (h["AMTRAT0"] or 0) == 0 and any(types.get(l["STKCOD"], "0") != "0" for l in lines[h["DOCNUM"]])),
            ("service_2", lambda h: h["RECTYP"] == "3" and h["FLGVAT"] in ("1", "2") and h["SRV_VATTYP"] == "2"
             and (h["AMTRAT0"] or 0) == 0 and any(types.get(l["STKCOD"], "0") != "0" for l in lines[h["DOCNUM"]])),
            ("exempt_whole", lambda h: (h["AMTRAT0"] or 0) > 0 and (h["VATAMT"] or 0) == 0),
            ("exempt_partial", lambda h: (h["AMTRAT0"] or 0) > 0 and (h["VATAMT"] or 0) > 0)):
        picks += [(kind, h) for h in [h for h in usable if test(h)][-per_kind:]]
    if not picks:
        return []
    accounts = account_probe.probe_default_accounts(copy)
    cash = (raw(table(copy, "ISINFO"), {"ACCNUM00"}) or [{}])[0].get("ACCNUM00", "")
    calendar = fiscal_period.load(copy)
    order = {h["DOCNUM"]: index for index, h in enumerate(heads)}
    results = []
    for kind, h in picks:
        rec = {"kind": kind, "mode": mode, "docnum": h["DOCNUM"], "native_flgvat": h["FLGVAT"],
               "native_srv": h["SRV_VATTYP"], "native_isvat": len(vats[h["DOCNUM"]])}
        try:
            before = {}
            for prior in heads[:order[h["DOCNUM"]]]:
                if (prior["RECTYP"] == "3" and prior["FLGVAT"] in ("1", "2") and prior["SRV_VATTYP"] in ("1", "2")
                        and prior.get("USERID", "").upper() != "PEARNLY"):
                    before.setdefault(prior["SUPCOD"], set()).add(prior["SRV_VATTYP"])
            known = {code: service_vat.verdict(values) for code, values in before.items()}
            service_vat.history = lambda _dir, _known=known: dict(_known)  # 答案里这张单之前的历史
            payload = build(h, lines[h["DOCNUM"]], vats[h["DOCNUM"]], accounts, cash, calendar)
            res = W.write_purchase(payload, copy, allowed_sets=("DATAT",), selected_dir=str(copy), make_backup=False)
            rec["decided"] = "history"
            if not res.ok and res.error_code == service_vat.CHOICE_CODE:
                rec["decided"] = "ask"
                payload[service_vat.PAYLOAD_KEY] = h["SRV_VATTYP"] if h["SRV_VATTYP"] in ("1", "2") else "1"
                res = W.write_purchase(payload, copy, allowed_sets=("DATAT",), selected_dir=str(copy), make_backup=False)
            if not res.ok:
                rec.update(outcome="write_failed", why=f"{res.error_code}: {res.error}"[:300])
                results.append(rec)
                continue
            rec.update(diffs=diffs(copy, res.docnum, h, lines[h["DOCNUM"]], vats[h["DOCNUM"]]))
            rec["outcome"] = "match" if not rec["diffs"] else "differs"
        except Exception:
            rec.update(outcome="error", why=traceback.format_exc()[-400:])
        results.append(rec)
    return results


def build(h, ls, vat_rows, accounts, cash, calendar):
    doctype = "HP" if h["RECTYP"] == "1" else "RR"
    day = dt.date(int(h["DOCDAT"][:4]), int(h["DOCDAT"][4:6]), int(h["DOCDAT"][6:8]))
    if calendar and calendar.locate(day) is None:
        day = calendar.periods[0].beg + dt.timedelta(days=14)
    base, vat, total = d2(h["NETVAL"]), d2(h["VATAMT"]), d2(h["NETAMT"])
    items = []
    for line in sorted(ls, key=lambda r: int(str(r["SEQNUM"]).strip() or 0)):
        qty = Decimal(repr(float(line["TRNQTY"] or 1))) or Decimal(1)
        items.append({"name": line["STKCOD"], "code": line["STKCOD"], "qty": str(qty), "unit_price": str(d2(d2(line["NETVAL"]) / qty)),
                      "amount": str(d2(line["NETVAL"])), "unit": "", "item_mode": "non_stock_item",
                      **({"vat_exempt": True} if line["VATCOD"] == "0" else {})})
    credit = cash if doctype == "HP" and cash else accounts["ap_acc"]
    gl = [{"acc": accounts["fallback_acc"], "side": "D", "amount": str(base), "desc": "ซื้อ"}]
    if vat:
        gl.append({"acc": accounts["vat_input_acc"], "side": "D", "amount": str(vat), "desc": "ภาษีซื้อ"})
    gl.append({"acc": credit, "side": "C", "amount": str(total), "desc": "เจ้าหนี้"})
    payload = {"doctype": doctype, "account_set": "DATAT", "docdate_be": be(day), "vat_period_be": be(day)[:4] + "01",
               "ref_no": (h["REFNUM"] or h["DOCNUM"])[:15], "vat_rate": 7.0, "base_amount": str(base), "vat_amount": str(vat),
               "total_amount": str(total), "items": items, "items_status": "ok", "items_account": accounts["fallback_acc"],
               "lines": gl, "supplier": {"code": h["SUPCOD"], "name": "", "tax_id": "", "prename": "", "supplier_new": False}}
    if (h["AMTRAT0"] or 0) > 0:
        zero = d2(h["AMTRAT0"])
        payload["vat_fields"] = {"AMT01": str(base - zero), "AMTRAT0": str(zero)}
    return payload


def diffs(copy, docnum, h, ls, vat_rows) -> list:
    out = []
    got = [r for r in raw(table(copy, "APTRN"), set(HEAD)) if r["DOCNUM"] == docnum][0]
    for key in ("FLGVAT", "SRV_VATTYP"):
        if str(got.get(key)) != str(h.get(key)):
            out.append(f"{key} {got.get(key)!r}!={h.get(key)!r}")
    for key in MONEY:
        if d2(got.get(key)) != d2(h.get(key)):
            out.append(f"{key} {d2(got.get(key))}!={d2(h.get(key))}")
    mine = sorted([r for r in raw(table(copy, "STCRD"), {"DOCNUM", "SEQNUM", "TRNVAL", "NETVAL", "VATCOD"}) if r["DOCNUM"] == docnum],
                  key=lambda r: int(str(r["SEQNUM"]).strip() or 0))
    for index, (g, a) in enumerate(zip(mine, sorted(ls, key=lambda r: int(str(r["SEQNUM"]).strip() or 0))), 1):
        if (g.get("VATCOD") or "") != (a["VATCOD"] or ""):
            out.append(f"L{index} VATCOD {g.get('VATCOD')!r}!={a['VATCOD']!r}")
        for key in ("TRNVAL", "NETVAL"):
            if d2(g.get(key)) != d2(a[key]):
                out.append(f"L{index} {key} {d2(g.get(key))}!={d2(a[key])}")
    have = [(d2(x["AMT01"]), d2(x["VAT01"]), d2(x.get("AMTRAT0"))) for x in raw(table(copy, "ISVAT"),
            {"DOCNUM", "AMT01", "VAT01", "AMTRAT0"}) if x["DOCNUM"] == docnum]
    want = [(d2(x["AMT01"]), d2(x["VAT01"]), d2(x.get("AMTRAT0"))) for x in vat_rows]
    if have != want:
        out.append(f"ISVAT {have}!={want}")
    return out


if __name__ == "__main__":
    sys.exit(main())
