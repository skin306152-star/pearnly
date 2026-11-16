"""按表单出题的步骤引擎（多产品共用，纯函数：不依赖 Frappe、不读库、不做 IO；总设计 2.3、2.4）。

输入是一份 ``form`` 声明（:mod:`declaration`）、已有的答案 ``{步骤 key: 值}`` 与后台已定的 ``{步骤 key: {value, source, ...}}``，
输出是「现在该问哪几步」「问完没有」。选项从哪来、默认值怎么算、记住的答案在哪，都由调用方（:mod:`run`）去问产品函数，这里不碰。

规则：

- 一步**适用**：它的 ``when`` 成立（依赖的那一步已有值且对得上）。不适用的不问、不要求有值。
- 一步**可以问**：适用，且 ``after`` 与 ``when`` 点名的步骤都已有值（答过的或后台定的）。先后只由依赖定，没依赖的几步同一张卡一起问。
- 一步**已定**：后台给了值（``rule`` 是 ``auto``，或 ``remember`` 且记住过）而用户没改；进「已定 N 项」，展开可改。
  ``auto`` 给不出值就降成问。``always`` 永远要用户明答（钱和对象的事不记不预选）。
- 改了前面一步的答案，依赖它的后面各步的答案作废（:func:`without_dependents`）：选了别的客户，勾过的发票不能留着。
- 值的形状按输入方式核（:func:`value_problem`）；值在不在选项里由调用方拿着选项核。
- **可不答的步骤**（``required: False``）：没答不挡「问完」；``after`` 点名它的步骤照常问（同一张卡上一起出）；``when`` 依赖它的步骤
  在它没答时不适用，也不算「没轮到」；交给业务函数的值里没有它。
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping

from pearnly_core.assistant.guide import declaration as d

ASKING, READY = "asking", "ready"
TEXT_MAX = 500           # 一格输入最长
ROWS_MAX = 200           # 多选 / 金额多选 / 分摊最多几行
FILES_MAX = 20           # 一步最多交几份
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_FILE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


def _has(value: Any) -> bool:
    return value not in (None, "", [], (), {})


def _values(guide: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """每一步此刻的值：用户答的优先，其次后台定的。``always`` 的步骤后台给的值不算数（要用户明答了才有值，
    否则依赖它的后一步会在用户还没定之前就按后台的猜测出题）。"""
    always = {step["key"] for step in guide.get("steps") or () if step.get("rule") == d.ALWAYS}
    out = {key: item.get("value") for key, item in (decided or {}).items()
           if _has(item.get("value")) and key not in always}
    out.update({key: value for key, value in (answers or {}).items() if _has(value)})
    return out


def _codes(value: Any) -> List[str]:
    """一个值里出现的选项码（单选是它自己；多选是各项；金额多选 / 分摊是各行的 code）。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item.get("code")) if isinstance(item, Mapping) else str(item) for item in value]
    return []


def applies(step: Mapping[str, Any], values: Mapping[str, Any]) -> bool:
    """这一步的 ``when`` 成不成立（没有 ``when`` = 成立）。依赖的那一步还没值 = 不成立（先问它）。"""
    when = step.get("when")
    if not when:
        return True
    other = values.get(when["key"])
    if not _has(other):
        return False
    codes = _codes(other)
    if "value" in when:
        return codes == [str(when["value"])]
    if "has" in when:
        return str(when["has"]) in codes
    if "starts" in when:
        return any(code.startswith(str(when["starts"])) for code in codes)
    return any(code in {str(v) for v in when["in"]} for code in codes)


def _optional_keys(guide: Mapping[str, Any]) -> set:
    """声明里可不答的步骤（``required: False``）。"""
    return {step["key"] for step in guide.get("steps") or () if step.get("required") is False}


def _needs(step: Mapping[str, Any], optional=frozenset()) -> List[str]:
    """这一步要先有值的步骤：``after`` 与 ``when`` 点名的；可不答的那几步不算（没答也不挡后面的步骤）。"""
    return [name for name in (*step.get("after", ()), *([step["when"]["key"]] if step.get("when") else ()))
            if name not in optional]


def _undecided(step: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]]) -> bool:
    """这一步还要用户答：没答过，且后台没给它定下值（``always`` 的步骤后台给了值也不算定）。"""
    if _has((answers or {}).get(step["key"])):
        return False
    return step["rule"] == d.ALWAYS or not _has(((decided or {}).get(step["key"]) or {}).get("value"))


def to_ask(guide: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """现在这张卡上问哪几步（照声明顺序）：适用、依赖都有值、还没定的。"""
    values = _values(guide, answers, decided)
    optional = _optional_keys(guide)
    return [dict(step) for step in guide.get("steps") or ()
            if applies(step, values) and all(_has(values.get(name)) for name in _needs(step, optional))
            and _undecided(step, answers, decided)]


def blocked(guide: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]]) -> List[str]:
    """还没轮到的步骤（依赖的那一步没值，现在既问不了也判不出适不适用）。"""
    values = _values(guide, answers, decided)
    optional = _optional_keys(guide)
    return [step["key"] for step in guide.get("steps") or ()
            if not all(_has(values.get(name)) for name in _needs(step, optional))]


