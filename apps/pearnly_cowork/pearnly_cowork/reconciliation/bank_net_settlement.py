"""刷卡净额按总账里实际记的手续费配（2026-10-11 用户同意做，清单第 17a 项）。

银行到账的是扣了手续费的净额；总账在本账户记刷卡毛额（借方），实际手续费另记一笔贷方（Express 印在同一份明细里，
对方科目是手续费科目，B02 TTB：「CARD TTB SV RY 3,893.25」「CARD TTB SV PD 11,200.33」借方，到账当天
「ปป.CARD TTB RY 311.29」贷方，银行到账 14,782.29）。

做法：每一笔还没配上的银行存入，在它之前 WINDOW_DAYS 天内找「几笔借方 − 一笔贷方 = 这笔存入」（贷方日期在存入
当天或前一天），只收**唯一**的凑法；凑不唯一或对不上照旧单边交人看。手续费是总账真记了的，不另造「银行已扣、
总账未记」的行（那是没有手续费分录时按费率预设估的，见 `bank_card_fee`）。不需要预设：有实际分录、金额正好相等、
只有一种凑法，三条都满足才配。

纯逻辑：只读对账结果行，不打数据库、不调模型。
"""
from itertools import combinations

NET_LAYER = 6
WINDOW_DAYS = 3        # 刷卡日到银行到账最多差几天（周末 / 假日顺延）
FEE_DAYS = 1           # 手续费分录记在到账当天或前一天
MAX_SLIPS = 4          # 一笔到账最多合几笔刷卡
MAX_CANDIDATES = 14    # 窗口里单边借方超过这么多就不凑（防组合爆炸）
TOLERANCE = 0.01


def _deposits(results):
    return [row for row in results if row.match_status == "stmt_deposit_only" and row.stmt_date
            and (row.stmt_deposit or 0) > 0]


def _debits(results):
    return [row for row in results if row.match_status == "gl_debit_only" and row.gl_date and (row.gl_debit or 0) > 0]


def _credits(results):
    return [row for row in results if row.match_status == "gl_credit_only" and row.gl_date
            and (row.gl_credit or 0) > 0]


def _hits(deposit, debits, credits, used):
    day = deposit.stmt_date
    slips = [row for row in debits if id(row) not in used and 0 <= (day - row.gl_date).days <= WINDOW_DAYS]
    fees = [row for row in credits if id(row) not in used and 0 <= (day - row.gl_date).days <= FEE_DAYS]
    if not slips or not fees or len(slips) > MAX_CANDIDATES:
        return []
    found = []
    for fee in fees:
        target = round(deposit.stmt_deposit + fee.gl_credit, 2)
        for count in range(1, min(MAX_SLIPS, len(slips)) + 1):
            for combo in combinations(slips, count):
                if abs(round(sum(row.gl_debit for row in combo), 2) - target) <= TOLERANCE:
                    found.append((combo, fee))
    return found


def pairings(results):
    """配上的（到账行, 刷卡行们, 手续费行）：每笔到账只收唯一的凑法，每行只进一组。"""
    debits, credits = _debits(results), _credits(results)
    used, out = set(), []
    for deposit in sorted(_deposits(results), key=lambda row: row.stmt_date):
        hits = _hits(deposit, debits, credits, used)
        if len(hits) != 1:
            continue
        combo, fee = hits[0]
        used.update(id(row) for row in (*combo, fee))
        out.append((deposit, combo, fee))
    return out


def accept(results) -> tuple:
    """把唯一解的组当作匹配（第 6 层「刷卡净额（按总账手续费）」，组号接在已有组号后面）。回 ``(行, 组数)``。"""
    found = pairings(results)
    if not found:
        return results, 0
    start = max((row.group_no or 0 for row in results), default=0)
    placed, grouped = set(), []
    for number, (deposit, combo, fee) in enumerate(found, start + 1):
        for row in (deposit, *combo, fee):
            row.match_status, row.match_layer, row.group_no, row.date_diff_days = "matched", NET_LAYER, number, None
            grouped.append(row)
            placed.add(id(row))
    matched = [row for row in results if row.match_status == "matched" and id(row) not in placed]
    rest = [row for row in results if row.match_status != "matched" and id(row) not in placed]
    return matched + grouped + rest, len(found)
