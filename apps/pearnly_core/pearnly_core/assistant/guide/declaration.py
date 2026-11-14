"""引导声明的形状（多产品共用，纯契约：不依赖 Frappe、不读库、不做 IO；总设计 2.2）。

一份声明是一个纯字面量的 dict（产品模块里的 ``GUIDES`` 列表，hooks ``pearnly_guides`` 列出模块路径），引擎只认这里列出的键与取值；
多一个不认得的键、少一个必填、取值不在集合里，:func:`problems` 都会说出来（闸门 ``scripts/check-cc-guides.py`` 与注册表都用它）。

- ``source``：``documents`` 按单据出题（题目由写账前置判定与必填登记表算，声明不列步骤）；``form`` 按表单出题（题目就是 ``steps``）；
  ``chat`` 只有菜单与 CC 的开场一句、之后走打字（还没有表单的业务的现状，覆盖总表里算「做了一半」）。
- ``steps``：每步一个输入（:data:`INPUTS` 九种，前端各实现一次）；何时问 ``rule``（:data:`ASK`：``auto`` 自动定 / ``remember`` 问一次记住 /
  ``always`` 每次问）；菜单上的字与开场一句是术语表键（``label_key`` / ``opening_key``，不在声明里写文案）；选项来源、默认值、复核、确定后的动作都是点路径，指向产品已有的服务函数。
- 点路径这里只核写法；指向的函数在不在由闸门脚本按源码核。
"""
from __future__ import annotations

import re
from typing import Any, List, Mapping

MODES = ("chat", "operator")                                   # chat = 直写，operator = 代录
LEDGERS = ("express_local", "express_cloud", "mrerp")          # 这项业务面向哪几种账本
DOCUMENTS, FORM, CHAT = "documents", "form", "chat"
SOURCES = (DOCUMENTS, FORM, CHAT)
INPUTS = ("choice", "multi", "amounts", "split", "file", "month", "number", "text", "date")
OPTION_INPUTS = frozenset({"choice", "multi", "amounts", "split"})   # 要选项来源的输入
SEARCH_INPUTS = frozenset({"choice", "multi"})                       # 能带服务端搜索的输入
AUTO, REMEMBER, ALWAYS = "auto", "remember", "always"
ASK = (AUTO, REMEMBER, ALWAYS)
TIERS = ("how", "whether")                                     # 怎么办（推荐预选、点了才算）/ 做不做（不预选）
REMEMBER_SCOPES = ("company", "company_party", "ledger")
EXPIRES = ("ledger", "company", "period", "master")            # 哪些变化让这次引导失效（放太久、同业务重开对所有业务都适用，不用声明）
# 菜单图标白名单（lucide 的名字，前端按名字取；加图标 = 这里与前端图标表同一提交各加一项）
ICONS = frozenset({"shopping-cart", "tag", "wallet", "landmark", "scale", "store", "clipboard-list", "receipt", "percent",
                   "download", "folder-clock", "list-checks", "building", "kanban", "settings", "boxes", "file-spreadsheet",
                   "building-plus"})

GUIDE_KEYS = frozenset({"id", "label_key", "icon", "order", "modes", "ledgers", "allowed", "opening_key", "source", "steps", "writes",
                        "draft", "review", "confirm", "result", "tool", "expires", "judge"})
STEP_KEYS = frozenset({"key", "input", "rule", "tier", "options", "search", "default", "after", "when", "remember", "required"})
WHEN_TESTS = ("value", "has", "in")

