"""公司规则（数字会计员第 2 期「统一客户规则」，设计见 docs/work/2026-10-16-customer-rules-phase2.md）：一家公司全所共用的
入账选择怎么与账套记忆、Express 历史叠在一起。纯函数，不连库、不依赖框架；push（推送准备）与 companies（存规则时校验）都导入它，
不放 contracts（策略不许 contracts 导入 push）。

优先级（最具体的说了算）：本票人工裁决 / 票面写明 > 账套记忆（人定，某份账套）> 公司规则（分店的 > 全公司的）
> Express 历史已定 > 名称匹配。前两档与历史、名称匹配由 `choices.resolve` 与推送准备照旧判，这里只做「公司规则垫在账套记忆之下」：

- `overlay`：账套记过的（值还在这份账套主档里）说了算；没记过、或记的值已不在主档里的，用公司规则补上（值须在这份账套主档里，
  主档没报上来时放行，到小助手写入时再判）。
- `valid`：一个值能不能用在这份账套（`config` 是推送准备的账套配置，带小助手上报的 `reported_*` 主档）。
- `decided_by` / `finish`：这张单每一类「按什么定的」（`task_default` 任务默认、`remembered` 账套记忆、`company_rule` 公司规则、
  `express_history` Express 历史；同一类几项来源不同记 `mixed`），付款方式由公司规则定时载荷的 `doctype_src` 记成 `company_rule`。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from pearnly_cowork.push.express.choices import KINDS, NEW, PAYMENT_VALUES, VAT_VALUES

SOURCES = ("task_default", "remembered", "company_rule", "express_history")  # 与 `pushed.ACCOUNT_FROM` 同一套词
# 每一类的值来自这份账套的哪张主档（付款、抵进项是固定值）
MASTERS = {"item": "reported_products", "account": "reported_accounts", "receipt": "reported_accounts",
           "department": "reported_departments"}
VALUE_MAX = 60


def branch_of(value: Any) -> str:
    """规则的范围：空 = 全公司；否则 5 位分店号（`00000` 总公司）。认不出的形状回 None。"""
    text = str(value or "").strip()
    if not text:
        return ""
    return text if re.fullmatch(r"\d{5}", text) else None


def _codes(rows: Any) -> Optional[set]:
    """主档里的编码；没报上来（不是列表或空表，同 `choices.resolve` 的「不拦」）回 None。"""
    if not isinstance(rows, list) or not rows:
        return None
    return {str(row.get("code")).strip() for row in rows
            if isinstance(row, Mapping) and str(row.get("code") or "").strip()}


def _masters(kind: str, subject: str, config: Mapping[str, Any]) -> Optional[set]:
    if kind == "party":
        side = "reported_customers" if str(subject or "").startswith("sales|") else "reported_suppliers"
        return _codes(config.get(side))
    if kind == "item":
        rows = config.get("reported_products")
        rows = [row for row in rows if isinstance(row, Mapping) and str(row.get("kind") or "stock") == "stock"] \
            if isinstance(rows, list) else rows
        return _codes(rows)
    return _codes(config.get(MASTERS.get(kind, "")))


def valid(kind: str, value: Any, config: Optional[Mapping[str, Any]], subject: str = "") -> bool:
    """`value` 能不能用在这份账套：付款 / 抵进项是固定值；对方档、存货可以是「新建」；其余要在这份账套的主档里，
    主档没报上来（`config` 为 None 或那张表没报）时放行。"""
    value = str(value or "").strip()
    if kind not in KINDS or not value or len(value) > VALUE_MAX:
        return False
    if kind == "payment":
        return value in PAYMENT_VALUES
    if kind == "vat":
        return value in VAT_VALUES
    if value == NEW:
        return kind in ("party", "item")
    codes = _masters(kind, subject, config or {})
    return codes is None or value in codes


def pick(rules: Iterable[Mapping[str, Any]], branch: str = "") -> Dict[str, Dict[str, Mapping[str, Any]]]:
    """这份账套（分店 `branch`）适用的规则：`{kind: {subject: 规则}}`；同一类同一归类键分店的比全公司的具体，分店的说了算。
    别的分店的规则不用。"""
    branch = branch_of(branch) or ""
    found: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    for rule in rules or ():
        if not isinstance(rule, Mapping):
            continue
        kind, subject, scope = str(rule.get("kind") or ""), str(rule.get("subject") or ""), branch_of(rule.get("branch"))
        if kind not in KINDS or not subject or scope is None or (scope and scope != branch):
            continue
        current = found.setdefault(kind, {}).get(subject)
        if current is None or (scope and not branch_of(current.get("branch"))):
            found[kind][subject] = rule
    return found


def overlay(remembered: Mapping[str, Mapping[str, str]], rules: Iterable[Mapping[str, Any]], branch: str = "",
            config: Optional[Mapping[str, Any]] = None) -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]]]:
    """账套记忆之下垫一层公司规则 → `(合并后的 {kind: {subject: value}}, 来源 {kind: {subject: source}})`。

    账套里记过、值还能用的照用（`remembered`）；没记过或记的值已不在这份账套主档里的，用适用的公司规则（`pick`，值须 `valid`）。
    `config` 为 None 时不按主档核（只按形状）。不改入参。"""
    merged = {kind: dict(values or {}) for kind, values in (remembered or {}).items()}
    sources = {kind: {subject: "remembered" for subject in values} for kind, values in merged.items()}
    for kind, subjects in pick(rules, branch).items():
        for subject, rule in subjects.items():
            mine = str(merged.get(kind, {}).get(subject) or "").strip()
            if mine and valid(kind, mine, config, subject):
                continue  # 账套记忆（人定，某份账套）说了算
            value = str(rule.get("value") or "").strip()
            if not valid(kind, value, config, subject):
                continue
            merged.setdefault(kind, {})[subject] = value
            sources.setdefault(kind, {})[subject] = "company_rule"
    return merged, sources


def decided_by(trace: Mapping[str, List[Optional[str]]], sources: Mapping[str, Mapping[str, str]]) -> Dict[str, str]:
    """`choices.resolve(trace=…)` 记下的「每一类用了哪几个归类键」（None = 任务默认）→ `{kind: source}`。"""
    out = {}
    for kind, used in (trace or {}).items():
        found = {"task_default" if subject is None else (sources.get(kind) or {}).get(subject, "remembered")
                 for subject in used or ()}
        if found:
            out[kind] = found.pop() if len(found) == 1 else "mixed"
    return out


def finish(decision: Dict[str, Any], trace: Mapping[str, List[Optional[str]]],
           sources: Mapping[str, Mapping[str, str]]) -> Dict[str, Any]:
    """把「按什么定的」放进 `decision["decided_by"]`；付款方式由公司规则定时，`applied["payment_from"]` 记成 `company_rule`
    （写进载荷 `doctype_src`，CC 推送结果据此说清，同验收 F19）。就地改并回 `decision`。"""
    found = decided_by(trace, sources)
    if found:
        decision["decided_by"] = found
    applied = decision.get("applied") or {}
    if found.get("payment") == "company_rule" and applied.get("payment_from") == "remembered":
        applied["payment_from"] = "company_rule"
    return decision


__all__ = ["MASTERS", "SOURCES", "branch_of", "decided_by", "finish", "overlay", "pick", "valid"]
