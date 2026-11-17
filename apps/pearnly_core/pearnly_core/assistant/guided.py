"""引导式录入的开关（多产品共用，CC 引导式录入第 1 期）：这次调用是不是在 CC 对话的工具调用里。

CC 对话里的判定与写入一律按引导卡口径（共用写账规则 ``express_rules.push.choices.resolve(guided=True)``：判不出的出题、不静默默认）；
判定与写入两次都在 CC 对话里发生，所以同一个判断两边一致，答过的写的时候也认。LINE、网页、定时任务、自动推不在 CC 对话里，行为不变。
工具执行时 ``toolrun`` 把本对话号放在 ``frappe.local.pearnly_assistant``（同 ``ledger._session``）。

**出题、记答案、开始前判定、写账四步必须同一口径**：同一张卡、同一张单只要有一步在对话里，其余几步也要在同一段对话里做
（:func:`within`）。代录开始卡（页面接口带任务号）、代录任务重交（页面点继续 / 中途作答 / 收尾时的后台）都不是工具调用，
用 :func:`within` 进任务的对话；否则引导口径才问的题（收入科目 A14、明细对不上 A7…）出了题却记不下、写的时候又不认
（11-17 回归 erp.cc.operator.intake.sales）。
"""
from __future__ import annotations

from contextlib import contextmanager

import frappe


@contextmanager
def within(session_id: str):
    """这个范围里的判定与写入按这段对话做（同工具调用：本对话账套、共用写账规则走引导口径）；对话号空时不变。用完还原。"""
    if not session_id:
        yield
        return
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(session_id)}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def offered() -> dict:
    """本站产品声明的业务清单 ``{chat: [...], operator: [...]}``：只有一处来源——产品的引导声明（``pearnly_guides``，
    ``guide.registry``：这个模式能办 + 这个人有权限的）。第 1 期另写的 ``pearnly_guide_businesses`` 已删（主会话 11-17：
    菜单按声明出，不再写死三项）。"""
    from pearnly_core.assistant.guide import registry

    return registry.offered()


def business_of(session_id: str) -> str:
    """这段对话选的业务（输入框上方的业务菜单，``Pearnly Assistant Session.business``）；没选回空。"""
    if not session_id:
        return ""
    return str(frappe.db.get_value("Pearnly Assistant Session", {"session_id": session_id}, "business") or "")


def active() -> bool:
    local = getattr(frappe, "local", None)  # 没有请求上下文（离线测试替身、脚本）= 不在 CC 对话里
    return bool((getattr(local, "pearnly_assistant", None) or {}).get("session_id"))


__all__ = ["active", "business_of", "offered", "within"]
