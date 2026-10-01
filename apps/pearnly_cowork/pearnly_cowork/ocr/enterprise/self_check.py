"""逐页抽取后的自核：这一页读出来的数自己对不对得上（纯函数，不调模型、不读文件）。

2026-09-30 用户同意「漏读页自动再读一次」（每次约 0.5 泰铢），压测窗口补上税报页合计：

- 银行账单：页内余额链（页上印的承前余额 → 逐笔 ± 金额 = 逐笔余额 → 页上印的结转余额）有断点，就是这页漏读或读错了行；
- 税报：这一页的行加起来要等于页上印的合计（未税 / 税额 / 含税，印了哪个核哪个）；印的是累计到本页的合计
  （结转、最后一页的全表总计）也算对上。一个合计都没读到的页没法自核。

判定只看读出来的数，不拿页上印的合计去「补」行：对不上就交给调用方决定要不要再读、以及标出来。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from pearnly_cowork.ocr.enterprise.local.common import dec

TOLERANCE = Decimal("0.01")
VAT_FIELDS = (("subtotal", "total_subtotal"), ("vat", "total_vat"), ("total", "total_total"))

PASSED = "passed"
MISMATCH = "mismatch"
NO_TOTAL = "no_total"


def _signed(entry: dict) -> Optional[Decimal]:
    deposit, withdrawal = dec(entry.get("deposit")), dec(entry.get("withdrawal"))
    if deposit is not None or withdrawal is not None:
        return abs(deposit or Decimal(0)) - abs(withdrawal or Decimal(0))
    amount = dec(entry.get("amount"))
    if amount is None:
        return None
    direction = str(entry.get("direction") or "").lower()
    if direction == "deposit":
        return abs(amount)
    if direction == "withdrawal":
        return -abs(amount)
    return None


def bank_breaks(document: dict) -> int:
    """页内余额链断点数：从页上印的承前余额（没印就从第一笔的余额）起逐笔核，最后再核页上印的结转余额。"""
    previous = dec(document.get("opening_balance"))
    breaks = 0
    for entry in document.get("entries") or []:
        balance, signed = dec(entry.get("balance")), _signed(entry)
        if previous is not None and balance is not None and signed is not None \
                and abs(previous + signed - balance) > TOLERANCE:
            breaks += 1
        if balance is not None:
            previous = balance
    closing = dec(document.get("closing_balance"))
    if closing is not None and previous is not None and abs(closing - previous) > TOLERANCE:
        breaks += 1
    return breaks


def vat_sums(document: dict) -> Dict[str, Decimal]:
    sums = {field: Decimal(0) for field, _printed in VAT_FIELDS}
    for entry in document.get("entries") or []:
        for field, _printed in VAT_FIELDS:
            sums[field] += dec(entry.get(field)) or Decimal(0)
    return sums


def vat_state(document: dict, before: Optional[Dict[str, Decimal]] = None) -> Tuple[str, Dict[str, Decimal]]:
    """``(状态, 本页各栏合计)``：``before`` 是本页之前各页的累计（同一份文件里）。"""
    sums = vat_sums(document)
    printed = {field: dec(document.get(key)) for field, key in VAT_FIELDS}
    printed = {field: value for field, value in printed.items() if value is not None}
    if not printed:
        return NO_TOTAL, sums
    before = before or {}
    for field, value in printed.items():
        page = sums[field]
        running = page + before.get(field, Decimal(0))
        if abs(value - page) > TOLERANCE and abs(value - running) > TOLERANCE:
            return MISMATCH, sums
    return PASSED, sums


def printed_totals(document: dict) -> Dict[str, str]:
    """页上印的合计（有的才带），存进页的留痕，供几份文件合起来再核累计。"""
    values = {field: dec(document.get(key)) for field, key in VAT_FIELDS}
    return {field: str(value) for field, value in values.items() if value is not None}


def unverified_report_pages(reports) -> list:
    """几份税报（照片 / 扫描件识别出的页，按上传顺序）里没法自核的页：``[(文件名, [页号…]), …]``。

    ``reports``：``[(文件名, [(页号, 页的 document), …]), …]``。累计跨文件算（一页一张照片时最后一张印的全表总计要和
    前面所有照片的行合起来比）：
    - 印了合计、和本页行合计或累计到本页的合计都对不上 → 无法自核；
    - 没印（没读到）合计的页先挂着，后面哪页印的合计和累计对上了，前面挂着的就一起算核过了；到最后还挂着的 → 无法自核；
    - 既没有行也没有合计的页（封面、空白页）不算。
    """
    running = {field: Decimal(0) for field, _key in VAT_FIELDS}
    flagged: Dict[str, list] = {}
    pending = []
    for name, pages in reports:
        for number, document in pages:
            sums = vat_sums(document)
            after = {field: running[field] + sums[field] for field in running}
            printed = {field: dec(document.get(key)) for field, key in VAT_FIELDS}
            printed = {field: value for field, value in printed.items() if value is not None}
            if not printed:
                if document.get("entries"):
                    pending.append((name, number))
            elif all(abs(value - after[field]) <= TOLERANCE for field, value in printed.items()):
                pending.clear()
            elif not all(abs(value - sums[field]) <= TOLERANCE or abs(value - after[field]) <= TOLERANCE
                    for field, value in printed.items()):
                flagged.setdefault(name, []).append(number)
            running = after
    for name, number in pending:
        flagged.setdefault(name, []).append(number)
    order = [name for name, _pages in reports]
    return [(name, sorted(set(flagged[name]))) for name in order if name in flagged]


# 税报逐行自检（压测窗口 2026-09-30：照片税报行错误 3–4%，要做到「错的都被点名」）：只看读出来的这一行自己对不对得上。
VAT_RATE = Decimal("0.07")
ROW_VAT_RATE, ROW_VAT_ONLY, ROW_DUPLICATE, ROW_DATE = "vat_rate", "vat_without_base", "duplicate", "date"


def _number_date(invoice_no: str):
    """单号开头印着佛历 年月日（「690526-011」= 2569-05-26）时的那个日期；不是这种写法回 None。"""
    import re
    from datetime import date

    found = re.match(r"^\D{0,4}(\d{2})(\d{2})(\d{2})\D", str(invoice_no or "") + "-")
    if not found:
        return None
    year, month, day = (int(part) for part in found.groups())
    try:
        return date(2500 + year - 543, month, day)
    except ValueError:
        return None


def _row_date(value):
    """「17/05/2569」「17/05/69」「2026-05-17」→ 日期（佛历换公历）；认不出回 None。"""
    import re
    from datetime import date

    text = str(value or "").strip()
    iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    printed = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$", text)
    try:
        if iso:
            year, month, day = (int(part) for part in iso.groups())
        elif printed:
            day, month, year = (int(part) for part in printed.groups())
            year = 2500 + year if year < 100 else year
        else:
            return None
        return date(year - 543 if year >= 2400 else year, month, day)
    except ValueError:
        return None


RATE_SLACK = Decimal("0.05")
DATE_SLACK_DAYS = 3


def _vat_fits(base, tax, total):
    """税额对得上税基：按税基 × 7%，或按含税价 × 7/107 倒算（含税价没读到就用税基 + 税额），差 0.05 以内都算对。"""
    gross = total if total else base + tax
    return (abs(tax - base * VAT_RATE) <= RATE_SLACK
            or abs(tax - gross * VAT_RATE / (1 + VAT_RATE)) <= RATE_SLACK)


def suspect_report_rows(reports) -> list:
    """识别读出的税报里自己对不上的行：``[(文件名, 单号, 原因), …]``。``reports`` 同 ``unverified_report_pages``。

    - 税额按税基 × 7% 和按含税价 × 7/107 倒算都差超过 0.05（含税价倒算的正常尾差 0.02 不算；税额为 0 的零税率 /
      免税行不算；压测 rM：02000076 / 077 是尾差、不是读错）；
    - 只有税额、没有税基；
    - 同一份报告里单号重复（整份都读一遍）；
    - 单号开头印着年月日（「690526-011」）而这一行的日期差了 3 天以上（一年以内才比）。先编号后开票、补录会差 1–3 天，
      不算（压测 rM：690519-007 等差 1–3 天是原单据本来如此；真读错的 690526-011 差 15 天）。
    """
    out, seen = [], {}
    for name, pages in reports:
        for _number, document in pages:
            for entry in document.get("entries") or []:
                invoice = str(entry.get("invoice_no") or "").strip()
                base, tax = dec(entry.get("subtotal")), dec(entry.get("vat"))
                if tax and not base:
                    out.append((name, invoice, ROW_VAT_ONLY))
                elif tax and base and not _vat_fits(base, tax, dec(entry.get("total"))):
                    out.append((name, invoice, ROW_VAT_RATE))
                key = invoice.upper().replace(" ", "")
                if key:
                    if key in seen and seen[key] == name:
                        out.append((name, invoice, ROW_DUPLICATE))
                    seen.setdefault(key, name)
                printed = _number_date(invoice)
                row_date = _row_date(entry.get("transaction_date_raw")) or _row_date(entry.get("transaction_date"))
                if printed and row_date and DATE_SLACK_DAYS < abs((printed - row_date).days) <= 366:
                    out.append((name, invoice, ROW_DATE))
    return out


# ── 银行页：行数明显少于原文、只断一处时按余额倒推纠正（用户 2026-10-01 同意）────────────────────────────

_MONTHS = (r"ม\.?\s?ค\.?|ก\.?\s?พ\.?|มี\.?\s?ค\.?|เม\.?\s?ย\.?|พ\.?\s?ค\.?|มิ\.?\s?ย\.?|ก\.?\s?ค\.?|ส\.?\s?ค\.?|ก\.?\s?ย\.?"
           r"|ต\.?\s?ค\.?|พ\.?\s?ย\.?|ธ\.?\s?ค\.?|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec")
_DATE = re.compile(r"(?<!\d)\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}(?!\d)"
                   r"|(?<!\d)\d{1,2}\s*(?:" + _MONTHS + r")\s*\d{2,4}(?!\d)", re.IGNORECASE)
# 读出的行比原文里带日期的行少这么多、且不到这个比例，才算「明显少读」（正常页两者几乎相等：2026-10-01 B08 8 页逐页
# 22/22、11/11、23/22、22/22、37/36、22/22；表头的期间、打印日期最多多出一两行，所以要差 3 行以上）。
SHORT_ROWS = 3
SHORT_RATIO = Decimal("0.8")
# 补读出的行最多可比原文带日期的行多到这个程度（同一天的几笔有时只印一次日期：B08 第 6 页原文 21 行带日期、实有 24 笔），
# 超出就当成凭空多出来的、不用。
RECOVER_RATIO = Decimal("1.3")


def recovered_limit(expected: int) -> int:
    return int(expected * RECOVER_RATIO) + 2


def dated_lines(transcript: str) -> int:
    """原文（Document AI 读字）里带日期的行数：银行账单每笔流水一行带日期，用来估这一页大约有几笔。"""
    return sum(1 for line in (transcript or "").splitlines() if _DATE.search(line))


def rows_short(document: dict, transcript: str) -> Optional[int]:
    """读出的行明显少于原文里带日期的行、而且页内余额链在接缝处断开：回原文的行数（再读时告诉模型），否则 None。

    漏读一张表 / 一栏的样子是：读出的行比原文带日期的行少得多，余额链大体是通的、只在漏掉的那段接缝处断几处
    （B08 第 6 页：11 / 20 行、断 3 处）。下面几种不算（2026-10-01 套生产近 4 天 222 页扫描账单：不收紧时 27 页会多读，
    只有 2 页是真的少读）：一行都没读出（无流水月的账单，表头的期间、打印日期也带日期；或根本不是账单）；链完全没断；
    断点占了一半以上（没有逐笔余额的版式、一行印记账日与起息日两个日期，日期数自然比笔数多，再读也补不出来）。"""
    expected = dated_lines(transcript)
    found = len(document.get("entries") or [])
    if not found or expected - found < SHORT_ROWS or found >= expected * SHORT_RATIO:
        return None
    breaks = bank_breaks(document)
    return expected if 1 <= breaks and breaks * 2 <= found else None


def _amount_texts(value: Decimal) -> List[str]:
    plain = f"{abs(value):.2f}"
    return [f"{abs(value):,.2f}", plain]


def repair_single_break(document: dict, transcript: str) -> Optional[int]:
    """页内余额链只断一处、断在某一笔上，且按前后余额倒推出的这笔金额原样印在本页原文里：按倒推值改这一笔，
    标成余额链补算（``chain_amount_imputed``，对账里显示为已自动更正、复核时看得到），回改的是第几笔（从 0 起）。
    其余情况（不止一处、断在结转余额上、倒推的数原文里没有）一律不改，回 None。只改金额，不动余额与其他行。"""
    if bank_breaks(document) != 1:
        return None
    entries = document.get("entries") or []
    previous = dec(document.get("opening_balance"))
    broken = None
    for index, entry in enumerate(entries):
        balance, signed = dec(entry.get("balance")), _signed(entry)
        if previous is not None and balance is not None and signed is not None \
                and abs(previous + signed - balance) > TOLERANCE:
            broken = (index, balance - previous)
            break
        if balance is not None:
            previous = balance
    if broken is None:
        return None  # 断在页上印的结转余额：不是哪一笔的金额
    index, implied = broken
    if abs(implied) < TOLERANCE or not any(text in (transcript or "") for text in _amount_texts(implied)):
        return None
    entry = entries[index]
    original = dict(entry)
    amount = f"{abs(implied):,.2f}"
    if entry.get("deposit") not in (None, "") or entry.get("withdrawal") not in (None, "") or not entry.get("amount"):
        entry["deposit"], entry["withdrawal"] = (amount, "") if implied > 0 else ("", amount)
    if entry.get("amount") not in (None, ""):
        entry["amount"], entry["direction"] = amount, "deposit" if implied > 0 else "withdrawal"
    if bank_breaks(document) != 0:
        # 改完链没有完全接上：不是这一笔的金额读错（更可能是余额读错），原样还原、不改
        entry.clear()
        entry.update(original)
        return None
    entry["chain_amount_imputed"] = True
    return index
