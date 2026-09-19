# -*- coding: utf-8 -*-
""""这个操作员能不能查 DMS"的服务端判据。

照搬老产品 `services/line_dms/query_access.py`（线上基准 `501589f0`）。

**权限判据在后端，不在"前端把菜单藏起来"** —— 菜单卡只是不显示第 5 项；
真拦人的是这里。老产品查的是花名册里那一栏（`can_query_dms`），新平台同一处：
`DMS Operator` 的 `can_query_dms` 字段，且**必须还是启用状态**（停用即收权）。
"""

from __future__ import annotations

import frappe

OPERATOR = "DMS Operator"


def can_query(binding: dict) -> bool:
    """有没有查询权限。没绑定 / 没账号 / 被停用 / 没勾那一栏 → 一律 False。"""
    user = str((binding or {}).get("user_id") or "")
    if not user:
        return False
    row = frappe.db.get_value(
        OPERATOR,
        {"user": user, "status": "active"},
        ["status", "can_query_dms"],
        as_dict=True,
    )
    return bool(row and row.get("can_query_dms"))
