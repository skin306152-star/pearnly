"""引导式录入的开关（多产品共用，CC 引导式录入第 1 期）：这次调用是不是在 CC 对话的工具调用里。

CC 对话里的判定与写入一律按引导卡口径（共用写账规则 ``express_rules.push.choices.resolve(guided=True)``：判不出的出题、不静默默认）；
判定与写入两次都在 CC 对话里发生，所以同一个判断两边一致，答过的写的时候也认。LINE、网页、定时任务、自动推不在 CC 对话里，行为不变。
工具执行时 ``toolrun`` 把本对话号放在 ``frappe.local.pearnly_assistant``（同 ``ledger._session``）。
"""
from __future__ import annotations

import frappe


BUSINESSES = ("purchase", "sales", "collection")
# 产品在 hooks.py 声明这个产品 × 这个模式真能做的业务（输入框上方的业务菜单只列这些；评估 6.1、12 节第 3 问）：
#     pearnly_guide_businesses = {"chat": ["purchase", "sales"], "operator": ["purchase", "sales"]}
BUSINESS_HOOK = "pearnly_guide_businesses"


def offered() -> dict:
    """本站产品声明的业务清单 ``{chat: [...], operator: [...]}``（只留认得的；没声明 = 空，不出菜单）。一个站点一个产品，取最后声明的。"""
    found = frappe.get_hooks(BUSINESS_HOOK) or {}
    found = found[-1] if isinstance(found, list) and found else found
    found = found if isinstance(found, dict) else {}
    out = {}
    for mode in ("chat", "operator"):
        values = found.get(mode) or []
        values = values[-1] if values and isinstance(values[0], list) else values
        out[mode] = [value for value in values if value in BUSINESSES]
    return out


def active() -> bool:
    local = getattr(frappe, "local", None)  # 没有请求上下文（离线测试替身、脚本）= 不在 CC 对话里
    return bool((getattr(local, "pearnly_assistant", None) or {}).get("session_id"))


__all__ = ["BUSINESSES", "BUSINESS_HOOK", "active", "offered"]
