#!/usr/bin/env python3
"""离线汇总 `ocr-policy-bench.py` 的结果：用当前代码重新整理每次读数，组合出各方案，比准确率、成本、速度。

不调模型、不花钱。需要 Frappe 环境（用 ERP 的整理与检查代码）：
    env/bin/python scripts/ocr-policy-report.py <bench-out-dir>
"""
import glob
import json
import re
import statistics
import sys
from decimal import Decimal, InvalidOperation

# 泰铢 / 百万 token：按 2026-09-25~26 账单「使用费 ÷ 用量」反推（未扣节省）。3.8 Flash 账单上目前有 −50% 其他节省。
PRICE = {"gemini-3.1-flash-lite": {"TEXT": 8.43, "IMAGE": 8.34, "OUT": 49.43},
         "gemini-3.8-flash": {"TEXT": 48.98, "IMAGE": 49.34, "OUT": 246.7}}
BILLED = {"gemini-3.8-flash": 0.5}
KEY = ("total", "vat", "doc_no", "doc_date", "seller_tax")


def money(value):
    try:
        return Decimal(str(value).replace(",", "").strip()) if value not in (None, "") else None
    except InvalidOperation:
        return None


def code(value):
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def cost(call):
    price, usage = PRICE.get(call.get("model")), call.get("usage") or {}
    if not price:
        return 0.0, 0.0
    text = image = 0
    for part in usage.get("promptTokensDetails") or []:
        if part.get("modality") == "IMAGE":
            image += part.get("tokenCount", 0)
        else:
            text += part.get("tokenCount", 0)
    if not (text or image):
        text = usage.get("promptTokenCount", 0)
    out = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
    listed = (text * price["TEXT"] + image * price["IMAGE"] + out * price["OUT"]) / 1e6
    return listed, listed * (1 - BILLED.get(call["model"], 0))


# 买方就是本公司：语料里各套的本公司税号（v2 合成票的买方税号不合法，不参与）。
OWN_TAX = {"sm-tax": "0105567178203"}
VISION_THB = 1.50 * 33.48 / 1000  # Vision 每页（前 1,000 页 / 月免费，这里按付费价计）
_AMOUNT = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)|(?<![\d.,])\d+\.\d{2}(?!\d)")
_TAX = re.compile(r"(?<!\d)(?:\d[\s-]?){12}\d(?!\d)")
_DATE = re.compile(r"(?<!\d)\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}(?!\d)")


def _differs_by_one(a, b):
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def _hard(raw):
    from pearnly_erp_portal.purchasing import recognition as pr
    from pearnly_erp_portal.recognition import checks

    if not isinstance(raw, dict):
        return 99
    result = pr._structured(raw)
    return 99 if result is None else len(checks.hard(pr.review(result))) + len(pr.review(result)) / 100


def repair(raw):
    """代码修复（不调模型）：票面小计 / VAT / 合计自洽、明细合计比票面合计多出一截而没读到整单折扣时，
    多出的就是整单折扣（语料 Giffarine：3.1 漏读 3,162）。只在修完检查问题变少时采用。"""
    from copy import deepcopy
    from pearnly_erp_portal.recognition import checks

    if not isinstance(raw, dict):
        return raw, []
    printed = raw.get("printed") if isinstance(raw.get("printed"), dict) else {}
    total = checks.money(printed.get("total"))
    lines = raw.get("lines") if isinstance(raw.get("lines"), list) else []
    gross = Decimal(0)
    for row in lines:
        qty = checks.money(row.get("qty")) or 1
        price = checks.money(row.get("unit_price"))
        amount = checks.money(row.get("amount"))
        gross += amount if amount is not None and price is None else (qty * (price or 0) - (checks.money(row.get("discount")) or 0))
    base = _hard(raw)
    if total is None or gross <= total or base < 1:
        return raw, []
    best, changed = raw, []
    for candidate in (gross - total, gross - (checks.money(printed.get("subtotal")) or total)):
        if candidate <= 0:
            continue
        trial = deepcopy(raw)
        trial["discount_total"] = float(candidate)
        if _hard(trial) < _hard(best):
            best, changed = trial, ["discount_total"]
    return best, changed