_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_PATH = re.compile(r"^[a-z_][a-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
_KEY = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")                   # 术语表键


def _is_path(value: Any) -> bool:
    return isinstance(value, str) and bool(_PATH.match(value))


def _subset(value: Any, allowed) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and len(set(value)) == len(value) and all(v in allowed for v in value)


def _step_problems(step: Any, seen: List[str]) -> List[str]:
    if not isinstance(step, Mapping):
        return ["step is not a mapping"]
    key = step.get("key")
    if not isinstance(key, str) or not _ID.match(key):
        return [f"step key {key!r} is not an id"]
    out = [f"step {key}: unknown key {name!r}" for name in sorted(set(step) - STEP_KEYS)]
    if key in seen:
        out.append(f"step {key}: key used twice")
    kind = step.get("input")
    if kind not in INPUTS:
        out.append(f"step {key}: input {kind!r} is not one of {INPUTS}")
    ask = step.get("rule")
    if ask not in ASK:
        out.append(f"step {key}: rule {ask!r} is not one of {ASK}")
    if "tier" in step and step["tier"] not in TIERS:
        out.append(f"step {key}: tier {step['tier']!r} is not one of {TIERS}")
    if kind in OPTION_INPUTS and not _is_path(step.get("options")):
        out.append(f"step {key}: {kind} needs options (a dotted path)")
    if kind not in OPTION_INPUTS and "options" in step:
        out.append(f"step {key}: {kind} takes no options")
    if "search" in step and (step["search"] is not True or kind not in SEARCH_INPUTS):
        out.append(f"step {key}: search is True and only on {sorted(SEARCH_INPUTS)}")
    if ask == AUTO and not _is_path(step.get("default")):
        out.append(f"step {key}: rule auto needs default (a dotted path)")
    if "default" in step and not _is_path(step["default"]):
        out.append(f"step {key}: default is not a dotted path")
    if ask == REMEMBER and step.get("remember") not in REMEMBER_SCOPES:
        out.append(f"step {key}: rule remember needs remember in {REMEMBER_SCOPES}")
    if ask != REMEMBER and "remember" in step:
        out.append(f"step {key}: remember only with rule remember")
    if "required" in step and not isinstance(step["required"], bool):
        out.append(f"step {key}: required is a bool")
    after = step.get("after", [])
    if not isinstance(after, (list, tuple)) or any(name not in seen for name in after):
        out.append(f"step {key}: after names an earlier step")
    when = step.get("when")
    if when is not None:
        tests = [name for name in WHEN_TESTS if isinstance(when, Mapping) and name in when]
        if not isinstance(when, Mapping) or when.get("key") not in seen or len(tests) != 1 or set(when) - {"key", *WHEN_TESTS}:
            out.append(f"step {key}: when is {{key: an earlier step, one of {WHEN_TESTS}}}")
        elif tests[0] == "in" and not (isinstance(when["in"], (list, tuple)) and when["in"]):
            out.append(f"step {key}: when.in is a non-empty list")
    return out


def problems(guide: Any) -> List[str]:
    """这份声明哪里不合形状（空 = 合）。只看形状，不解析点路径。"""
    if not isinstance(guide, Mapping):
        return ["guide is not a mapping"]
    name = guide.get("id")
    if not isinstance(name, str) or not _ID.match(name):
        return [f"id {name!r} is not an id"]
    out = [f"unknown key {key!r}" for key in sorted(set(guide) - GUIDE_KEYS)]
    for key in ("label_key", "opening_key"):
        if not isinstance(guide.get(key), str) or not _KEY.match(guide[key]):
            out.append(f"{key} is a glossary key")
    if guide.get("icon") not in ICONS:
        out.append(f"icon {guide.get('icon')!r} is not on the icon list")
    if not isinstance(guide.get("order"), int) or isinstance(guide.get("order"), bool):
        out.append("order is an int")
    if not _subset(guide.get("modes"), MODES):
        out.append(f"modes is a non-empty subset of {MODES}")
    if not _subset(guide.get("ledgers"), LEDGERS):
        out.append(f"ledgers is a non-empty subset of {LEDGERS}")
    source = guide.get("source")
    if source not in SOURCES:
        out.append(f"source {source!r} is not one of {SOURCES}")
    for key in ("allowed", "draft", "review", "confirm", "result"):
        if key in guide and not _is_path(guide[key]):
            out.append(f"{key} is not a dotted path")
    if "tool" in guide and not (isinstance(guide["tool"], str) and _ID.match(guide["tool"])):
        out.append("tool is a tool name")
    if not isinstance(guide.get("writes"), bool):
        out.append("writes is a bool (does it change a ledger or delete something)")
    if "expires" in guide and not _subset(guide["expires"], EXPIRES):
        out.append(f"expires is a non-empty subset of {EXPIRES}")
    steps = guide.get("steps", [])
    if not isinstance(steps, (list, tuple)):
        return out + ["steps is a list"]
    seen: List[str] = []
    for step in steps:
        out.extend(_step_problems(step, seen))
        if isinstance(step, Mapping) and isinstance(step.get("key"), str):
            seen.append(step["key"])
    if source == FORM:
        if not steps:
            out.append("a form guide needs steps")
        if "confirm" not in guide:
            out.append("a form guide needs confirm")
        if guide.get("writes") is True and "review" not in guide:
            out.append("a form guide that writes needs review")
    if source == DOCUMENTS and any(isinstance(s, Mapping) and s.get("input") != "file" for s in steps):
        out.append("a documents guide only declares file steps (the questions come from the write precheck)")
    if source == CHAT and (steps or any(key in guide for key in ("draft", "review", "confirm", "result"))):
        out.append("a chat guide has no steps, draft, review, confirm or result")
    judge = guide.get("judge")
    if judge is not None and not (isinstance(judge, (list, tuple)) and judge and all(isinstance(j, str) and _ID.match(j) for j in judge)):
        out.append("judge is a list of ids")
    return [f"{name}: {text}" for text in out]


def paths(guide: Mapping[str, Any]) -> List[str]:
    """声明里出现的全部点路径（闸门按源码核它们指向的函数在不在）。"""
    found = [guide[key] for key in ("allowed", "draft", "review", "confirm", "result") if _is_path(guide.get(key))]
    for step in guide.get("steps") or ():
        found.extend(step[key] for key in ("options", "default") if isinstance(step, Mapping) and _is_path(step.get(key)))
    return found


def complete(guide: Mapping[str, Any]) -> bool:
    """算不算完整的引导（覆盖总表的「✅」前提之一）：``chat`` 只有开场一句，不算。"""
    return guide.get("source") in (DOCUMENTS, FORM)


__all__ = ["ALWAYS", "ASK", "AUTO", "CHAT", "DOCUMENTS", "EXPIRES", "FORM", "GUIDE_KEYS", "ICONS", "INPUTS", "LEDGERS", "MODES",
           "OPTION_INPUTS", "REMEMBER", "REMEMBER_SCOPES", "SEARCH_INPUTS", "SOURCES", "STEP_KEYS", "TIERS", "WHEN_TESTS",
           "complete", "paths", "problems"]
