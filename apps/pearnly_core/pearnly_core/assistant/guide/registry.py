"""引导声明注册表（多产品共用；总设计 2.4）：收各产品 hooks ``pearnly_guides`` 列出的声明，给业务菜单。

产品在 ``hooks.py`` 写 ``pearnly_guides = ["<模块路径>.GUIDES", ...]``，每个路径指向一个声明列表（纯字面量，形状见 :mod:`declaration`）。
形状不合的声明不上菜单、记一笔日志（闸门 ``scripts/check-cc-guides.py`` 在合入前就拦，这里只是不让一份坏声明拖垮整个菜单）；
同一个 id 声明两次算坏。菜单只列「这个模式能办 + 这个人有权限」的；账本种类与此刻能不能办（``capable``）在接上产品判定时加。
"""
from __future__ import annotations

from typing import Any, Dict, List

import frappe

from pearnly_core.assistant.guide import declaration

HOOK = "pearnly_guides"


def _raw() -> List[Any]:
    found: List[Any] = []
    for path in frappe.get_hooks(HOOK) or []:
        value = frappe.get_attr(path)
        found.extend(value if isinstance(value, (list, tuple)) else [value])
    return found


def declared() -> List[Dict[str, Any]]:
    """本站产品声明的全部引导（形状合的），按 ``order`` 排。"""
    good: Dict[str, Dict[str, Any]] = {}
    bad: List[str] = []
    for guide in _raw():
        found = declaration.problems(guide)
        if not found and guide["id"] in good:
            found = [f"{guide['id']}: declared twice"]
        if found:
            bad.extend(found)
            continue
        good[guide["id"]] = dict(guide)
    if bad:
        frappe.log_error(title="Assistant guide declarations rejected", message="\n".join(bad))
    return sorted(good.values(), key=lambda guide: (guide["order"], guide["id"]))


def find(guide_id: str) -> Dict[str, Any] | None:
    return next((guide for guide in declared() if guide["id"] == str(guide_id or "")), None)


def _allowed(guide: Dict[str, Any]) -> bool:
    """这个人能不能办这项业务（声明的 ``allowed(user) -> bool``：产品里这项功能的那一处权限判断，勾了这个功能的人才有）；
    没声明 = 跟着产品的 CC 入口权限走。公司一层（本人可用的公司）在开始引导时由业务函数自己核。"""
    if not guide.get("allowed"):
        return True
    try:
        return bool(frappe.get_attr(guide["allowed"])(frappe.session.user))
    except Exception:  # noqa: BLE001 — 权限判断出错按没有权限处理，不上菜单
        frappe.log_error(title="Assistant guide permission check failed")
        return False


def menu(mode: str) -> List[Dict[str, Any]]:
    """这个模式的业务菜单：``[{id, label_key, icon, order, source}]``（``label_key`` 是术语表键，由调用方按本轮语言翻）。"""
    return [{"id": guide["id"], "label_key": guide["label_key"], "icon": guide["icon"], "order": guide["order"],
             "source": guide["source"]}
            for guide in declared() if mode in guide["modes"] and _allowed(guide)]


def offered() -> Dict[str, List[str]]:
    """``{chat: [业务 id], operator: [...]}``：同第 1 期 ``guided.offered`` 的形状（旧 hooks ``pearnly_guide_businesses`` 的替代）。"""
    return {mode: [item["id"] for item in menu(mode)] for mode in declaration.MODES}


__all__ = ["HOOK", "declared", "find", "menu", "offered"]