def anchor(raw, text, own_tax=None):
    """OCR 数字校对（以检查为准）：候选 = OCR 原文里与读数只差一位的金额 / 校验位正确的税号；
    逐个试，**检查问题变少才采用**。单号、日期无法用检查验证，不改（实测按「只差一位」替换反而改坏：日期 89→85、单号 137→135）。
    自家公司：原文里出现本公司税号（差一位以内）而读数没有买方税号 → 填上（它就是买方）。"""
    from copy import deepcopy
    from pearnly_erp_portal.recognition import checks

    if not isinstance(raw, dict) or not text:
        return raw, []
    raw, changed = deepcopy(raw), []
    flat = re.sub(r"[^0-9A-Z]", "", text.upper())
    digits_only = re.sub(r"\D", "", text)
    if own_tax and re.sub(r"\D", "", str(raw.get("buyer_tax_id") or "")) != own_tax:
        if own_tax in digits_only or any(_differs_by_one(t, own_tax) for t in
                                         (re.sub(r"\D", "", m) for m in _TAX.findall(text))):
            raw["buyer_tax_id"], _ = own_tax, changed.append("buyer_tax_id(own)")
    taxes = {t for t in (re.sub(r"\D", "", m) for m in _TAX.findall(text)) if checks.valid_tax_id(t)}
    amounts = {a for a in (money(x.replace(",", "")) for x in _AMOUNT.findall(text)) if a is not None}
    improved = True
    while improved:
        improved, base = False, _hard(raw)
        trials = []
        for key in ("supplier_tax_id",):
            value = re.sub(r"\D", "", str(raw.get(key) or ""))
            if len(value) == 13 and not checks.valid_tax_id(value):
                trials += [(key, None, t) for t in taxes if sum(x != y for x, y in zip(t, value)) <= 2]
        printed = raw.get("printed") if isinstance(raw.get("printed"), dict) else {}
        for key in ("total", "vat", "subtotal", "discount"):
            value = money(printed.get(key))
            if value is not None and value >= 0:
                trials += [("printed", key, float(a)) for a in amounts if _differs_by_one(f"{a:.2f}", f"{value:.2f}")]
        for field, sub, value in trials:
            trial = deepcopy(raw)
            if sub:
                trial[field][sub] = value
            else:
                trial[field] = value
            if _hard(trial) < base:
                raw, improved = trial, True
                changed.append(f"{field}.{sub}" if sub else field)
                break
    return raw, changed


def fields(result):
    """一次读数 → 业务上会记下的字段（实付额、VAT、单号、日期、卖方税号、抵不抵进项、票种）。"""
    from pearnly_erp_portal.purchasing import amounts

    if not result:
        return {}
    if result.get("not_document"):
        return {"not_document": True}
    printed = result.get("printed") or {}
    out = {"doc_no": result.get("doc_no"), "doc_date": result.get("doc_date"), "seller_tax": result.get("supplier_tax_id"),
           "claims_input_vat": result.get("has_vat") is not False, "document_type": result.get("document_type"),
           "read_vat": printed.get("vat"), "documents": 1 + len(result.get("additional_documents") or [])}
    try:
        totals = amounts.calculate(result.get("lines") or [], has_vat=result.get("has_vat") is not False,
                                   price_mode=result.get("price_mode") or "exclusive",
                                   discount_total=result.get("discount_total") or 0, rounding=result.get("rounding") or 0,
                                   discount_before_vat=True, vat_from_total=True)
        out.update(total=totals["net_payable"], vat=totals["vat_amount"])
    except Exception:  # noqa: BLE001 - uncomputable = the card would show no amounts
        out.update(total=None, vat=None)
    return out


