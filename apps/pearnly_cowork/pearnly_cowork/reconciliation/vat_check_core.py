# 销项税报告核查（Cowork，用户 2026-09-26 定迁）：配对与维度差异（services/recon/vat_recon_core.py）；另含 services/vat/vat_ocr_extract.py::_ocr_validate_invoice
# 来源：旧线上 ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188 的同名逻辑，逐字移植，只改导入路径；
# 有意改动：发票一侧分公司为空时不比分公司（见 `_diff_dims`，用户 2026-09-26 定）；客户名按 `company_name` 比
# （忽略空格、公司前后缀、总公司 / 分公司标注，用户 2026-09-30 定），税号不同照样报。
"""VAT 对账核心:金额/期间归一化 + 一对一配对 + 维度差异计算 · vat_excel_export 拆分 leaf。"""

from typing import List, Dict, Any, Optional

from pearnly_cowork.reconciliation.normalization import (
    company_name,
    normalize_invoice_no,
    normalize_tax_id,
    normalize_str,
    parse_date,
    tax_id_fuzzy_distance,
)


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return round(float(str(v).replace(",", "")), 2)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════════════
# v118.32.4.9.6 新增 · 辅助函数(Bug 1/2/3/4/5)
# ════════════════════════════════════════════════════════════════════════


def _derive_period(date_str: str, period_str: str = "") -> str:
    """Bug 1 · 期间降级 · period 非空直接用 · 否则从日期算 MM/YYYY 公历(BE 自动转)"""
    s = (period_str or "").strip()
    if s:
        return s
    d = parse_date(date_str or "")
    if d:
        return f"{d.month:02d}/{d.year}"
    return ""


