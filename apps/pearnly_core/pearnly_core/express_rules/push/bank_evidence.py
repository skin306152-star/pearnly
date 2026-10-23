"""Pure bank evidence matching over an authorized, caller-provided snapshot."""
from __future__ import annotations
from decimal import Decimal
from typing import Any, Dict, List
from pearnly_cowork.push.express.primitives import _d, parse_invoice_date

_CENT = Decimal("0.01")


_WINDOW_DAYS = 7


_DIR_FOR_DIRECTION = {"purchase": "OUT", "sales": "IN"}


def bank_paid_match(
    bank_index: List[Dict[str, Any]], *, amount: Any, invoice_date: Any, direction: str
) -> bool:
    """票面金额(精确 2dp)+ 方向 + 日期(±7 天)在银行流水命中一笔 → True。

    只提"已付"佐证:amount/date 解析失败或无命中 → False(留给 payment_verdict 的下一级判据)。
    invoice_date 认 ISO 与 OCR 常见 DD/MM/YYYY 两种记法(见 common.parse_invoice_date)。
    """
    want_dir = _DIR_FOR_DIRECTION.get(direction)
    amt = _d(amount)
    inv_date = parse_invoice_date(invoice_date)
    if not want_dir or amt is None or inv_date is None:
        return False
    amt = amt.quantize(_CENT)
    for tx in bank_index or []:
        if (tx.get("direction") or "").strip().upper() != want_dir:
            continue
        tx_amt = _d(tx.get("amount"))
        if tx_amt is None or tx_amt.quantize(_CENT) != amt:
            continue
        tx_date = parse_invoice_date(tx.get("tx_date"))
        if tx_date is None or abs((tx_date - inv_date).days) > _WINDOW_DAYS:
            continue
        return True
    return False