def marks(value, gold):
    m = {}
    if gold.get("not_document"):
        return {"not_document": bool(value.get("not_document"))}
    for key in ("total",):
        if key in gold:
            want, got = money(gold[key]), money(value.get(key))
            m[key] = want is not None and got is not None and abs(want - got) <= Decimal("0.01")
    if "vat" in gold:
        got = value.get("vat") if gold.get("claims_input_vat") else value.get("read_vat")
        want, got = money(gold["vat"]), money(got)
        m["vat"] = (want is not None and got is not None and abs(want - got) <= Decimal("0.01")) or (
            not want and not got)
    if "doc_no" in gold:
        a, b = code(gold["doc_no"]), code(value.get("doc_no"))
        m["doc_no"] = bool(a and b) and (a in b or b in a)
    if "doc_date" in gold:
        m["doc_date"] = str(value.get("doc_date") or "")[:10] == gold["doc_date"]
    if "seller_tax" in gold and not gold["seller_tax"].startswith("01055000"):  # v2 的合成税号不计
        m["seller_tax"] = code(gold["seller_tax"]) == code(value.get("seller_tax"))
    if "claims_input_vat" in gold:
        m["claims_input_vat"] = bool(value.get("claims_input_vat")) == bool(gold["claims_input_vat"])
    if "document_type" in gold:
        m["document_type"] = value.get("document_type") == gold["document_type"]
    if "documents" in gold:
        m["documents"] = value.get("documents") == gold["documents"]
    return m


