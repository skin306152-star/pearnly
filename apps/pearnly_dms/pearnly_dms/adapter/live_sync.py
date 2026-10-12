# -*- coding: utf-8 -*-
"""白天每分钟看一次 DMS 使用记录，把别人在 DMS 网页上的改动补回缓存（2026-09-27）。

## 为什么

以前增量只在"LINE 这边有人操作"时顺手做（`change_sync.catch_up`）：有人直接在 DMS 网页上
改了车型、客户，而 LINE 这边一直没人操作，缓存就一直旧到凌晨全量。

2026-09-27 在 DMS-TEST 实测：使用记录按模块查条数每次约 0.07 秒，改动当秒可见；登录会话
复用后（`adapter/session_cache`）一轮检查约 2 秒，不再有 6–16 秒的登录。于是白天每分钟
看一眼，有变化才补读被点名的那几条。

## 规则

* 只在白天窗口内跑（`pearnly_dms_live_sync_hours`，默认 `6-22` 即 6:00–22:00，站点时区）；夜里交给全量回读。
* **只用读档账号**（`adapter/admin_pool.read_account`，用户 2026-09-27）：没登记读档账号就不跑，
  绝不拿连接自己的管理员账号每分钟登录 —— 网页上可能有人正用着它，DMS 会把重复登录踢下线。
* 间隔 `pearnly_dms_live_sync_seconds`（默认 60 秒，`0` 关闭）；会话复用关闭时也不跑 ——
  没有复用就等于每分钟登录一次，会一直顶掉同账号的人。
* 走专用 `dms_sync` 队列、只读批次（遇到操作员的交互会话就让路），同一连接同时只跑一个。
* **白天不做大组整组重读**：日志点不出具体记录时，客户/地址两个大组只标记"待重读"，
  由凌晨全量完成；车辆、公司这类小组照常立即整组重读（`change_sync._request_refresh`）。
* 某个模块的使用记录读不懂（对方改版）：只跳过它，其余模块照常；车辆、公司组读不懂满 10 分钟，
  这一轮后台任务改走整组重读兜底，每组每小时至多一次（`change_sync._fallback_refresh`，2026-10-13）。
* 读档账号的会话刚被人顶掉（有人拿它登录了网页）时，接下来一段时间不再为了同步去登录，
  免得每分钟把那个人踢下线一次。
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import frappe

logger = logging.getLogger(__name__)

DEFAULT_SECONDS = 60
DEFAULT_HOURS = "6-22"
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
    return 6, 22


def enabled() -> bool:
    from pearnly_dms.adapter import admin_pool, session_cache

    return interval_seconds() > 0 and session_cache.enabled() and admin_pool.read_account() is not None


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
        # 后台这一轮才做"读不懂太久就整组重读车辆/公司"的兜底（操作员顺手同步不做）。
        change_sync.catch_up(endpoint, client=client, background=True)
        return {"ok": True}

    try:
        result = run_logged_in(endpoint, work, authoritative_read=True, background=True,
                               read_only_batch=True)
    except Exception:
        logger.warning("[dms live sync] round did not finish", exc_info=True)
        return {"ok": False}
    return result if isinstance(result, dict) else {"ok": False}


def _admin_account_in_use(endpoint: str) -> bool:
    """读档账号最近被人顶掉过（有人拿它登录了网页）时，这一轮不去登录。"""
    from pearnly_core.session_lock import dms_login_key
    from pearnly_dms.adapter import admin_pool, session_cache
    from pearnly_dms.adapter.session import MrerpDmsAdapter

    reader = admin_pool.read_account()
    if reader is None:
        return True  # 没有读档账号就不跑（`enabled` 已挡住；这里再兜一次）
    system_url = frappe.db.get_value("Pearnly ERP Endpoint", endpoint, "system_url") or ""
    login_url, _base = MrerpDmsAdapter._normalize_urls(system_url)
    return session_cache.recently_kicked(dms_login_key(login_url, reader.username))
