"""「选择要用的公司」自动弹窗记谁看过哪些（公司接入重做第 2 批，2026-11-14；用户定 A 方案）。

CC 桌面版授权（或 MR.ERP 目录刷新）后，连接里有还没接进 ERP 的公司（`express.companies.pending`），ERP 网页给能建公司的人
弹一次选择窗；关掉、或勾完建好，这一批标识记在本人的用户默认值里（官方 `frappe.defaults`，不另建表），同一批不再弹，
以后再有新的才弹。以后要加随时点公司页「从连接添加」。
"""
import json

import frappe

SEEN_KEY = "pearnly_ledgers_seen"


def _seen(user) -> set:
    try:
        return set(json.loads(frappe.defaults.get_user_default(SEEN_KEY, user) or "[]"))
    except (TypeError, ValueError):
        return set()


def unseen(user=None) -> int:
    """还要人勾、本人还没看过的有几家（启动数据用；只读缓存，没有才现算）。"""
    from pearnly_erp_portal.express import companies

    user = user or frappe.session.user
    return len(set(companies.pending()) - _seen(user))


def mark_seen(user=None) -> None:
    """本人看过了现在这一批：只记还在名单里的那些（名单上的建了就不在了，记录不会越积越多）。"""
    from pearnly_erp_portal.express import companies

    user = user or frappe.session.user
    frappe.defaults.set_user_default(SEEN_KEY, json.dumps(sorted(companies.pending())), user)