def main():
    from pearnly_erp_portal.purchasing import recognition as pr
    from pearnly_erp_portal.recognition import checks

    docs = [json.load(open(path)) for path in sorted(glob.glob(sys.argv[1] + "/*.json"))]
    policies = {}

    def read(call):
        if not call or not isinstance(call.get("raw"), dict):
            return None, ["unreadable"]
        result = pr._structured(call["raw"])
        return (result, pr.review(result)) if result is not None else (None, ["unreadable"])

    def actionable(issues):
        # 值得再读一遍的：金额 / 结构对不上。只标红不重读的：税号校验位、贷项、票种 / 进项原因、日期软标注。
        return [code for code in checks.hard(issues) if code not in ("tax_id_checksum",)]

    def agree(a, b):
        fa, fb = fields(a), fields(b)
        return all(str(fa.get(k)) == str(fb.get(k)) for k in ("total", "vat", "doc_no", "doc_date"))

    vision_dir = sys.argv[2] if len(sys.argv) > 2 else None
    fixes = {}
    for doc in docs:
        calls = doc.get("calls") or {}
        vision = json.load(open(f"{vision_dir}/{doc['id']}.json")) if vision_dir else {}
        own = OWN_TAX.get(doc.get("set"))
        if "r1" in calls:
            fixed, changed = repair(calls["r1"].get("raw"))
            calls["r1r"] = {**calls["r1"], "raw": fixed}
            for item in changed:
                fixes["code:" + item] = fixes.get("code:" + item, 0) + 1
        for name in ("r1r", "c2"):
            if name in calls:  # 没有 OCR 原文（空白页等）时校对不做改动
                base_raw, _ = repair(calls[name].get("raw")) if name == "c2" else (calls[name].get("raw"), [])
                fixed, changed = anchor(base_raw, vision["text"], own)
                calls[name + "v"] = {**calls[name], "raw": fixed, "vision_ms": vision.get("ms", 0)}
                for item in changed:
                    fixes[item] = fixes.get(item, 0) + 1
        got = {name: read(call) for name, call in calls.items()}
        spend = {name: cost(call) for name, call in calls.items()}
        ms = {name: call.get("ms", 0) for name, call in calls.items()}
        r1, i1 = got.get("r1", (None, ["unreadable"]))
        options = {}
        # 现状：3.1 → 过不了硬闸整张升 3.8；两次取硬闸问题少的（相同取 3.8）。
        if checks.hard(i1) and "a2" in got:
            a2, ia = got["a2"]
            pick = (a2, ia) if len(checks.hard(ia)) <= len(checks.hard(i1)) else (r1, i1)
            options["现状 3.1→3.8"] = (pick, ["r1", "a2"], ms["r1"] + ms["a2"])
        else:
            options["现状 3.1→3.8"] = ((r1, i1), ["r1"], ms.get("r1", 0))
        options["3.1 单读"] = ((r1, i1), ["r1"], ms.get("r1", 0))
        options["3.1 首读加思考"] = (got.get("e1", (None, ["unreadable"])), ["e1"], ms.get("e1", 0))
        if actionable(i1) and "c2" in got:
            c2, ic = got["c2"]
            pick = (c2, ic) if len(checks.hard(ic)) <= len(checks.hard(i1)) else (r1, i1)
            options["3.1→3.1 带提示复核"] = (pick, ["r1", "c2"], ms["r1"] + ms["c2"])
        else:
            options["3.1→3.1 带提示复核"] = ((r1, i1), ["r1"], ms.get("r1", 0))
        d2, i2 = got.get("d2", (None, ["unreadable"]))
        if r1 and d2 and agree(r1, d2):
            vote = (r1, i1)
        else:
            vote = min([(r1, i1), (d2, i2)], key=lambda item: (item[0] is None, len(checks.hard(item[1]))))
            vote = (vote[0], list(vote[1]) + ["reads_disagree"])
        options["3.1 双读投票"] = (vote, ["r1", "d2"], max(ms.get("r1", 0), ms.get("d2", 0)))
        if actionable(vote[1]) and "c2" in got:
            c2, ic = got["c2"]
            pick = (c2, ic) if len(checks.hard(ic)) <= len(checks.hard(vote[1])) else vote
            options["3.1 双读投票→复核"] = (pick, ["r1", "d2", "c2"], max(ms.get("r1", 0), ms.get("d2", 0)) + ms["c2"])
        else:
            options["3.1 双读投票→复核"] = (vote, ["r1", "d2"], max(ms.get("r1", 0), ms.get("d2", 0)))
        options["3.1 + 代码修复"] = (got.get("r1r", (None, ["unreadable"])), ["r1"], ms.get("r1", 0))
        if "r1rv" in got:
            vms = max(ms.get("r1", 0), calls["r1rv"].get("vision_ms", 0))
            r1v, i1v = got["r1rv"]
            options["3.1 + 代码修复 + OCR 校对"] = ((r1v, i1v), ["r1", "vision"], vms)
            if checks.hard(i1v) and "a2" in got:
                a2, ia = got["a2"]
                pick = (a2, ia) if len(checks.hard(ia)) < len(checks.hard(i1v)) else (r1v, i1v)
                options["3.1 + 修复 + OCR 校对 → 仍不过才 3.8"] = (pick, ["r1", "vision", "a2"], vms + ms["a2"])
            else:
                options["3.1 + 修复 + OCR 校对 → 仍不过才 3.8"] = ((r1v, i1v), ["r1", "vision"], vms)
        # 新方案：3.1 读 + 代码修复 → 金额 / 结构仍不过才 3.1 带提示复核 → 只在「没写买方 / 税号校验不过」时
        # 调 OCR 做以检查为准的核对（自家公司、税号）→ 还不过标红，不用 3.8。
        step, used, elapsed = got.get("r1r", (None, ["unreadable"])), ["r1"], ms.get("r1", 0)
        if actionable(step[1]) and "c2" in calls:
            c2r = read({**calls["c2"], "raw": repair(calls["c2"].get("raw"))[0]})
            if len(checks.hard(c2r[1])) < len(checks.hard(step[1])):
                step = c2r
            used, elapsed = used + ["c2"], elapsed + ms.get("c2", 0)
        if {"tax_invoice_without_buyer", "tax_id_checksum"} & set(step[1]) and vision.get("text"):
            source = "c2" if step is not got.get("r1r") and "c2" in calls else "r1r"
            base_raw = repair(calls["c2"].get("raw"))[0] if source == "c2" else calls["r1r"]["raw"]
            fixed, _changed = anchor(base_raw, vision["text"], own)
            anchored = read({**calls["r1"], "raw": fixed})
            if len(anchored[1]) <= len(step[1]):
                step = anchored
            used, elapsed = used + ["vision"], elapsed + vision.get("ms", 0)
        options["新方案（3.1 + 代码修复 + 按需复核 / OCR，无 3.8）"] = (step, used, elapsed)
        spend["vision"] = (VISION_THB, VISION_THB)
        for name, ((result, issues), used, elapsed) in options.items():
            value = fields(result)
            m = marks(value, doc.get("gold") or {})
            wrong = [k for k, ok in m.items() if not ok]
            flagged = result is None or bool(issues)  # 卡片 / 网页上每条原因都红字显示
            row = policies.setdefault(name, {"docs": 0, "fields": {}, "all_ok": 0, "silent": 0, "flag": 0,
                                             "list": [], "billed": [], "ms": [], "calls": [], "by_set": {}})
            row["docs"] += 1
            for code_ in set(issues):
                row.setdefault("codes", {}).setdefault(code_, 0)
                row["codes"][code_] += 1
            if key_wrong_pre := [k for k in wrong if k in ("total", "vat", "claims_input_vat", "not_document")]:
                row.setdefault("errors", []).append((doc["id"], key_wrong_pre, bool(result is None or issues)))
            for k, ok in m.items():
                row["fields"].setdefault(k, [0, 0])[0 if ok else 1] += 1
            key_wrong = [k for k in wrong if k in ("total", "vat", "claims_input_vat", "not_document")]
            row["all_ok"] += not key_wrong
            row["silent"] += bool(key_wrong) and not flagged
            row["flag"] += flagged
            row["list"].append(sum(spend[n][0] for n in used if n in spend))
            row["billed"].append(sum(spend[n][1] for n in used if n in spend))
            row["ms"].append(elapsed)
            row["calls"].append(len(used))
            bucket = row["by_set"].setdefault(doc.get("set"), [0, 0])
            bucket[0 if not key_wrong else 1] += 1
    print(f"语料 {len(docs)} 张（{', '.join(sorted({d.get('set') for d in docs}))}）\n")
    header = "方案 | 金额+VAT+进项全对 | 错了却没标红 | 标红率 | 每张实付 ฿ | 每张原价 ฿ | 平均耗时 s | P95 s | 平均调用"
    print(header)
    for name, row in policies.items():
        n = row["docs"]
        p95 = sorted(row["ms"])[int(0.95 * (n - 1))] / 1000
        print(f"{name} | {row['all_ok']}/{n} | {row['silent']} | {row['flag'] / n:.0%} | {statistics.mean(row['billed']):.3f} | "
              f"{statistics.mean(row['list']):.3f} | {statistics.mean(row['ms']) / 1000:.1f} | {p95:.1f} | "
              f"{statistics.mean(row['calls']):.2f}")
    for name in ("现状 3.1→3.8", "新方案（3.1 + 代码修复 + 按需复核 / OCR，无 3.8）"):
        row = policies[name]
        print("\n", name, "标红原因", sorted(row.get("codes", {}).items(), key=lambda kv: -kv[1]))
        print(" 错的", [(i[:30], w, "标红" if f else "没标红") for i, w, f in row.get("errors", [])])
    print("\nOCR 校对改动次数", fixes)
    print("\n逐字段（对/总）")
    for name, row in policies.items():
        print(name, {k: f"{v[0]}/{v[0] + v[1]}" for k, v in row["fields"].items()},
              {k: f"{v[0]}/{v[0] + v[1]}" for k, v in row["by_set"].items()})


if __name__ == "__main__":
    import frappe  # noqa: F401 - the ERP modules import it

    sys.exit(main())