def settled(guide: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """「已定 N 项」：适用、后台定了值、用户没改、且不是每次都要明答的步骤（``{key, value, source, ...}`` 照后台给的带上）。"""
    values = _values(guide, answers, decided)
    out = []
    for step in guide.get("steps") or ():
        item = (decided or {}).get(step["key"]) or {}
        if step["rule"] != d.ALWAYS and _has(item.get("value")) and not _has((answers or {}).get(step["key"])) \
                and applies(step, values):
            out.append({**item, "key": step["key"], "input": step["input"]})
    return out


def state(guide: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]]) -> str:
    """``asking``：还有要问的或没轮到的；``ready``：适用的步骤都有值了，可以进复核 / 确定。"""
    optional = _optional_keys(guide)
    waiting = [step for step in to_ask(guide, answers, decided) if step["key"] not in optional]
    return ASKING if waiting or blocked(guide, answers, decided) else READY


def final_values(guide: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """交给声明的 ``draft`` / ``confirm`` 的值：只含适用的步骤（不适用的那几步就算留着旧答案也不带）。"""
    values = _values(guide, answers, decided)
    return {step["key"]: values[step["key"]] for step in guide.get("steps") or ()
            if _has(values.get(step["key"])) and applies(step, values)}


def dependents(guide: Mapping[str, Any], key: str) -> List[str]:
    """直接或间接依赖 ``key`` 这一步的步骤（照声明顺序）。"""
    found: List[str] = []
    for step in guide.get("steps") or ():
        if any(name == key or name in found for name in _needs(step)):
            found.append(step["key"])
    return found


def without_dependents(guide: Mapping[str, Any], answers: Mapping[str, Any], changed: Iterable[str]) -> Dict[str, Any]:
    """改了 ``changed`` 这几步之后还留着的答案：依赖它们的后面各步作废（被改的那几步自己的新值由调用方另写）。"""
    drop = {name for key in changed for name in dependents(guide, key)} - set(changed)
    return {key: value for key, value in (answers or {}).items() if key not in drop}


def amount(value: Any) -> Decimal | None:
    try:
        found = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return found if found.is_finite() else None


def _rows_problem(value: Any) -> str:
    if not isinstance(value, (list, tuple)) or not value or len(value) > ROWS_MAX:
        return "a non-empty list of rows"
    codes = []
    for row in value:
        found = amount(row.get("amount")) if isinstance(row, Mapping) else None
        if not isinstance(row, Mapping) or not isinstance(row.get("code"), str) or not row["code"] or found is None or found <= 0:
            return "each row is {code, amount} with a positive amount"
        codes.append(row["code"])
    return "a code appears twice" if len(set(codes)) != len(codes) else ""


def value_problem(step: Mapping[str, Any], value: Any) -> str:
    """这个值的形状对不对（空串 = 对）。只看形状：在不在选项里、金额超没超可收、合计对不对由调用方与业务函数核。"""
    kind = step["input"]
    if kind == "choice":
        return "" if isinstance(value, str) and value and len(value) <= TEXT_MAX else "one option code"
    if kind == "multi":
        ok = isinstance(value, (list, tuple)) and 0 < len(value) <= ROWS_MAX and len(set(value)) == len(value) \
            and all(isinstance(v, str) and v for v in value)
        return "" if ok else "a non-empty list of distinct option codes"
    if kind in ("amounts", "split"):
        return _rows_problem(value)
    if kind == "file":
        ok = isinstance(value, (list, tuple)) and 0 < len(value) <= FILES_MAX and len(set(value)) == len(value) \
            and all(isinstance(v, str) and _FILE.match(v) for v in value)
        return "" if ok else "a non-empty list of distinct file ids"
    if kind == "month":
        return "" if isinstance(value, str) and _MONTH.match(value) else "a month as YYYY-MM"
    if kind == "number":
        return "" if isinstance(value, (str, int)) and not isinstance(value, bool) and amount(value) is not None else "a number"
    if kind == "date":
        try:
            date.fromisoformat(value if isinstance(value, str) else "")
        except ValueError:
            return "a date as YYYY-MM-DD"
        return ""
    return "" if isinstance(value, str) and value.strip() and len(value) <= TEXT_MAX else "a short text"


def problems_in(guide: Mapping[str, Any], answers: Mapping[str, Any], decided: Mapping[str, Mapping[str, Any]],
                given: Mapping[str, Any]) -> Dict[str, str]:
    """页面交来的这批答案哪里不收（``{步骤 key: 原因}``，空 = 都收）：只收此刻可以问的步骤与「已定」里的步骤，值要合形状。"""
    asked = {step["key"]: step for step in to_ask(guide, answers, decided)}
    for item in settled(guide, answers, decided):
        asked[item["key"]] = next(step for step in guide["steps"] if step["key"] == item["key"])
    # 已经答过的步骤也可以改（改了以后依赖它的各步作废，由调用方处理）
    for step in guide.get("steps") or ():
        if _has((answers or {}).get(step["key"])):
            asked.setdefault(step["key"], step)
    out: Dict[str, str] = {}
    for key, value in (given or {}).items():
        step = asked.get(key)
        if step is None:
            out[key] = "not asked now"
            continue
        found = value_problem(step, value)
        if found:
            out[key] = found
    return out


__all__ = ["ASKING", "FILES_MAX", "READY", "ROWS_MAX", "TEXT_MAX", "amount", "applies", "blocked", "dependents", "final_values",
           "problems_in", "settled", "state", "to_ask", "value_problem", "without_dependents"]
