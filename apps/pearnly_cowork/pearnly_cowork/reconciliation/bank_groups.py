"""多笔对一笔的疑似合计匹配（用户 2026-09-30 定）：只提示、不当作确定匹配，行的状态和汇总都不动。

真实场景：总账逐笔记收款、银行一笔合并入账（GIZE BAY-CA-03：169 笔 RE… ↔ 03-20 一笔 2,071,955.81）；银行逐笔信用卡
扣款、总账记一笔合计（KBANK-CA-04：16 笔 = 107,403.66）；刷卡净额按日入账（B02 TTB）。

防组合爆炸：不做任意子集搜索，只看「同一天或相邻 WINDOW_DAYS 天内的全部单边行」的净额是否正好等于另一边的一笔 ——
批量入账、按日汇总都是这个形状。有多个窗口都凑得上时不配（交人看）；每行最多进一组；一组最多 MAX_ROWS 行。

纯逻辑：只读对账结果行，不打数据库、不调模型。
"""
from datetime import timedelta
from typing import List, Optional

WINDOW_DAYS = 3
MAX_ROWS = 400
TOLERANCE = 0.01

STMT_ONLY = ("stmt_deposit_only", "stmt_withdrawal_only")
GL_ONLY = ("gl_debit_only", "gl_credit_only")


def _stmt_net(row):
    return round((row.stmt_deposit or 0) - (row.stmt_withdrawal or 0), 2)


def _gl_net(row):
    # 银行存入对总账借方、银行支出对总账贷方：总账一行按「借 − 贷」和银行「存 − 取」同号比。
    return round((row.gl_debit or 0) - (row.gl_credit or 0), 2)


def _windows(day):
    """以 ``day`` 为锚、覆盖它的连续日期窗口（起, 止），从窄到宽。"""
    spans = []
    for before in range(WINDOW_DAYS + 1):
        for after in range(WINDOW_DAYS + 1 - before):
            spans.append((day - timedelta(days=before), day + timedelta(days=after)))
    return spans


def _one_side(anchors, others, anchor_net, anchor_day, other_net, other_day, used):
    groups = []
    for anchor in sorted(anchors, key=anchor_day):
        day = anchor_day(anchor)
        if day is None or id(anchor) in used:
            continue
        target = anchor_net(anchor)
        if not target:
            continue
        hits = []
        for start, end in _windows(day):
            inside = [row for row in others if id(row) not in used and other_day(row)
                      and start <= other_day(row) <= end]
            # 两种成员：只取同方向的（批量入账、信用卡合计）；或窗口里全部（刷卡净额：手续费记在反方向）。
            same = [row for row in inside if (other_net(row) > 0) == (target > 0)]
            for members in (same, inside):
                if len(members) < 2 or len(members) > MAX_ROWS:
                    continue
                if abs(round(sum(other_net(row) for row in members), 2) - target) <= TOLERANCE:
                    key = frozenset(id(row) for row in members)
                    if all(key != found[0] for found in hits):
                        hits.append((key, members, start, end))
        if len(hits) == 1:
            _key, members, start, end = hits[0]
            used.add(id(anchor))
            used.update(id(row) for row in members)
            groups.append({"anchor": anchor, "members": members, "start": start, "end": end})
    return groups


def suspected_groups(results) -> List[dict]:
    """单边行里疑似「多笔对一笔」的组：``[{side, anchor, members, start, end}]``。

    ``side`` 是 "stmt"（银行一笔 ↔ 总账多笔）或 "gl"（总账一笔 ↔ 银行多笔）。"""
    stmt = [row for row in results if row.match_status in STMT_ONLY and row.stmt_date]
    gl = [row for row in results if row.match_status in GL_ONLY and row.gl_date]
    used = set()
    first = _one_side(stmt, gl, _stmt_net, lambda row: row.stmt_date, _gl_net, lambda row: row.gl_date, used)
    second = _one_side(gl, stmt, _gl_net, lambda row: row.gl_date, _stmt_net, lambda row: row.stmt_date, used)
    return [{"side": "stmt", **group} for group in first] + [{"side": "gl", **group} for group in second]
