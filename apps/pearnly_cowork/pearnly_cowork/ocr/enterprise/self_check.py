"""逐页抽取后的自核：这一页读出来的数自己对不对得上（纯函数，不调模型、不读文件）。

2026-09-30 用户同意「漏读页自动再读一次」（每次约 0.5 泰铢），压测窗口补上税报页合计：

- 银行账单：页内余额链（页上印的承前余额 → 逐笔 ± 金额 = 逐笔余额 → 页上印的结转余额）有断点，就是这页漏读或读错了行；
- 税报：这一页的行加起来要等于页上印的合计（未税 / 税额 / 含税，印了哪个核哪个）；印的是累计到本页的合计
  （结转、最后一页的全表总计）也算对上。一个合计都没读到的页没法自核。

判定只看读出来的数，不拿页上印的合计去「补」行：对不上就交给调用方决定要不要再读、以及标出来。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional, Tuple

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
