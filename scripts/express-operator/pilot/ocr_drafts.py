"""识别结果 → 草稿单（主会话 10-26：识别走产品本身、单据只看一次；标准答案只用来核对，不当输入）。

输入（在本机，不进仓库，见 README）：
- 产品正式识别跑过「录入推送语料」抽样 250 份的结果（甲骨文 /opt/pearnly-claude-eval/runs-prod/ex-prod-cowork.jsonl，
  走 Cowork 正式代码 + claude-ocr 服务，和生产同一条路），每份带实际花费 usd；
- 语料清单 list.tsv 和各公司 Express 答案（会计手录的进项 / 销项税登记、进货 / 销货单 CSV）；
- 试点账套 69EXP\\test 的主档（执行代理 book_rows 读 APMAS / ARMAS / STMAS / ISPRD，``masters`` 子命令整理）。

每份识别结果里读到的第一张票 → 一张草稿单：
- 单种按识别出的票种：收据类 → 现购 HP / 现销 HS，其余 → 赊购 RR / 赊销 IV（进项 / 销项按和答案对上的方向，
  对不上按语料分类）；
- 对方按对方税号固定映射到试点账套已有的供应商 / 客户；明细一行：试点账套的一个非库存品（没有就用库存品），数量 1、
  单价 = 识别的未税额（读不到未税额用合计 − 税额）；日期挪进试点账套的会计期间（同月同日）；对方单号 = 识别的单号；
- answer = 和答案对上的那条（同样挪了日期），只给 compare_answer 核对；ocr_usd = 这一份的识别花费。
识别缺日期 / 金额、零税率等暂不适合照录的，按原因计数，不生成。

    python -m pilot.ocr_drafts masters <book_rows 输出.json> > masters.json
    python -m pilot.ocr_drafts build <语料目录> masters.json > drafts.jsonl     （统计打到 stderr）
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional

from .draft import Draft, Line, money

RECEIPTS = {"receipt", "receipt_tax_invoice", "fuel_receipt"}
KINDS = RECEIPTS | {"tax_invoice", "bill_invoice", "utility"}  # 照四种买卖单录的票种；报关、扣税凭证、转账单等另有录法


def num(v) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(v or "").upper())


def digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def dec(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v).replace(",", "")) if v not in (None, "") else None
    except InvalidOperation:
        return None


def answers(root: Path, tax: str) -> List[Dict]:
    """同甲骨文 express.py 的口径：税登记与进 / 销货单两种来源，way = P 进项 / S 销项。"""
    rows = []
    for d in (root / "answers" / tax).glob("*"):
        for f, way in (("进项税登记.csv", "P"), ("销项税登记.csv", "S")):
            p = d / f
            if p.exists():
                for r in csv.DictReader(open(p, encoding="utf-8-sig")):
                    sub, vat = dec(r["未税额"]), dec(r["税额"])
                    if sub is not None:
                        rows.append({"way": way, "number": r["对方单号"], "date": r["单据日期"][:10],
                                     "tax": digits(r["税号"]), "subtotal": sub, "vat": vat or Decimal(0),
                                     "total": sub + (vat or 0), "src": f})
        for f, way in (("进货单.csv", "P"), ("销货单.csv", "S")):
            p = d / f
            if p.exists():
                for r in csv.DictReader(open(p, encoding="utf-8-sig")):
                    sub, vat, tot = dec(r["合计"]), dec(r["税额"]), dec(r["净额"])
                    if tot is not None:
                        rows.append({"way": way, "number": r["对方单号"], "date": r["日期"][:10],
                                     "tax": digits(r["对方税号"]), "subtotal": sub, "vat": vat or Decimal(0),
                                     "total": tot, "src": f})
    return rows


def find(inv: Dict, truth: List[Dict]) -> Optional[Dict]:
    """同甲骨文 express.py：单号 + 对方税号唯一，否则单号唯一，否则 税号 + 合计 + 日期唯一。"""
    n = num(inv.get("invoice_number"))
    taxes = {digits(inv.get("seller_tax")), digits(inv.get("buyer_tax"))} - {""}
    if n:
        hit = [t for t in truth if num(t["number"]) == n]
        both = [t for t in hit if t["tax"] in taxes]
        for c in (both, hit):
            if c and len({(t["way"], t["total"]) for t in c}) == 1:
                return sorted(c, key=lambda t: 0 if "税登记" in t["src"] else 1)[0]
    tot = dec(inv.get("total"))
    if tot is not None:
        c = [t for t in truth if t["tax"] in taxes and abs(t["total"] - tot) <= Decimal("0.01")
             and t["date"] == inv.get("date")]
        if len({(t["way"], num(t["number"])) for t in c}) == 1:
            return sorted(c, key=lambda t: 0 if "税登记" in t["src"] else 1)[0]
    return None


def _pick(pool: List[str], key: str) -> str:
    return pool[int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % len(pool)]


def _shift(iso: str, year: int) -> Optional[str]:
    try:
        day = dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return dt.date(year, day.month, min(day.day, 28)).isoformat()


def masters(rows: Dict[str, List[Dict]]) -> Dict:
    """book_rows 读出的试点账套主档 → 草稿单要用的码表和会计期间。"""
    def codes(table, col, keep=lambda r: True):
        return sorted({str(r.get(col) or "").strip() for r in rows.get(table, [])
                       if not r.get("_del") and r.get(col) and keep(r)})

    dates = []
    for r in rows.get("ISPRD", []):
        for k, v in r.items():
            if re.fullmatch(r"(BEG|END)\d+", k) and v:
                dates.append(str(v)[:10])
    return {"suppliers": codes("APMAS", "SUPCOD"), "customers": codes("ARMAS", "CUSCOD"),
            "services": codes("STMAS", "STKCOD", lambda r: str(r.get("STKTYP") or "").strip() not in ("0", "")),
            "stock": codes("STMAS", "STKCOD", lambda r: str(r.get("STKTYP") or "").strip() in ("0", "")),
            "period": {"beg": min(dates), "end": max(dates)} if dates else {}}


def build(root: Path, results: Path, m: Dict) -> List[Draft]:
    listing = {r["编号"]: r for r in csv.DictReader(open(root / "list.tsv", encoding="utf-8"), delimiter="\t")}
    year = dt.date.fromisoformat(m["period"]["end"]).year
    items = m["services"] or m["stock"]
    stats, cache, out = Counter(), {}, []
    for line in results.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        stats["识别份数"] += 1
        stats["识别花费_微美元"] += int(round((r.get("usd") or 0) * 1_000_000))
        read = r.get("read") or {}
        if read.get("entry") != "enter" or not read.get("invoices"):
            stats[f"不录：{read.get('entry') or r.get('error', '读失败')[:20]}"] += 1
            continue
        if r.get("doc_kind") not in KINDS:
            stats[f"不录：票种 {r.get('doc_kind')}"] += 1
            continue
        inv = read["invoices"][0]
        meta = listing.get(r["id"], {})
        own = meta.get("税号", "")
        if own not in cache:
            cache[own] = answers(root, own)
        truth = find(inv, cache[own])
        way = truth["way"] if truth else ("S" if "销" in meta.get("分类", "") else "P")
        sub, vat, tot = dec(inv.get("subtotal")), dec(inv.get("vat")), dec(inv.get("total"))
        if sub is None and tot is not None and vat is not None:
            sub = tot - vat
        date = _shift(inv.get("date") or "", year)
        if not date or sub is None or sub <= 0:
            stats["不录：识别缺日期或金额"] += 1
            continue
        if not vat:
            stats["不录：零税 / 无税（固定流程暂只做 7%）"] += 1
            continue
        kind = ("HP" if way == "P" else "HS") if r.get("doc_kind") in RECEIPTS else ("RR" if way == "P" else "IV")
        counter = digits(inv.get("seller_tax") if way == "P" else inv.get("buyer_tax")) or str(inv.get("seller_name"))
        pool = m["suppliers"] if way == "P" else m["customers"]
        answer = {}
        if truth:
            answer = {"date": _shift(truth["date"], year) or "", "ref": truth["number"], "net": str(truth["subtotal"]),
                      "vat": str(truth["vat"]), "total": str(truth["total"])}
            stats["对上答案"] += 1
        else:
            stats["没对上答案（只测操作，不算准确率）"] += 1
        # 现销 HS 照 Express 默认「1 - รวม VAT」按含税单价录（会计录现销的习惯）；其余按不含税单价
        incl = kind == "HS"
        price = money(tot if (incl and tot is not None) else sub + vat if incl else sub)
        out.append(Draft(
            doc_id=f"{kind}-{r['id']}", doc_type=kind, date=date, party_code=_pick(pool, counter),
            lines=[Line(item_code=_pick(items, f"{own}/{kind}"), qty="1", unit_price=str(price))], vat_included=incl,
            ref_no=num(inv.get("invoice_number"))[:15] if kind in ("RR", "HP") else "",
            ocr_usd=str(r.get("usd") or 0), answer=answer,
            source={"corpus_id": r["id"], "company": meta.get("公司", ""), "doc_kind": r.get("doc_kind") or "",
                    "ocr_total": str(tot), "ocr_vat": str(vat)}))
        stats[f"草稿单 {kind}"] += 1
    sys.stderr.write(json.dumps(dict(stats), ensure_ascii=False) + "\n")
    return out


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["masters"]:
        print(json.dumps(masters(json.loads(Path(args[1]).read_text(encoding="utf-8"))), ensure_ascii=False))
        return 0
    if args[:1] == ["build"]:
        root = Path(args[1])
        m = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        results = Path(args[3]) if len(args) > 3 else root.parent.parent / "runs-prod" / "ex-prod-cowork.jsonl"
        for d in build(root, results, m):
            print(json.dumps(d.to_json(), ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
