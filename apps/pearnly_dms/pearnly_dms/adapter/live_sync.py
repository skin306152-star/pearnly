# -*- coding: utf-8 -*-
"""白天每分钟看一次 DMS 使用记录，把别人在 DMS 网页上的改动补回缓存（2026-09-27）。

## 为什么

以前增量只在"LINE 这边有人操作"时顺手做（`change_sync.catch_up`）：有人直接在 DMS 网页上
改了车型、客户，而 LINE 这边一直没人操作，缓存就一直旧到凌晨全量。

2026-09-27 在 DMS-TEST 实测：使用记录按模块查条数每次约 0.07 秒，改动当秒可见；登录会话
复用后（`adapter/session_cache`）一轮检查约 2 秒，不再有 6–16 秒的登录。于是白天每分钟
看一眼，有变化才补读被点名的那几条。

## 规则

* 只在白天窗口内跑（`pearnly_dms_live_sync_hours`，默认 `7-21`，站点时区）；夜里交给全量回读。
* 间隔 `pearnly_dms_live_sync_seconds`（默认 60 秒，`0` 关闭）；会话复用关闭时也不跑 ——
  没有复用就等于每分钟登录一次，会一直顶掉同账号的人。
* 走专用 `dms_sync` 队列、只读批次（遇到操作员的交互会话就让路），同一连接同时只跑一个。
* **白天不做大组整组重读**：日志点不出具体记录时，客户/地址两个大组只标记"待重读"，
  由凌晨全量完成；车辆、公司这类小组照常立即整组重读（`change_sync._request_refresh`）。
* 管理员会话刚被人顶掉（有人在用同一账号登录网页）时，接下来一段时间不再为了同步去登录，
  免得每分钟把那个人踢下线一次；配了系统账号池（`adapter/admin_pool`）就不会遇到这个问题。
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import frappe

logger = logging.getLogger(__name__)

DEFAULT_SECONDS = 60
DEFAULT_HOURS = "7-21"
#: 客户、地址：整组重读一次可能要一个多小时，白天只标记，留给夜里。
HEAVY_DATASETS = frozenset({"customers", "geo"})
_THROTTLE_KEY = "pearnly:dms:live-sync:last"
JOB_TIMEOUT = 300


def interval_seconds() -> int:
    try:
        return max(0, int(frappe.conf.get("pearnly_dms_live_sync_seconds", DEFAULT_SECONDS)))
    except (TypeError, ValueError):
        return DEFAULT_SECONDS


def _hours() -> Tuple[int, int]:
    raw = str(frappe.conf.get("pearnly_dms_live_sync_hours") or DEFAULT_HOURS)
    try:
        start, end = (int(part) for part in raw.split("-", 1))
        if 0 <= start <= 23 and 1 <= end <= 24 and start < end:
            return start, end
    except (TypeError, ValueError):
        pass
    return 7, 21


def enabled() -> bool:
    from pearnly_dms.adapter import session_cache

    return interval_seconds() > 0 and session_cache.enabled()


def in_business_hours(now=None) -> bool:
    now = now or frappe.utils.now_datetime()  # 站点时区
    start, end = _hours()
    return start <= now.hour < end


def defer_heavy(dataset: str) -> bool:
    """白天窗口内，大组的整组重读不马上做（由凌晨全量完成）。"""
    return enabled() and dataset in HEAVY_DATASETS and in_business_hours()


def _primary_endpoint() -> Optional[str]:
    return frappe.db.get_value("Pearnly ERP Endpoint", {
        "adapter": "mrerp_dms", "owner_user": ["is", "not set"], "enabled": 1}, "name")


def tick() -> None:
    """每分钟的调度入口：条件都满足才往 `dms_sync` 队列派一次（去重，不叠加）。"""
    if not enabled() or not in_business_hours():
        return
    endpoint = _primary_endpoint()
    if not endpoint:
        return
    try:
        cache = frappe.cache()
        if not cache.set(cache.make_key(_THROTTLE_KEY), "1", nx=True,
                         ex=max(30, interval_seconds() - 5)):
            return
    except Exception:
        return
    frappe.enqueue("pearnly_dms.adapter.live_sync.pull", queue="dms_sync", timeout=JOB_TIMEOUT,
                   job_id="dms-live-sync-" + endpoint, deduplicate=True, endpoint=endpoint)


def pull(endpoint: str) -> dict:
    """一轮增量：复用会话读使用记录，补读被点名的记录。从不抛异常。"""
    from pearnly_dms.adapter import change_sync, session_cache
    from pearnly_dms.adapter.operations import run_logged_in
    from pearnly_dms.snapshots import store

    state = store.change_state(endpoint) or {}
    if not change_sync.seeded(state):
        return {"ok": True, "skipped": "not_seeded"}
    if _admin_account_in_use(endpoint):
        return {"ok": True, "skipped": "account_in_use"}

    def work(client, adapter):
        change_sync.catch_up(endpoint, client=client)
        return {"ok": True}

    try:
        result = run_logged_in(endpoint, work, authoritative_read=True, background=True,
                               read_only_batch=True)
    except Exception:
        logger.warning("[dms live sync] round did not finish", exc_info=True)
        return {"ok": False}
    return result if isinstance(result, dict) else {"ok": False}


def _admin_account_in_use(endpoint: str) -> bool:
    """连接自带的管理员账号最近被人顶掉过、又没有系统账号池可换时，这一轮不去登录。"""
    from pearnly_core.session_lock import dms_login_key
    from pearnly_dms.adapter import admin_pool, session_cache

    row = frappe.db.get_value("Pearnly ERP Endpoint", endpoint,
                              ["system_url", "username", "admin_username"], as_dict=True) or {}
    username = row.get("admin_username") or row.get("username") or ""
    if not username:
        return False
    try:
        pooled = frappe.db.count(admin_pool.DOCTYPE, {"enabled": 1})
    except Exception:
        pooled = 0
    if pooled:
        return False
    from pearnly_dms.adapter.session import MrerpDmsAdapter

    login_url, _base = MrerpDmsAdapter._normalize_urls(row.get("system_url") or "")
    return session_cache.recently_kicked(dms_login_key(login_url, username))
