"""公司自动推送档位的证据与升降（数字会计员第 3 期第 7、8 条）。纯函数，不连库、不依赖框架：`companies.automation` 取数后调这里，
离线回放（`scripts/da-replay.py`）用同一套比三套阈值。

- **完整月 = 单据所属会计期间**（不看墙上时间）。**合格回读**：这一期的回读在这期 ภ.พ.30 截止日之后发起才算（会计通常报税前才在
  Express 里改账，早回读会把「被改」量低）。
- **样本只算被合格回读覆盖的单**：提议（人推的）与自动推的单。**被改**：合格回读里 changed / deleted / voided / missing；或人推前
  改过票面 / 另选了某一项；或推后核对 mismatch。提议重判出的值因规则 / 记忆变化（来源不是人）而不同的记「过时」，不进样本。
- **阈值**（`THRESHOLDS`，所有租户同一套，用户 10-16 定建议 A）：
  - L0 → L1：最近一个完整期，合格回读覆盖的 A 类 ≥20 张、被改 ≤1%（向下取整，不到 100 张即 0 张）；
  - L1 → L2：在 L1 满 2 个完整期，自动推被改 ≤0.5%；B 类提议每期 ≥20 张、被改 ≤1%；另要 L2 推 B 类的全局前提；
  - 降一档：一期内自动推被改 ≥2 张且 >1%；自动推的单核不上立刻降到 L0（`companies.automation.on_mismatch`）。
- **一次升一档**；降档（或升档）后只认之后开始的完整会计期间。
- **定时任务 A 类的基线**（`baseline`，用户 10-16「新客户第一个月照推 A 类」）：L0 上从没升降过、也还没有合格回读的是新客户，
  照推；之后按最近一个完整期的证据：A 类被改不超过 1%（同 L0 → L1 的比例）、没有核不上才推。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from pearnly_cowork.push.automation import rules as tier_rules

L0, L1, L2 = tier_rules.L0, tier_rules.L1, tier_rules.L2
CHANGES = ("changed", "deleted", "voided", "missing")
COUNTS = ("a", "a_changed", "b", "b_changed", "auto", "auto_changed", "mismatch", "stale")
THRESHOLDS = {
    # L0 → L1：最近 1 个完整期，A 类 ≥20 张、被改 ≤1%
    "up_l1": {"periods": 1, "a_min": 20, "a_rate": "0.01"},
    # L1 → L2：在 L1 满 2 个完整期，自动推被改 ≤0.5%；B 类提议每期 ≥20 张、被改 ≤1%
    "up_l2": {"periods": 2, "auto_rate": "0.005", "b_min": 20, "b_rate": "0.01"},
    # 降一档：一期内自动推被改 ≥2 张且 >1%
    "down": {"auto_changed_min": 2, "auto_rate": "0.01"},
}
HISTORY_MAX = 24


def allowed(count: int, rate: str) -> int:
    """被改最多几张（``count`` × ``rate`` 向下取整：不到 100 张、1% 即 0 张）。整数运算，不用浮点。"""
    numerator, _, decimals = rate.partition(".")
    scale = 10 ** len(decimals)
    return (int(count) * int(numerator + decimals)) // scale


def _exceeds(changed: int, count: int, rate: str) -> bool:
    """被改比例严格大于 ``rate``。"""
    numerator, _, decimals = rate.partition(".")
    scale = 10 ** len(decimals)
    return int(changed) * scale > int(count) * int(numerator + decimals)


def qualified(requested_at: Optional[dt.date], deadline: Optional[dt.date]) -> bool:
    """这一期的回读在这期 ภ.พ.30 截止日之后（当天不算）发起才算合格。"""
    return bool(requested_at and deadline) and requested_at > deadline


def tally(documents: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """一家一期的统计。每张单：``automated``（自动推的）/ ``proposal``（有提议、人推的）、``cls``（A / B）、``covered``（被这期
    合格回读覆盖）、``readback``（回读里的变化：changed / deleted / voided / missing 或空）、``verify``（推后核对 ok / mismatch /
    unverified）、``outcome``（人推提议时与提议比的结果：``edited`` / ``changed`` / ``stale``，`push.automation.rules.compare`）。

    → ``{a, a_changed, b, b_changed, auto, auto_changed, mismatch, stale}``：a / b 是 A / B 类样本（自动推的与人推的提议都算，
    定时任务在 L0 自动推的 A 类就是 A 类的证据），auto 是其中自动推的；mismatch 是核不上的自动推（不论有没有回读覆盖）；stale 是
    因规则 / 记忆变化而与提议不同、不进样本的。"""
    out = {key: 0 for key in COUNTS}
    for doc in documents or ():
        automated = bool(doc.get("automated"))
        if automated and doc.get("verify") == "mismatch":
            out["mismatch"] += 1
        cls = doc.get("cls")
        if cls not in ("A", "B") or not (automated or doc.get("proposal")):
            continue
        outcome = doc.get("outcome") if isinstance(doc.get("outcome"), Mapping) else {}
        human = bool(outcome.get("edited") or outcome.get("changed"))
        if not automated and outcome.get("stale") and not human:
            out["stale"] += 1
            continue
        if not doc.get("covered"):
            continue
        changed = (doc.get("readback") in CHANGES or doc.get("verify") == "mismatch" or human)
        key = "a" if cls == "A" else "b"
        out[key] += 1
        out[key + "_changed"] += int(changed)
        if automated:
            out["auto"] += 1
            out["auto_changed"] += int(changed)
    return out


def _after(period: str, since: Optional[dt.date]) -> bool:
    """这一期在 ``since``（上次升降档的日子）之后才开始（第一天晚于它）；从没升降过的都算。"""
    if since is None:
        return True
    return dt.date(int(period[:4]), int(period[5:7]), 1) > since


def evaluate(tier: str, since: Optional[dt.date], periods: Mapping[str, Mapping[str, int]],
             *, thresholds: Mapping[str, Mapping[str, Any]] = THRESHOLDS,
             b_at_l2: bool = tier_rules.B_AT_L2) -> Tuple[str, str]:
    """按合格回读覆盖的完整期统计（``periods`` ``{YYYY-MM: tally}``，只给有合格回读的期）定档：回 ``(新档, 原因码)``；不变回
    ``(原档, "")``。一次升一档；降一档优先于升。只认 ``since`` 之后开始的期。"""
    tier = tier_rules.tier_of(tier)
    eligible = sorted(period for period in periods if _after(period, since))
    if not eligible:
        return tier, ""
    latest = periods[eligible[-1]]
    down = thresholds["down"]
    if tier != L0 and latest.get("auto", 0) and latest.get("auto_changed", 0) >= down["auto_changed_min"] \
            and _exceeds(latest["auto_changed"], latest["auto"], down["auto_rate"]):
        return (L1 if tier == L2 else L0), "changed"
    if tier == L0:
        rule = thresholds["up_l1"]
        recent = [periods[period] for period in eligible[-rule["periods"]:]]
        if len(recent) == rule["periods"] and all(
                stats.get("a", 0) >= rule["a_min"]
                and stats.get("a_changed", 0) <= allowed(stats.get("a", 0), rule["a_rate"]) for stats in recent):
            return L1, "readback"
    elif tier == L1 and b_at_l2:
        rule = thresholds["up_l2"]
        recent = [periods[period] for period in eligible[-rule["periods"]:]]
        auto = sum(stats.get("auto", 0) for stats in recent)
        auto_changed = sum(stats.get("auto_changed", 0) for stats in recent)
        if len(recent) == rule["periods"] and auto_changed <= allowed(auto, rule["auto_rate"]) and all(
                stats.get("b", 0) >= rule["b_min"]
                and stats.get("b_changed", 0) <= allowed(stats.get("b", 0), rule["b_rate"]) for stats in recent):
            return L2, "readback"
    return tier, ""


def baseline(tier: str, since: Optional[dt.date], periods: Mapping[str, Mapping[str, int]], reason: str = "",
             *, thresholds: Mapping[str, Mapping[str, Any]] = THRESHOLDS) -> str:
    """定时任务 A 类在 L0 的基线推送（`push.automation.rules.action` 的 ``baseline``）：``new``（新客户：从没升降过、没有合格
    回读的期）/ ``ok``（最近一个完整期 A 类被改 ≤ ``up_l1.a_rate``、没有核不上）/ 不推的原因码（``changed`` / ``written_mismatch``；
    升降过档、之后还没有完整期证据的回上次的原因）。L1 / L2 不用基线，回 ``ok``。"""
    if tier_rules.tier_of(tier) != L0:
        return tier_rules.BASELINE_OK
    eligible = sorted(period for period in periods if _after(period, since))
    if not eligible:
        return tier_rules.BASELINE_NEW if since is None else (reason if reason in ("changed", "written_mismatch")
                                                               else "changed")
    latest = periods[eligible[-1]]
    if latest.get("mismatch", 0):
        return "written_mismatch"
    if latest.get("a_changed", 0) > allowed(latest.get("a", 0), thresholds["up_l1"]["a_rate"]):
        return "changed"
    return tier_rules.BASELINE_OK


def history_entry(before: str, after: str, reason: str, at: Any, period: str = "") -> Dict[str, str]:
    return {"from": before, "to": after, "reason": reason, "at": str(at or ""), "period": period}


def keep_history(history: Sequence[Mapping[str, Any]], entry: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """最近 ``HISTORY_MAX`` 次升降（新的在后）。"""
    rows = [dict(item) for item in history or () if isinstance(item, Mapping)]
    rows.append(dict(entry))
    return rows[-HISTORY_MAX:]


__all__ = ["CHANGES", "COUNTS", "HISTORY_MAX", "THRESHOLDS", "allowed", "baseline", "evaluate", "history_entry", "keep_history",
           "qualified", "tally"]
