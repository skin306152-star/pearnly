"""对比报告（多产品共用，评估 §11）：同一批单据，代录 / 现有直写 / 会计手录三种方式并排。无框架、无 IO。

岳父 10-26：做出来和现有方式比较，他找一家公司来试。第 0 期验收必出，试用时直接出；先在 69EXP\\test 用试点语料出样张
（会计手录一列用标准答案代替）。

每种方式交一份 ``Run``：每张单的用时、准确（读回逐字段与标准答案一致）、要不要人、原因、作废与空单头、费用。
``compare`` 回一张表：行是指标（前端按代码出文案），列是方式；缺的数据给 None（显示「—」），不估、不补。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Sequence

METHODS = ("operator", "direct", "manual")
METRICS = ("docs", "docs_by_type", "seconds_total", "seconds_per_doc", "accurate", "accuracy", "human",
           "human_by_reason", "voided", "placeholders", "cost_thb", "cost_per_doc_thb", "machine_hours")


@dataclass
class DocResult:
    doc_id: str
    doc_type: str
    seconds: Optional[float] = None  # 这张从开始到存好的用时；手录取会计自报或计时
    accurate: Optional[bool] = None  # 读回逐字段与标准答案一致；手录作基准时为 None
    human: bool = False  # 必须人工（挂起）
    reason: str = ""
    voided: int = 0
    placeholders: int = 0
    cost_thb: Optional[Decimal] = None  # 识别费 + AI 接手费；手录 = 人工时 × 时薪（试用公司给）


@dataclass
class Run:
    method: str
    docs: List[DocResult] = field(default_factory=list)
    machine_hours: Optional[float] = None  # 电脑占用（代录）；直写、手录不填
    baseline: bool = False  # 这一列是标准答案本身（会计手录作基准）


def _q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places), ROUND_HALF_UP)


def summarize(run: Run) -> Dict:
    docs = run.docs
    n = len(docs)
    by_type: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    for d in docs:
        by_type[d.doc_type] = by_type.get(d.doc_type, 0) + 1
        if d.human:
            by_reason[d.reason or "unknown_screen"] = by_reason.get(d.reason or "unknown_screen", 0) + 1
    timed = [d.seconds for d in docs if d.seconds is not None]
    seconds = round(sum(timed), 1) if timed and len(timed) == n else None
    judged = [d.accurate for d in docs if d.accurate is not None]
    accurate = sum(1 for a in judged if a) if judged and not run.baseline else None
    costs = [d.cost_thb for d in docs if d.cost_thb is not None]
    cost = _q(sum(costs, Decimal(0))) if costs and len(costs) == n else None
    return {
        "docs": n,
        "docs_by_type": dict(sorted(by_type.items())),
        "seconds_total": seconds,
        "seconds_per_doc": round(seconds / n, 1) if seconds is not None and n else None,
        "accurate": accurate,
        "accuracy": (_q(Decimal(accurate) * 100 / len(judged), "0.1") if accurate is not None else None),
        "human": sum(1 for d in docs if d.human) if run.method != "manual" else None,
        "human_by_reason": dict(sorted(by_reason.items())) if run.method != "manual" else None,
        "voided": sum(d.voided for d in docs) if run.method != "direct" else None,
        "placeholders": sum(d.placeholders for d in docs) if run.method == "operator" else None,
        "cost_thb": cost,
        "cost_per_doc_thb": _q(cost / n) if cost is not None and n else None,
        "machine_hours": run.machine_hours if run.method == "operator" else None,
    }


def compare(runs: Sequence[Run]) -> Dict:
    """回 ``{"methods": [...], "rows": [{"metric", "values": {method: 值}}], "same_docs": bool}``。

    ``same_docs``：三种方式是不是同一批单据（单号集合一样）；不是的话报告页要标出来，不能硬比。"""
    by_method = {r.method: r for r in runs if r.method in METHODS}
    methods = [m for m in METHODS if m in by_method]
    summaries = {m: summarize(by_method[m]) for m in methods}
    ids = [frozenset(d.doc_id for d in by_method[m].docs) for m in methods]
    return {"methods": methods,
            "rows": [{"metric": metric, "values": {m: summaries[m][metric] for m in methods}} for metric in METRICS],
            "same_docs": len(set(ids)) <= 1}
