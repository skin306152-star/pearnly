"""公司规则（数字会计员第 2 期「统一客户规则」，设计见 docs/work/2026-10-16-customer-rules-phase2.md）：一家公司全所共用的
入账选择怎么与账套记忆、Express 历史叠在一起。纯函数，不连库、不依赖框架；push（推送准备）与 companies（存规则时校验）都导入它，
不放 contracts（策略不许 contracts 导入 push）。

优先级（最具体的说了算）：本票人工裁决 / 票面写明 > 账套记忆（人定，某份账套）> 公司规则（分店的 > 全公司的）
> Express 历史已定 > 名称匹配。前两档与历史、名称匹配由 `choices.resolve` 与推送准备照旧判，这里只做「公司规则垫在账套记忆之下」：

- `overlay`：账套记过的（值还在这份账套主档里）说了算；没记过、或记的值已不在主档里的，用公司规则补上（值须在这份账套主档里）。
  主档没报上来时：费用科目、收款科目（`WAIT_KINDS`，靠科目表）先不用公司规则，这一项照常列为要选，推送按现有规则请电脑读科目表、
  读到后再推（同定时任务跟到别的年度账套时的任务默认 `folder_intake.follow.fixed_in`：公司规则覆盖全公司各年度，新年度科目表
  可能改过号）；其余各类放行，到 CC 桌面版写入时再判。
- `valid`：一个值能不能用在这份账套（`config` 是推送准备的账套配置，带 CC 桌面版上报的 `reported_*` 主档）。
- `decided_by` / `finish`：这张单每一类「按什么定的」（`task_default` 任务默认、`remembered` 账套记忆、`company_rule` 公司规则、
  `express_history` Express 历史；同一类几项来源不同记 `mixed`），付款方式由公司规则定时载荷的 `doctype_src` 记成 `company_rule`。
- `used`：这张单实际用上的公司规则是哪几条（规则编号、对象名、值、范围），推送准备冻结进目标（`company_rules`），CC 说「按哪条
  公司规则」时照这里说，不去说明里的 `<client_choices>` 猜（2026-10-16 测试：SD-6909-1010 按一条已删的สะอาดดี规则入账，
  CC 却说成说明里仅剩的ซ่อมไว那条）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from pearnly_core.express_rules.push.choices import KINDS, NEW, PAYMENT_VALUES, VAT_VALUES

SOURCES = ("task_default", "remembered", "company_rule", "express_history")  # 与 `pushed.ACCOUNT_FROM` 同一套词
# 每一类的值来自这份账套的哪张主档（付款、抵进项是固定值）
MASTERS = {"item": "reported_products", "account": "reported_accounts", "receipt": "reported_accounts",
           "department": "reported_departments"}
VALUE_MAX = 60
# 靠科目表的几类（同推送准备的 `CHART_KINDS`，要选时会等电脑读科目表）：主档没报上来时不套用公司规则
WAIT_KINDS = ("account", "receipt")


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

    账套里记过、值还能用的照用（`remembered`）；没记过或记的值已不在这份账套主档里的，用适用的公司规则（`pick`，值须 `valid`；
    `WAIT_KINDS` 这份账套的科目表还没报上来时先不用，见文件头）。`config` 为 None 时不按主档核（只按形状）。不改入参。"""
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
            if config is not None and kind in WAIT_KINDS and _masters(kind, subject, config) is None:
                continue  # 科目表没读到：先不用，要选时推送请电脑读、读到后再推
            merged.setdefault(kind, {})[subject] = value
            sources.setdefault(kind, {})[subject] = "company_rule"
    return merged, sources


