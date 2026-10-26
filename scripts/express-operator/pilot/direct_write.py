"""第 ④ 种方式（基准，不花钱）：同一批草稿单用现有 CC 直接写入代码写进试点账套的**本机副本**，计时并用同一把尺子读回核对。

只写工作目录 copies 下的副本（副本由执行代理的 copy_book 从 69EXP\\test 只读拷来），不碰服务器上的账套。
在测试机上跑（要 companion 源码和 dbf 包，和 scripts/express-*-regression.py 同样的环境）：

    python -m pilot.direct_write --book C:\\XpPilot\\copies\\direct-1026 --drafts drafts.jsonl \\
        --companion <仓库>\\companion\\src --out direct-results.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

from . import compare as C
from . import dbfread
from . import draft as D


def be(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{(y + 543) % 100:02d}{m:02d}{d:02d}"


def counts(book: Path):
    out = {}
    for name in ("APTRN", "ARTRN", "STCRD", "ISVAT"):
        p = book / f"{name}.DBF"
        if p.exists():
            with p.open("rb") as f:
                out[name] = dbfread.count(f.read(8))
    return out


def new_rows(book: Path, since):
    return {name: list(dbfread.rows((book / f"{name}.DBF").read_bytes(), n)) for name, n in since.items()}


def payload(d: D.Draft, accounts, names):
    t = d.totals()
    # CC 写入要求明细合计 = 税前金额：含税价的单（现销）按税前比例摊回每一行
    ratio = (t["net"] / t["total"]) if d.vat_included and t["total"] else Decimal(1)
    items = [{"name": (names.get(l.item_code) or l.item_code)[:50], "code": l.item_code, "qty": l.qty,
              "unit_price": f"{(l.amount * ratio / Decimal(l.qty)).quantize(Decimal('0.01'))}",
              "amount": f"{(l.amount * ratio).quantize(Decimal('0.01'))}", "unit": "", "loc": l.loc,
              "item_mode": "stock_item" if d.spec["side"] == "purchase" else "stock_sale"} for l in d.lines]
    base = {"doctype": d.doc_type, "account_set": "PILOT", "docdate_be": be(d.date), "vat_period_be": be(d.date)[:4] + "01",
            "ref_no": d.ref_no or d.doc_id[:15], "vat_rate": float(d.vat_rate), "base_amount": f"{t['net']:.2f}",
            "vat_amount": f"{t['vat']:.2f}", "total_amount": f"{t['total']:.2f}", "items": items, "items_status": "ok"}
    if d.spec["side"] == "purchase":
        base.update({"supplier": {"code": d.party_code, "name": "", "tax_id": "", "prename": "", "supplier_new": False},
                     "lines": [{"acc": accounts["fallback_acc"], "side": "D", "amount": f"{t['net']:.2f}", "desc": "ซื้อ"},
                               {"acc": accounts["vat_input_acc"], "side": "D", "amount": f"{t['vat']:.2f}", "desc": "ภาษีซื้อ"},
                               {"acc": accounts["ap_acc"], "side": "C", "amount": f"{t['total']:.2f}", "desc": "เจ้าหนี้"}],
                     "items_account": accounts["fallback_acc"]})
    else:
        base.update({"direction": "sales",
                     "customer": {"code": d.party_code, "name": "", "tax_id": "", "prename": "", "customer_new": False},
                     "lines": [{"acc": accounts["ar_acc"], "side": "D", "amount": f"{t['total']:.2f}", "desc": "ลูกหนี้"},
                               {"acc": accounts["revenue_acc"], "side": "C", "amount": f"{t['net']:.2f}", "desc": "รายได้"},
                               {"acc": accounts["vat_output_acc"], "side": "C", "amount": f"{t['vat']:.2f}", "desc": "ภาษีขาย"}],
                     "items_account": accounts["revenue_acc"]})
    return base


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--book", required=True)
    p.add_argument("--drafts", required=True)
    p.add_argument("--companion", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--runner", required=True, help="已安装 CC 的 cdx_reindex_runner.exe（和正式 CC 一样重建索引）")
    args = p.parse_args(argv)
    book = Path(args.book)
    copies = (Path(__file__).resolve().parent.parent / "copies").resolve()
    if copies not in book.resolve().parents:
        raise SystemExit(f"只写 {copies} 下的副本")
    sys.path.insert(0, args.companion)
    from companion import account_probe, cdx_reindex
    from companion import dbf_sales as WS
    from companion import dbf_writer as W

    runner = Path(args.runner)
    if not runner.is_file():
        raise SystemExit(f"找不到 {runner}")
    cdx_reindex.runner_path = lambda: runner  # 同 scripts/express_cdx_check.use_runner
    accounts = account_probe.probe_default_accounts(book)
    names = {str(r.get("STKCOD") or "").strip(): str(r.get("STKDES") or "").strip()
             for r in dbfread.rows((book / "STMAS.DBF").read_bytes())}
    with open(args.out, "a", encoding="utf-8") as out:
        for d in D.load(Path(args.drafts)):
            before = counts(book)
            started = time.perf_counter()
            error = ""
            try:
                fn = W.write_purchase if d.spec["side"] == "purchase" else WS.write_sale
                res = fn(payload(d, accounts, names), book, allowed_sets=("PILOT",), selected_dir=str(book),
                         make_backup=False)
                if not res.ok:
                    error = f"{res.error_code}: {res.error}"
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"
            seconds = time.perf_counter() - started
            result = C.compare(d, new_rows(book, before)) if not error else {"ok": False, "docnum": "", "diffs": []}
            rec = {"doc_id": d.doc_id, "doc_type": d.doc_type, "mode": "direct", "lines": len(d.lines),
                   "seconds": round(seconds, 2), "usd": "0", "outcome": "done" if not error else "error",
                   "error": error, "readback_ok": result["ok"], "docnum": result["docnum"], "diffs": result["diffs"],
                   "fallbacks": [], "human": 0, "crashed": False, "first_try": result["ok"]}
            out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            print("OK " if rec["readback_ok"] else "BAD", d.doc_id, rec["seconds"], rec["docnum"], error[:120] if error else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
