"""替写的切换时刻（旧件闸，主会话 11-17）：两产品各自 App 的迁移补丁调同一个函数，在存量站点迁移那一刻写上。

规则本身在 `pearnly_core.cc_desktop.wire.route_contracts`（:data:`~pearnly_core.cc_desktop.wire.route_contracts.WRITER_RULE_SINCE`、
``stand_in_open``）：配了切换时刻，只有那之后排的件能由别人的电脑替写；没配 = 全部件都能（新开户站点不跑补丁，天然如此）。
用户看过上线前的统计、定了「旧件也写」之后，删掉这项站点配置即放开。
"""
from __future__ import annotations

from datetime import datetime, timezone

import frappe

from pearnly_core.cc_desktop.wire import route_contracts

# 站点配置键（同 `route_contracts.WRITER_RULE_SINCE`；写成字面量，配置键清单 `scripts/impact/product-map/config.toml` 按它认）
WRITER_RULE_SINCE = "pearnly_writer_rule_since"
assert WRITER_RULE_SINCE == route_contracts.WRITER_RULE_SINCE


def current() -> str:
    """此刻的切换时刻（站点配置，没配回空串）：领单、回读领取都按它判（`route_contracts.stand_in_open` / ``db_time``）。"""
    return str(frappe.conf.get(WRITER_RULE_SINCE) or "")


def stamp_since(now=None) -> str:
    """站点配置里还没有切换时刻就写成此刻（ISO UTC）；已有就不动（两产品的补丁都调，先跑的那个写，幂等）。回生效的值。"""
    current = (frappe.get_site_config() or {}).get(WRITER_RULE_SINCE)
    if current:
        return str(current)
    value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    from frappe.installer import update_site_config  # 不能写成 frappe.installer.update_site_config（不是自动挂的属性）

    update_site_config(WRITER_RULE_SINCE, value)
    frappe.conf[WRITER_RULE_SINCE] = value  # 同一进程里接着跑的看得到
    return value


__all__ = ["WRITER_RULE_SINCE", "current", "stamp_since"]
