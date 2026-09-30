"""刷卡净额入账（对账预设，用户 2026-09-30 定）：银行到账的是扣了刷卡手续费的净额，总账记的是刷卡毛额，晚一两天到账。

问一次这个账户的手续费率（结果页按数据推一个费率让人确认），以后按费率把「银行一笔净额 ↔ 总账当天一到三笔刷卡毛额」配上
（第 5 层「刷卡净额」），扣掉的手续费单列一行「银行已扣、总账未记」，对账公式照样平。

压测 B02（TTB，2026-05）：手续费 = 每笔刷卡额 × 1.95%，另加 7% 增值税，按笔四舍五入；到账晚 1 天；同一天一到两笔合并入账。
不硬套：只配窗口内唯一的凑法；有增值税 / 无增值税两种算法都试（有的合约报价已含税）；凑不唯一或对不上的照旧单边，交人看。

纯逻辑：只读对账结果行，不打数据库、不调模型。
"""
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import List, Optional, Tuple

CARD_FEE_LAYER = 5
VAT = 1.07
WINDOW_DAYS = 3          # 总账刷卡日到银行到账最多差几天（周末 / 假日顺延）
MAX_SLIPS = 3            # 一笔到账最多合几笔刷卡（同一刷卡日）
BUSY_DAY = 12            # 一天刷卡超过这么多笔只按单笔配（防组合爆炸）
SLIP_TOLERANCE = 0.05    # 每笔手续费按笔四舍五入的尾差
MAX_RATE = 10.0          # 费率（%）上限
SCAN_RATES = [round(0.5 + step * 0.05, 2) for step in range(61)]  # 0.50%–3.50%，推荐费率时逐档试


def parse_rate(value) -> Optional[float]:
    """预设里存的费率（百分数文字，如 "1.95"）→ 1.95；不是合法费率回 None。"""
    try:
        rate = float(Decimal(str(value).strip().rstrip("%")))
    except (InvalidOperation, ValueError):
        return None
    return rate if 0 < rate <= MAX_RATE else None


def fee(gross: float, rate_percent: float, vat: bool) -> float:
    base = round(gross * rate_percent / 100, 2)
    return round(base * VAT, 2) if vat else base


def _stmt_only(results):
    return [row for row in results if row.match_status == "stmt_deposit_only" and row.stmt_date and row.stmt_deposit > 0]


def _gl_only(results):
    return [row for row in results if row.match_status == "gl_debit_only" and row.gl_date and row.gl_debit > 0]


def _hits(stmt, slips, rate_percent, used):
    """这笔到账在窗口内所有凑得上的（刷卡组合, 含不含增值税）：只合同一刷卡日的一到三笔。"""
    by_day = {}
    for slip in slips:
        if id(slip) not in used and 0 <= (stmt.stmt_date - slip.gl_date).days <= WINDOW_DAYS:
            by_day.setdefault(slip.gl_date, []).append(slip)
    found = []
    for day_slips in by_day.values():
        for count in range(1, min(MAX_SLIPS, len(day_slips)) + 1):
            if count > 1 and len(day_slips) > BUSY_DAY:
                break
            for combo in combinations(day_slips, count):
                gross = round(sum(slip.gl_debit for slip in combo), 2)
                for vat in (True, False):
                    charged = round(sum(fee(slip.gl_debit, rate_percent, vat) for slip in combo), 2)
                    if abs(round(gross - charged, 2) - stmt.stmt_deposit) <= SLIP_TOLERANCE * count:
                        found.append((combo, vat))
                        break
    return found


def pairings(results, rate_percent) -> List[tuple]:
    """按费率配上的（到账行, 刷卡行们, 含不含增值税）：每笔到账只收唯一的凑法，每笔刷卡只进一组。"""
    stmts, slips = _stmt_only(results), _gl_only(results)
    used, out = set(), []
    for stmt in sorted(stmts, key=lambda row: row.stmt_date):
        hits = _hits(stmt, slips, rate_percent, used)
        if len(hits) != 1:
            continue
        combo, vat = hits[0]
        used.update(id(slip) for slip in combo)
        out.append((stmt, combo, vat))
    return out


def suggest(results) -> Optional[Tuple[float, int, list]]:
    """按数据推一个费率：0.50%–3.50% 逐档试，能配上最多笔到账的那一档（至少 2 笔）。回 ``(费率, 笔数, 配对)``。"""
    if not _stmt_only(results) or not _gl_only(results):
        return None
    best = None
    for rate in SCAN_RATES:
        found = pairings(results, rate)
        if len(found) >= 2 and (best is None or len(found) > best[1]):
            best = (rate, len(found), found)
    return best


def accept(results, rate_value, fee_label) -> tuple:
    """这个账户已预设刷卡手续费率：配上的到账与刷卡当作匹配（第 5 层，组号接在已有组号后面），每组另加一行
    「银行已扣、总账未记」的手续费（``fee_label`` 是这一行的说明）。回 ``(行, 组数)``。"""
    rate = parse_rate(rate_value)
    if rate is None:
        return results, 0
    found = pairings(results, rate)
    if not found:
        return results, 0
    start = max((row.group_no or 0 for row in results), default=0)
    placed, grouped = set(), []
    for number, (stmt, combo, _vat) in enumerate(found, start + 1):
        for row in (stmt, *combo):
            row.match_status, row.match_layer, row.group_no, row.date_diff_days = "matched", CARD_FEE_LAYER, number, None
            grouped.append(row)
            placed.add(id(row))
        charged = round(sum(slip.gl_debit for slip in combo) - stmt.stmt_deposit, 2)
        if charged > 0:
            grouped.append(replace(stmt, match_status="stmt_withdrawal_only", match_layer=None, stmt_desc=fee_label,
                                   stmt_deposit=0.0, stmt_withdrawal=charged, stmt_balance=None, group_no=number,
                                   stmt_autocorrected=False))
    matched = [row for row in results if row.match_status == "matched" and id(row) not in placed]
    rest = [row for row in results if row.match_status != "matched" and id(row) not in placed]
    return matched + grouped + rest, len(found)
