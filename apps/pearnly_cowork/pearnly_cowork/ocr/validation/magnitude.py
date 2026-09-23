"""Supplier magnitude baseline: a deterministic guard against scale misreads.

Source: ec2e5c957e6b ``services/ocr/magnitude_baseline.py``, moved with only its
indentation matched to this repository. The rule exists because an internally
consistent wrong read (a fuel receipt whose true total is ~1,780 read as 44.67 from
the wrong column) passes every other cheque: the only deterministic signal left is
that this supplier's history stays in the thousands.

It only flags, never rewrites a value: no baseline (too few valid history rows, a
new supplier) means no judgement, and a missing or non-positive total is left to the
other gates. The median rather than the mean keeps dirty history from moving it.

Pure logic: no Frappe, database, file, clock, HTTP, model or export access. The
deployed project never wired a history source either — its own note says the query
("recent N positive totals for this seller tax id") stayed out of that commit — so
this platform keeps the decision core and leaves the history source to a caller.
"""

from statistics import median
from typing import List, Optional

# Deviation multiple: a conservative twenty times the real case's roughly forty.
# It catches a scale error (wrong column, one extra digit) and not ordinary batch
# variation, where two- to three-fold swings are common for one supplier.
RATIO = 20.0
# Minimum history rows: below this there is no reliable baseline, so nothing is
# judged and a new supplier is never flagged.
MIN_HISTORY = 5


def magnitude_anomaly_reason(
    new_total: Optional[float],
    history_totals: List,
    *,
    min_history: int = MIN_HISTORY,
    ratio: float = RATIO,
) -> Optional[str]:
    """新票总额相对供应商历史中位数偏离 ratio 倍 → 返回原因串;否则 None。"""
    if not isinstance(new_total, (int, float)) or new_total <= 0:
        return None
    valid = [float(t) for t in (history_totals or []) if isinstance(t, (int, float)) and t > 0]
    if len(valid) < min_history:
        return None
    med = median(valid)
    if med <= 0:
        return None
    if new_total * ratio < med:
        return f"总额 {new_total} 比该供应商历史中位数 {med:.2f} 低 >{ratio:g}× — 疑选错列/读错位"
    if new_total > med * ratio:
        return f"总额 {new_total} 比该供应商历史中位数 {med:.2f} 高 >{ratio:g}× — 疑多读位/串列"
    return None
