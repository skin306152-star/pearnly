"""引导式录入的开关（多产品共用，CC 引导式录入第 1 期）：这次调用是不是在 CC 对话的工具调用里。

CC 对话里的判定与写入一律按引导卡口径（共用写账规则 ``express_rules.push.choices.resolve(guided=True)``：判不出的出题、不静默默认）；
判定与写入两次都在 CC 对话里发生，所以同一个判断两边一致，答过的写的时候也认。LINE、网页、定时任务、自动推不在 CC 对话里，行为不变。
工具执行时 ``toolrun`` 把本对话号放在 ``frappe.local.pearnly_assistant``（同 ``ledger._session``）。
"""
from __future__ import annotations

import frappe


def active() -> bool:
    local = getattr(frappe, "local", None)  # 没有请求上下文（离线测试替身、脚本）= 不在 CC 对话里
    return bool((getattr(local, "pearnly_assistant", None) or {}).get("session_id"))


__all__ = ["active"]
