"""用户默认值写入后，在事务提交之后再清一次缓存（多产品共用，主会话 11-15「切换当前公司间歇不生效」）。

根因（锁定的 Frappe 16.33.0，`frappe/defaults.py` 的 `set_default` 与 `frappe.client_cache`）：
1. ``set_default`` 写库后**立刻**清 ``defaults::<用户>``（提交之前）。这之后、本请求提交之前，同一用户的另一个请求（页面刷新、
   启动数据、轮询、别的标签页）照 ``get_defaults_for`` 读库——读到的还是已提交的旧值——再写回缓存（存在 Redis 里，各进程都认）。
   提交后缓存里就是旧值。
2. 之后再点「设为当前」：库里已经是新值，``set_default`` 判「没变」直接返回、**不清缓存**——旧值一直卡着，点几次都没用。

做法（一处管全部写用户默认值的地方，调用方不改）：
- ``DefaultValue`` 每插入一行（``set_default`` 换值就是删旧行、插新行）都挂 :func:`after_write`：登记到 ``frappe.db.after_commit``，
  **提交之后**再清这个用户的默认值缓存与启动数据缓存——这时库里已是新值，之后谁再回填都是新值。
- 当前公司这一处另走 :func:`current`：直接读已提交的那一行，不经会被回填的缓存（`masters.company.active_company`）。
"""
from __future__ import annotations

import frappe


def _clear(parent):
    from frappe.cache_manager import clear_defaults_cache

    clear_defaults_cache(parent)
    frappe.clear_cache(user=parent)  # 同 Frappe `_clear_cache`：连带启动数据里的用户默认值


def after_write(doc, method=None):
    """（``DefaultValue`` 的 after_insert）提交之后再清一次这个用户（或全局）的默认值缓存。"""
    parent = doc.get("parent")
    if not parent or frappe.flags.in_install:
        return
    frappe.db.after_commit.add(lambda: _clear(parent))


def refresh(parent):
    """当场清一次、提交之后再清一次（值没变时 Frappe 不清：已经卡住的旧值靠这个解开）。"""
    _clear(parent)
    frappe.db.after_commit.add(lambda: _clear(parent))


def current(key, user=None):
    """读这个用户默认值的已提交值（不经会被并发回填的缓存）；没设回 ``None``。"""
    user = user or frappe.session.user
    rows = frappe.get_all("DefaultValue", filters={"parent": user, "defkey": key}, fields=["defvalue"],
                          order_by="creation desc", limit_page_length=1)
    return rows[0].defvalue if rows else None