def _dominant_report_period(parsed: Dict[str, Any]):
    """单份 VAT 报告的主导期间 (year, month):meta 有 period 直接用 · 否则取各行 report_date 众数。

    P1-2 修(2026-05-25):pdf_text_regex / pipeline 路径 meta={} · 此前期间永远拿不到 → 混月份不拦。
    用行日期众数兜底:单文件内个别噪声日期不影响(取出现最多的年/月)· 真·两月文件才被跨文件比对拦下。
    """
    m = parsed.get("meta", {}) or {}
    py, pm = m.get("period_year"), m.get("period_month")
    if py and pm:
        return (py, pm)
    counts: Dict[tuple, int] = {}
    for r in parsed.get("rows") or []:
        d = parse_date(r.get("report_date") or "")
        if d:
            counts[(d.year, d.month)] = counts.get((d.year, d.month), 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _eq_amount(a, b, tol: float = 0.01) -> bool:
    try:
        return abs(float(a or 0) - float(b or 0)) <= tol
    except Exception:
        return False


def _get_inv_total(inv: Dict) -> float:
    try:
        return round(float(inv.get("total_amount") or 0), 2)
    except Exception:
        return 0.0


def _get_rep_total(row: Dict) -> float:
    """报告行的含税额：有未税列按「未税 + VAT」；没有未税列、只有合计列时合计本身就含税，不再加一遍 VAT
    （2026-09-29 排查：表头 Amount / VAT / Total 的报告 1,000 + 70 = 1,070 算成 1,140，一致的行判成金额不一致）。"""
    pre = row.get("report_amount_pre_vat")
    vat = row.get("report_vat_amount") or 0
    try:
        if not pre and row.get("report_amount"):
            return round(float(row["report_amount"]), 2)
        return round(float(pre or 0) + float(vat or 0), 2)
    except Exception:
        return 0.0


def _build_recon_pairs(
    invoices: List[Dict], report_rows: List[Dict], lang: str = "th"
) -> Dict[str, Any]:
    """v118.32.4.9.6 · 一对一配对引擎(Bug 2/3/4/5)
    多轮匹配 · 优先级从高到低:
      1) 发票号 normalize 一致 → matched
      2) 税号 + 含税金额 一致 → matched
      3) 散客双方都无税号 · 客户名 + 发票号 + 金额 三重一致 → matched_cash(Bug 2)
      4) 税号编辑距离 ≤2 + 金额一致 → fuzzy(Bug 4 · 疑似 · 标 "请确认")
      5) 发票税号空 + 报告里能查到(客户名+发票号+金额一致)→ ocr_missing(Bug 5 · OCR 漏抽)
      剩余:invoice_orphan / report_orphan
    返回: {pairs:[{inv_idx, rep_idx, kind, note}], unmatched_inv:[idx], unmatched_rep:[idx]}
    """
    lab = diff_labels(lang)  # 配对备注本地化(散客匹配 / 税号疑似 · 跟用户语言)
    n_inv = len(invoices)
    n_rep = len(report_rows)
    used_inv: set = set()
    used_rep: set = set()
    pairs: List[Dict] = []

    # ── 第 1 轮 · 发票号 normalize 一致
    rep_no_idx: Dict[str, List[int]] = {}
    for ri in range(n_rep):
        key = normalize_invoice_no(report_rows[ri].get("report_invoice_no") or "")
        if key:
            rep_no_idx.setdefault(key, []).append(ri)

    for ii in range(n_inv):
        key = normalize_invoice_no(invoices[ii].get("invoice_no") or "")
        if not key:
            continue
        candidates = rep_no_idx.get(key, [])
        for ri in candidates:
            if ri in used_rep:
                continue
            used_inv.add(ii)
            used_rep.add(ri)
            pairs.append({"inv_idx": ii, "rep_idx": ri, "kind": "matched", "note": ""})
            break

    # ── 第 2 轮 · 税号 + 含税金额 一致
    for ii in range(n_inv):
        if ii in used_inv:
            continue
        inv = invoices[ii]
        t1 = normalize_tax_id(inv.get("buyer_tax_id") or "")
        if not t1:
            continue
        a1 = _get_inv_total(inv)
        for ri in range(n_rep):
            if ri in used_rep:
                continue
            rep = report_rows[ri]
            t2 = normalize_tax_id(rep.get("report_buyer_tax_id") or "")
            if t1 != t2:
                continue
            if _eq_amount(a1, _get_rep_total(rep)):
                used_inv.add(ii)
                used_rep.add(ri)
                pairs.append({"inv_idx": ii, "rep_idx": ri, "kind": "matched", "note": ""})
                break

    # ── 第 3 轮 · 散客双方都无税号 · 三重匹配(Bug 2)
    for ii in range(n_inv):
        if ii in used_inv:
            continue
        inv = invoices[ii]
        if normalize_tax_id(inv.get("buyer_tax_id") or ""):
            continue  # 不是散客
        inv_name = company_name(inv.get("buyer_name") or "")
        inv_no = normalize_invoice_no(inv.get("invoice_no") or "")
        inv_amt = _get_inv_total(inv)
        for ri in range(n_rep):
            if ri in used_rep:
                continue
            rep = report_rows[ri]
            if normalize_tax_id(rep.get("report_buyer_tax_id") or ""):
                continue  # 报告这行不是散客
            rep_name = company_name(rep.get("report_buyer_name") or "")
            rep_no = normalize_invoice_no(rep.get("report_invoice_no") or "")
            rep_amt = _get_rep_total(rep)
            # 名 + 号 + 金 三重 · 金最严 · 名/号至少一个非空且一致
            if not _eq_amount(inv_amt, rep_amt):
                continue
            name_ok = inv_name and rep_name and inv_name == rep_name
            no_ok = inv_no and rep_no and inv_no == rep_no
            if name_ok or no_ok:
                used_inv.add(ii)
                used_rep.add(ri)
                pairs.append(
                    {
                        "inv_idx": ii,
                        "rep_idx": ri,
                        "kind": "matched_cash",
                        "note": lab["cash_match"],
                    }
                )
                break

    # ── 第 4 轮 · 税号编辑距离 ≤2 + 金额一致 → 疑似(Bug 4)
    for ii in range(n_inv):
        if ii in used_inv:
            continue
        inv = invoices[ii]
        t1 = normalize_tax_id(inv.get("buyer_tax_id") or "")
        if not t1 or len(t1) < 10:  # 太短不参与 fuzzy
            continue
        a1 = _get_inv_total(inv)
        best = None
        for ri in range(n_rep):
            if ri in used_rep:
                continue
            rep = report_rows[ri]
            t2 = normalize_tax_id(rep.get("report_buyer_tax_id") or "")
            if not t2 or len(t2) < 10:
                continue
            d = tax_id_fuzzy_distance(t1, t2)
            if d == 0 or d > 2:
                continue
            if not _eq_amount(a1, _get_rep_total(rep)):
                continue
            if best is None or d < best[1]:
                best = (ri, d)
        if best:
            ri, d = best
            used_inv.add(ii)
            used_rep.add(ri)
            pairs.append(
                {
                    "inv_idx": ii,
                    "rep_idx": ri,
                    "kind": "fuzzy",
                    "note": lab["tax_fuzzy"].format(d=d),
                }
            )

    # ── 第 5 轮 · 发票税号空 + 报告反查(Bug 5 · OCR 漏抽)
    for ii in range(n_inv):
        if ii in used_inv:
            continue
        inv = invoices[ii]
        if normalize_tax_id(inv.get("buyer_tax_id") or ""):
            continue  # 有税号 · 不是 OCR 漏抽
        inv_name = company_name(inv.get("buyer_name") or "")
        inv_no = normalize_invoice_no(inv.get("invoice_no") or "")
        inv_amt = _get_inv_total(inv)
        for ri in range(n_rep):
            if ri in used_rep:
                continue
            rep = report_rows[ri]
            rep_tax = normalize_tax_id(rep.get("report_buyer_tax_id") or "")
            if not rep_tax:
                continue  # 报告这行也没税号 → 第 3 轮已处理
            rep_name = company_name(rep.get("report_buyer_name") or "")
            rep_no = normalize_invoice_no(rep.get("report_invoice_no") or "")
            rep_amt = _get_rep_total(rep)
            if not _eq_amount(inv_amt, rep_amt):
                continue
            name_ok = inv_name and rep_name and inv_name == rep_name
            no_ok = inv_no and rep_no and inv_no == rep_no
            if name_ok and no_ok:
                used_inv.add(ii)
                used_rep.add(ri)
                pairs.append(
                    {
                        "inv_idx": ii,
                        "rep_idx": ri,
                        "kind": "ocr_missing",
                        "note": lab["ocr_missing_tax"].format(tax=rep_tax),
                    }
                )
                break

    # ── 后处理 · 跨轮升级状态(第 1/2 轮匹配后才能定的标签)
    for p in pairs:
        if p["kind"] != "matched":  # fuzzy / matched_cash / ocr_missing 已定 · 不动
            continue
        inv = invoices[p["inv_idx"]]
        rep = report_rows[p["rep_idx"]]
        inv_tax = normalize_tax_id(inv.get("buyer_tax_id") or "")
        rep_tax = normalize_tax_id(rep.get("report_buyer_tax_id") or "")
        # 双方都无税号 · 实为散客匹配
        if not inv_tax and not rep_tax:
            p["kind"] = "matched_cash"
            p["note"] = lab["cash_match"]
            continue
        # 发票无税号 但 报告有 → OCR 漏抽(Bug 5)
        if not inv_tax and rep_tax:
            p["kind"] = "ocr_missing"
            p["note"] = lab["ocr_missing_tax"].format(tax=rep_tax)

    unmatched_inv = [i for i in range(n_inv) if i not in used_inv]
    unmatched_rep = [i for i in range(n_rep) if i not in used_rep]
    return {"pairs": pairs, "unmatched_inv": unmatched_inv, "unmatched_rep": unmatched_rep}


# 逐行差异文案 4 语标签(导出 Excel 跟用户语言走 · 泰语报告别冒中文)。
_DIFF_LABELS = {
    "th": {
        "diff": "ต่าง",
        "days": "วัน",
        "date_one_missing": "วันที่ขาดด้านหนึ่ง",
        "inv_empty": "(ใบกำกับว่าง)",
        "rep_empty": "(รายงานว่าง)",
        "inv": "ใบกำกับ",
        "rep": "รายงาน",
        "amt_pre_diff": "ต่างก่อนVAT",
        "amt_vat_diff": "ต่างVAT",
        "amt_pre_ok": "ก่อนVATตรง",
        "amt_vat_ok": "VATตรง",
        "ocr_incomplete": "โปรดตรวจสอบกับ PDF ต้นฉบับ",
        "cash_match": "จับคู่ลูกค้าทั่วไป 3 จุด",
        "tax_fuzzy": "เลขภาษีต่าง {d} หลัก · โปรดตรวจ",
        "ocr_missing_tax": "เลขภาษีไม่ครบ · ที่ถูกคือ {tax}",
    },
    "en": {
        "diff": "Diff",
        "days": "d",
        "date_one_missing": "Date missing on one side",
        "inv_empty": "(invoice empty)",
        "rep_empty": "(report empty)",
        "inv": "inv",
        "rep": "report",
        "amt_pre_diff": "Pre-VAT diff",
        "amt_vat_diff": "VAT diff",
        "amt_pre_ok": "Pre-VAT ok",
        "amt_vat_ok": "VAT ok",
        "ocr_incomplete": "Please verify against the source PDF",
        "cash_match": "Cash customer triple match",
        "tax_fuzzy": "Tax ID differs {d} digit(s) · please verify",
        "ocr_missing_tax": "Tax ID missing · should be {tax}",
    },
    "zh": {
        "diff": "差",
        "days": "天",
        "date_one_missing": "日期一边缺",
        "inv_empty": "(发票空)",
        "rep_empty": "(报告空)",
        "inv": "发",
        "rep": "报",
        "amt_pre_diff": "净额差",
        "amt_vat_diff": "VAT 差",
        "amt_pre_ok": "净额一致",
        "amt_vat_ok": "VAT 一致",
        "ocr_incomplete": "建议核对原 PDF 确认完整",
        "cash_match": "散客三重匹配",
        "tax_fuzzy": "税号差 {d} 位 · 请人工确认",
        "ocr_missing_tax": "税号缺失 · 应为 {tax}",
    },
    "ja": {
        "diff": "差",
        "days": "日",
        "date_one_missing": "日付が片方欠落",
        "inv_empty": "(請求書空)",
        "rep_empty": "(報告書空)",
        "inv": "請求書",
        "rep": "報告",
        "amt_pre_diff": "税抜差",
        "amt_vat_diff": "VAT差",
        "amt_pre_ok": "税抜一致",
        "amt_vat_ok": "VAT一致",
        "ocr_incomplete": "元PDFとご照合ください",
        "cash_match": "一般客3点照合",
        "tax_fuzzy": "税番号 {d} 桁差 · 要確認",
        "ocr_missing_tax": "税番号なし · 正しくは {tax}",
    },
}


def diff_labels(lang: str) -> Dict[str, str]:
    """取逐行差异/金额备注的本地化标签(未知语言回落 th)。"""
    return _DIFF_LABELS.get(lang or "th", _DIFF_LABELS["th"])


def _diff_dims(inv: Dict, rep: Dict, lang: str = "th") -> Dict[str, str]:
    """v118.32.4.9.6.1 · 6 维度差异检测(发票号 / 日期 / 期间 / 税号 / 分公司 / 客户名)
    tax_id 值以 '~' 开头表示 fuzzy(编辑距离 ≤2) · 不含 '~' 为硬差异
    返回 dict · 值为空 = 无差异 · 非空 = 差异文案(随 lang 本地化)"""
    lab = diff_labels(lang)
    out = {"inv_no": "", "date": "", "period": "", "tax_id": "", "branch": "", "name": ""}
    # 发票号
    n1 = normalize_invoice_no(inv.get("invoice_no") or "")
    n2 = normalize_invoice_no(rep.get("report_invoice_no") or "")
    if n1 != n2 and (n1 or n2):
        out["inv_no"] = f"{inv.get('invoice_no') or '—'} ≠ {rep.get('report_invoice_no') or '—'}"
    # 日期
    d1 = parse_date(inv.get("invoice_date"))
    d2 = parse_date(rep.get("report_date"))
    if d1 and d2 and d1 != d2:
        delta = (d2 - d1).days
        out["date"] = f"{lab['diff']} {delta:+d} {lab['days']}"
    elif (d1 is None) != (d2 is None):
        out["date"] = lab["date_one_missing"]
    # 期间
    p1 = _derive_period(inv.get("invoice_date") or "", inv.get("period") or "")
    p2 = ""
    if d2:
        p2 = f"{d2.month:02d}/{d2.year}"
    if p1 and p2 and p1 != p2:
        out["period"] = f"{p1} ≠ {p2}"
    # 税号差异 (Bug 3 · v118.32.4.9.6.1)
    inv_tax = normalize_tax_id(inv.get("buyer_tax_id") or "")
    rep_tax = normalize_tax_id(rep.get("report_buyer_tax_id") or "")
    if (inv_tax or rep_tax) and inv_tax != rep_tax:
        raw1 = inv.get("buyer_tax_id") or inv_tax
        raw2 = rep.get("report_buyer_tax_id") or rep_tax
        if not inv_tax:
            out["tax_id"] = f"{lab['inv_empty']} · {lab['rep']}={raw2}"
        elif not rep_tax:
            out["tax_id"] = f"{lab['inv']}={raw1} · {lab['rep_empty']}"
        else:
            _td = tax_id_fuzzy_distance(inv_tax, rep_tax)
            if _td <= 2:
                out["tax_id"] = f"~{raw1} ≈ {raw2}"  # ~ 前缀 = fuzzy
            else:
                out["tax_id"] = f"{raw1} ≠ {raw2}"
    # 分公司
    b1 = (inv.get("buyer_branch") or "").strip()
    b2 = (rep.get("report_buyer_branch") or "").strip()

    # 简单规范:总部相关词都归 00000
    # P0-1 修(2026-05-25 销项税回归):空分公司 ≡ 00000(总部)。泰国税务惯例 ——
    #   发票常不印分公司码,缺省即总部 สำนักงานใหญ่(00000)。此前空串归 "" ≠ "00000"
    #   → 每张正常发票(报告侧标准化成 00000)被误判"分公司差异",TC01 全匹配资料判 matched=0。
    #   归一后:空 vs 00000 视为相同(不报差);真实支店码(如 00001)vs 00000 仍正常报差。
    def _norm_branch(s):
        if not s:
            return "00000"
        n = s.lower().replace(" ", "")
        if n in ("00000", "สำนักงานใหญ่", "head", "headoffice", "hq", "สนญ"):
            return "00000"
        return s

    # 两侧都空时归一后同为 00000 · 不会进差异;只有一侧是真实非总部支店码才报差
    # Cowork 有意改动（用户 2026-09-26 定）：发票一侧没读到分公司（空）就不比分公司——
    #   识别目前读不出分公司码，空不代表总部；空 vs 报告的支店码不再报“分公司差异”。发票读到了照旧比。
    if b1 and _norm_branch(b1) != _norm_branch(b2):
        out["branch"] = f"{b1 or '—'} ≠ {b2 or '—'}"
    # 客户名
    name1 = (inv.get("buyer_name") or "").strip()
    name2 = (rep.get("report_buyer_name") or "").strip()
    if company_name(name1) != company_name(name2) and (name1 or name2):
        out["name"] = f"{name1 or '—'} ≠ {name2 or '—'}"
    return out


# ── 来源 services/vat/vat_ocr_extract.py ──
def _ocr_validate_invoice(inv: Dict) -> List[str]:
    """7 项 OCR 准确率底线校验 · 返回问题 key 列表(空=全通过)
    校验规则:发票号空 / 客户名空 / 税号非13位 / 日期格式异常 /
             含税金额为0 / VAT≠7% / 净额+VAT≠总额"""
    warns: List[str] = []
    # 1. 发票号空
    if not (inv.get("invoice_no") or "").strip():
        warns.append("w_invoice_no_empty")
    # 2. 客户名空
    if not (inv.get("buyer_name") or "").strip():
        warns.append("w_buyer_name_empty")
    # 3. 税号非 13 位(仅当非空时校验 · 只数数字位数)
    tax_digits = "".join(c for c in (inv.get("buyer_tax_id") or "") if c.isdigit())
    if tax_digits and len(tax_digits) != 13:
        warns.append("w_tax_id_bad_length")
    # 4. 日期格式异常(仅当非空时校验)
    date_str = (inv.get("invoice_date") or "").strip()
    if date_str and parse_date(date_str) is None:
        warns.append("w_date_parse_fail")
    # 5. 含税金额为 0 或缺失
    total = _to_float(inv.get("total_amount"))
    if total is None or total == 0.0:
        warns.append("w_total_zero")
    # 6. VAT ≠ 7%(净额 > 10 THB 才检查 · 容差 max(1 THB, 5%))
    pre = _to_float(inv.get("amount_pre_vat"))
    vat = _to_float(inv.get("vat_amount"))
    if pre is not None and vat is not None and pre > 10.0:
        expected_vat = round(pre * 0.07, 2)
        tol = max(1.0, expected_vat * 0.05)
        if abs(vat - expected_vat) > tol:
            warns.append("w_vat_rate_mismatch")
    # 7. 净额 + VAT ≠ 总额(三值都有 · 容差 0.02 THB)
    if pre is not None and vat is not None and total is not None and total > 0:
        computed = round(pre + vat, 2)
        if abs(computed - total) > 0.02:
            warns.append("w_amount_sum_mismatch")
    return warns


# ── 来源 services/vat/vat_report_merge.py::_merge_vat_reports_active（解析之后那一半）──
# 逐字保留“多份报告必须同卖方、同期间”的校验与拼接；解析本身由调用方逐份完成后传进来。
def merge_parsed_reports(parsed_list: List[Dict[str, Any]], filenames: List[str]) -> Dict[str, Any]:
    metas = [p.get("meta", {}) for p in parsed_list]
    sellers = set()
    for m in metas:
        sid = m.get("seller_tax_id") or m.get("issuer_tax_id") or ""
        if sid:
            sellers.add(sid)
    periods = set()
    for p in parsed_list:
        dp = _dominant_report_period(p)
        if dp:
            periods.add(dp)

    if len(sellers) > 1:
        return {"ok": False, "rows": [], "error_code": "vat_check.sellers_differ",
                "error_args": [" / ".join(sorted(sellers))]}
    if len(periods) > 1:
        _ps = " / ".join(f"{y}-{mn:02d}" for (y, mn) in sorted(periods))
        return {"ok": False, "rows": [], "error_code": "vat_check.periods_differ", "error_args": [_ps]}

    all_rows: List[Dict] = []
    seq = 0
    for p, src_filename in zip(parsed_list, filenames):
        for row in p.get("rows", []):
            seq += 1
            row = dict(row)
            row["row_no"] = seq
            row["source_filename"] = src_filename
            row.setdefault("source_page", row.get("page_no") or row.get("page") or "")
            all_rows.append(row)

    seller_tax_id = next(iter(sellers)) if sellers else ""
    seller_name = ""
    for m in metas:
        if m.get("seller_name"):
            seller_name = m["seller_name"]
            break
    period_year = next(iter(periods))[0] if periods else None
    period_month = next(iter(periods))[1] if periods else None
    return {"ok": True, "rows": all_rows, "seller_tax_id": seller_tax_id, "seller_name": seller_name,
            "period_year": period_year, "period_month": period_month, "row_count": len(all_rows)}


# ── 来源 services/vat/vat_report_parser.py::report_row_to_invoice（结构化发票表直读）──
def report_row_to_invoice(row: Dict[str, Any], filename: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "filename": filename,
        "buyer_tax_id": row.get("report_buyer_tax_id", ""),
        "buyer_name": row.get("report_buyer_name", ""),
        "buyer_branch": row.get("report_buyer_branch", ""),
        "invoice_no": row.get("report_invoice_no", ""),
        "invoice_date": row.get("report_date", ""),
        "amount_pre_vat": row.get("report_amount_pre_vat"),
        "vat_amount": row.get("report_vat_amount"),
        # 发票表没有合计列时按「未税 + VAT」补上（与 CSV 发票表同一口径）；原来直接取空，按 0 比，一致的票判成金额不一致。
        "total_amount": row.get("report_amount") if row.get("report_amount") is not None else _pre_plus_vat(row),
    }


def _pre_plus_vat(row: Dict[str, Any]) -> Optional[float]:
    pre, vat = row.get("report_amount_pre_vat"), row.get("report_vat_amount")
    if pre is None:
        return None
    return round(float(pre) + float(vat or 0), 2)
