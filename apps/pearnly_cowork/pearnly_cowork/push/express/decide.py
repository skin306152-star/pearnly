"""一张票在一份 Express 账套里的选择怎么定（推送准备 `push.application.service._decisions` 的判定核心，数字会计员第 3 期
第 11 条抽出）。纯函数，不连库、不依赖框架：推送服务与离线回放（`scripts/da-replay.py`）共用同一套判定。

取数（账套记忆、公司规则、Express 历史、同公司别年度借来的 POS 收款科目）由调用方做好传进来：
- ``remembered``：这份账套记过的 ``{kind: {subject: value}}``（`push.choices.remembered`），这里先垫上公司规则（`company_choices.overlay`）；
- ``rules`` / ``branch``：这家公司的规则与这份账套的分店（第 2 期）；``rules_read`` 为假（取规则出错）时这一张不用 Express 历史已定；
- ``borrow(remembered, receipts)``：就地借同公司别年度记过的 POS 收款科目（只用于这一次判断）；
- ``settle(needed)``：要人选费用科目时 Express 历史很一致的科目 ``{code, docs, ...}`` 或 None。

结果同 `choices.resolve` 的 ``{"applied", "needed"}``，另带 ``decided_by``（每一类按什么定的）与历史已定时的 ``history``；
第二个返回值是各值的来源 ``{kind: {subject: source}}``（推送服务据此细分费用科目的 ``account_from``）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Tuple

from pearnly_cowork.push.express import choices as choice_rules
from pearnly_cowork.push.express import company_choices


def decide(fields: Mapping[str, Any], *, direction: str, posting_kind: Optional[str], config: Mapping[str, Any],
           remembered: Mapping[str, Mapping[str, str]], rules: Iterable[Mapping[str, Any]] = (), branch: str = "",
           rules_read: bool = True, account_resolved: bool = False, fixed: Optional[Mapping[str, str]] = None,
           receipts: Iterable = (), borrow: Optional[Callable] = None,
           settle: Optional[Callable] = None) -> Tuple[Dict[str, Any], Dict[str, Dict[str, str]]]:
    """→ ``(decision, sources)``（见文件头）。``posting_kind`` 是已解析的入账类型（`contracts.push.resolve_posting_kind`）。"""
    remembered, sources = company_choices.overlay(remembered, rules, branch, config)
    # 科目表没读到而先没用的公司规则：这一项不拿 Express 历史顶替，照常要选、等电脑读科目表（`company_choices.held_back`）
    held = company_choices.held_back(remembered, rules, branch, config)
    if borrow is not None:
        borrow(remembered, receipts)
    trace = {}
    decision = choice_rules.resolve(fields, direction=direction, posting_kind=posting_kind, config=config,
                                    remembered=remembered, account_resolved=account_resolved, fixed=fixed,
                                    receipts=receipts, trace=trace)
    waiting = any(item.get("kind") == "account" and item.get("subject") in held.get("account", ())
                  for item in decision["needed"])
    settled = settle(decision["needed"]) if settle is not None and rules_read and not waiting else None
    if settled:
        # 历史很一致：当作这家已记住的科目用（只这一次判断，不写进账套记忆；主档页可见可删）
        subject = next(item["subject"] for item in decision["needed"] if item.get("kind") == "account")
        remembered = {**remembered, "account": {**(remembered.get("account") or {}), subject: settled["code"]}}
        sources = {**sources, "account": {**(sources.get("account") or {}), subject: "express_history"}}
        trace = {}
        decision = choice_rules.resolve(fields, direction=direction, posting_kind=posting_kind, config=config,
                                        remembered=remembered, account_resolved=account_resolved, fixed=fixed,
                                        receipts=receipts, trace=trace)
        decision["history"] = {"account": settled["code"], "docs": settled["docs"]}
        if settled.get("version"):
            # 汇总的版本（CC 桌面版 `account_history.RESULT_VERSION`）：只有排除了 Pearnly 自己写的行（v2）的「已定」才算
            # 规则定（`automation.tiers`），v1 的最高算 CC 判断（第 3 期第 1、11 条）
            decision["history"]["version"] = settled["version"]
        if settled.get("read"):
            decision["history"]["read"] = str(settled["read"])[:10]  # 读的日子：各层说法不一只比近 31 天内读的
    company_choices.finish(decision, trace, sources)
    return decision, sources


__all__ = ["decide"]