def held_back(merged: Mapping[str, Mapping[str, str]], rules: Iterable[Mapping[str, Any]], branch: str = "",
              config: Optional[Mapping[str, Any]] = None) -> Dict[str, set]:
    """`overlay` 因这份账套科目表没读到而先没用的公司规则：`{kind: {subject}}`（`merged` 是 `overlay` 合并后的结果）。
    推送准备据此不拿 Express 历史顶替（公司规则比历史具体），这一项照常要选、等电脑读科目表。`config` 为 None 回空。"""
    if config is None:
        return {}
    found: Dict[str, set] = {}
    for kind, subjects in pick(rules, branch).items():
        if kind not in WAIT_KINDS or _masters(kind, "", config) is not None:
            continue
        for subject, rule in subjects.items():
            if str((merged.get(kind) or {}).get(subject) or "").strip():
                continue
            if valid(kind, str(rule.get("value") or "").strip(), config, subject):
                found.setdefault(kind, set()).add(subject)
    return found


def decided_by(trace: Mapping[str, List[Optional[str]]], sources: Mapping[str, Mapping[str, str]]) -> Dict[str, str]:
    """`choices.resolve(trace=…)` 记下的「每一类用了哪几个归类键」（None = 任务默认）→ `{kind: source}`。"""
    out = {}
    for kind, used in (trace or {}).items():
        found = {"task_default" if subject is None else (sources.get(kind) or {}).get(subject, "remembered")
                 for subject in used or ()}
        if found:
            out[kind] = found.pop() if len(found) == 1 else "mixed"
    return out


USED_MAX = 10  # 一张单冻结的公司规则最多这么多条（存货逐行可能多条）


def used(trace: Mapping[str, List[Optional[str]]], sources: Mapping[str, Mapping[str, str]],
         rules: Iterable[Mapping[str, Any]] = (), branch: str = "") -> List[Dict[str, str]]:
    """这张单实际用上的公司规则 → `[{id, kind, subject, label, value, value_label, branch}]`（按 `trace` 的先后，去重，
    最多 `USED_MAX` 条）。只认来源记成 `company_rule` 的归类键，规则按 `pick` 同一取法（分店的比全公司的具体）。"""
    picked = pick(rules, branch)
    out: List[Dict[str, str]] = []
    seen = set()
    for kind, subjects in (trace or {}).items():
        for subject in subjects or ():
            if subject is None or (kind, subject) in seen or (sources.get(kind) or {}).get(subject) != "company_rule":
                continue
            rule = (picked.get(kind) or {}).get(subject)
            if not rule:
                continue
            seen.add((kind, subject))
            out.append({"id": str(rule.get("id") or ""), "kind": kind, "subject": subject,
                        "label": str(rule.get("subject_label") or ""), "value": str(rule.get("value") or ""),
                        "value_label": str(rule.get("value_label") or ""), "branch": branch_of(rule.get("branch")) or ""})
            if len(out) >= USED_MAX:
                return out
    return out


def finish(decision: Dict[str, Any], trace: Mapping[str, List[Optional[str]]],
           sources: Mapping[str, Mapping[str, str]], rules: Iterable[Mapping[str, Any]] = (),
           branch: str = "") -> Dict[str, Any]:
    """把「按什么定的」放进 `decision["decided_by"]`、用上的公司规则放进 `decision["company_rules"]`（`used`）；付款方式由
    公司规则定时，`applied["payment_from"]` 记成 `company_rule`（写进载荷 `doctype_src`，CC 推送结果据此说清，同验收 F19）。
    就地改并回 `decision`。"""
    found = decided_by(trace, sources)
    if found:
        decision["decided_by"] = found
    rules_used = used(trace, sources, rules, branch)
    if rules_used:
        decision["company_rules"] = rules_used
    applied = decision.get("applied") or {}
    if found.get("payment") == "company_rule" and applied.get("payment_from") == "remembered":
        applied["payment_from"] = "company_rule"
    return decision


__all__ = ["MASTERS", "SOURCES", "USED_MAX", "WAIT_KINDS", "branch_of", "decided_by", "finish", "held_back", "overlay", "pick",
           "used", "valid"]
