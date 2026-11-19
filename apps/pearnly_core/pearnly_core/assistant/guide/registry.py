"""引导声明注册表（多产品共用；总设计 2.4）：收各产品 hooks ``pearnly_guides`` 列出的声明，给业务菜单。

产品在 ``hooks.py`` 写 ``pearnly_guides = ["<模块路径>.GUIDES", ...]``，每个路径指向一个声明列表（纯字面量，形状见 :mod:`declaration`）。
形状不合的声明不上菜单、记一笔日志（闸门 ``scripts/check-cc-guides.py`` 在合入前就拦，这里只是不让一份坏声明拖垮整个菜单）；
同一个 id 声明两次算坏。菜单只列「这个模式能办 + 这个人有权限」的；账本种类与此刻能不能办（``capable``）在接上产品判定时加。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

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


# 产品声明「这段对话的账本是哪一种」：``pearnly_guide_ledger = ["<点路径>"]``，函数(对话号) → ``declaration.LEDGERS`` 之一；
# 公司没接账本回 :data:`NO_LEDGER`；对话还没挂公司（或判不出）回 None（不按账本过滤）。没声明的产品不按账本过滤
LEDGER_HOOK = "pearnly_guide_ledger"
NO_LEDGER = "none"


def ledger_of(session_id: str) -> str | None:
    """这段对话此刻的账本种类（产品判，见 :data:`LEDGER_HOOK`）；判不出 / 出错回 None。"""
    paths = frappe.get_hooks(LEDGER_HOOK) or []
    if not session_id or not paths:
        return None
    try:
        found = frappe.get_attr(paths[-1])(session_id)
    except Exception:  # noqa: BLE001 — 判不出不挡菜单：按不过滤处理
        frappe.log_error(title="Assistant guide ledger check failed")
        return None
    return found if found in declaration.LEDGERS or found == NO_LEDGER else None


def fits_ledger(guide: Mapping[str, Any], ledger: str | None) -> bool:
    """这项业务对这种账本出不出现：None（还没挂公司 / 判不出）不过滤；没接账本的一项都不出；其余看声明的 ``ledgers``。"""
    if ledger is None:
        return True
    return ledger != NO_LEDGER and ledger in (guide.get("ledgers") or ())


def menu(mode: str, ledger: str | None = None) -> List[Dict[str, Any]]:
    """这个模式的业务菜单：``[{id, label_key, icon, order, source}]``（``label_key`` 是术语表键，由调用方按本轮语言翻）。
    ``ledger``：这段对话的账本种类（:func:`ledger_of`）——不在声明 ``ledgers`` 里的业务不出现（不置灰、不说明，11-19）。"""
    return [{"id": guide["id"], "label_key": guide["label_key"], "icon": guide["icon"], "order": guide["order"],
             "source": guide["source"]}
            for guide in declared() if mode in guide["modes"] and fits_ledger(guide, ledger) and _allowed(guide)]


def offered(ledger: str | None = None) -> Dict[str, List[str]]:
    """``{chat: [业务 id], operator: [...]}``：同第 1 期 ``guided.offered`` 的形状（旧 hooks ``pearnly_guide_businesses`` 的替代）。"""
    return {mode: [item["id"] for item in menu(mode, ledger)] for mode in declaration.MODES}


__all__ = ["HOOK", "LEDGER_HOOK", "NO_LEDGER", "declared", "find", "fits_ledger", "ledger_of", "menu", "offered"]
