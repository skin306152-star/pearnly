# -*- coding: utf-8 -*-
"""DMS 主档的**有界新鲜共享快照**：一个请求去刷，并发请求复用那次结果。

## 为什么必须有这一层（不是"优化"，是防故障）

DMS **没有版本号接口**，而且**同一个账号只允许一个活跃会话** —— 两个人同时登录会互相
踢下线。老产品的订车逐问每一步都要读主档，如果每步都登录一次：

1. 两个销售同时操作 → 互相把对方踢掉，双方都失败；
2. 一次订车要读十几个主档 → 十几轮登录，慢且脆。

老产品的解法就是这一层：**影子快照 + 刷新租约**。快照有界新鲜（`SESSION_MAX_AGE_SECONDS`），
刷新的那个请求拿租约、其余人等结果复用；**写之前**仍然做一次权威实时复核（那条路在
`masters_cache.write_authoritative_snapshot`，不受这里控制）。

## 与老产品的实现差异（语义一致，机制换了）

老产品把租约存在 Postgres 表 `dms_master_refresh_locks` 里；新平台用 **Redis 的
`SET NX PX`** —— 它天生就是"带过期的原子抢占"，不用建表、不用清理死租约
（进程崩了租约到点自动消失）。Frappe 的 cache key 本来就按站点隔离，
而新平台**一个站点就是一家租户**，租户边界不用再自己算。

## 新鲜度口径（照线上，不是我之前写的 12 小时）

| 用途 | 上限 |
|---|---|
| 逐问过程中的会话快照 | 90 秒 |
| 后台预热颜色 | 45 秒 |
| 单个车型颜色 | 90 秒 |
| 等别人刷新的耐心 | 45 秒 |
| 一个租约最长持有时长 | 150 秒 |

**空读 / 不完整 / 失败，一律不覆盖上一份好快照**（宁可短暂用旧的，也不要把主档写坏）。
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SESSION_MAX_AGE_SECONDS = int(os.environ.get("DMS_MASTER_SESSION_MAX_AGE_SECONDS", "90"))
BACKGROUND_MAX_AGE_SECONDS = int(os.environ.get("DMS_MASTER_BACKGROUND_MAX_AGE_SECONDS", "45"))
PAINT_MAX_AGE_SECONDS = int(os.environ.get("DMS_PAINT_SESSION_MAX_AGE_SECONDS", "90"))
REFRESH_WAIT_SECONDS = int(os.environ.get("DMS_MASTER_REFRESH_WAIT_SECONDS", "45"))
REFRESH_LEASE_SECONDS = int(os.environ.get("DMS_MASTER_REFRESH_LEASE_SECONDS", "150"))
PAINT_WARM_LIMIT = int(os.environ.get("DMS_PAINT_WARM_LIMIT", "8"))

_LEASE_PREFIX = "pearnly:dms:lease:"


def _scope(endpoint: Any) -> str:
    """缓存/租约的作用域 = 本站在控制面里的这条 DMS 连接。"""
    from pearnly_dms.dms_masters_cache import _eid

    return _eid(endpoint)


# ── 租约（Redis SET NX PX）──────────────────────────────────────────────────


def _acquire(scope_key: str, lease_seconds: int = REFRESH_LEASE_SECONDS) -> Optional[str]:
    """抢占租约。拿到返回 owner 令牌，没拿到返回 None（说明别人正在刷）。"""
    import frappe

    owner = str(uuid.uuid4())
    try:
        ok = frappe.cache().set(
            _LEASE_PREFIX + scope_key, owner, nx=True, px=max(1, int(lease_seconds)) * 1000
        )
    except Exception:
        logger.warning("[dms masters] 抢租约失败", exc_info=True)
        return None
    return owner if ok else None


def _release(scope_key: str, owner: str) -> None:
    """释放租约。**只释放自己的那把**（别人的租约不能删）。"""
    import frappe

    try:
        cur = frappe.cache().get(_LEASE_PREFIX + scope_key)
        # ⚠️ Redis 拿回来是 **bytes**，直接跟 str 比永远不等 ——
        # 那样租约就永远删不掉，后面的请求全得白等 45 秒再超时（实测踩到）。
        if isinstance(cur, (bytes, bytearray)):
            cur = cur.decode("utf-8", "ignore")
        if cur is not None and str(cur) != str(owner):
            return
        frappe.cache().delete(_LEASE_PREFIX + scope_key)
    except Exception:
        logger.warning("[dms masters] 释放租约失败（到点会自己过期）", exc_info=True)


# ── 新鲜度判据 ──────────────────────────────────────────────────────────────


def _complete(cached: Optional[dict], require_complete: bool) -> bool:
    if not cached or not isinstance(cached.get("masters"), dict):
        return False
    if not require_complete:
        return True
    from pearnly_dms import dms_masters_cache as cache

    masters = cached["masters"]
    return all(isinstance(masters.get(key), list) for key in cache._COMPLETE_KEYS)


def _fresh(cached: Optional[dict], max_age_seconds: float, require_complete: bool) -> bool:
    return _complete(cached, require_complete) and float(cached.get("age_seconds") or 0) <= float(
        max_age_seconds
    )


def needs_refresh(endpoint: Any, max_age_seconds: float = SESSION_MAX_AGE_SECONDS) -> bool:
    from pearnly_dms import dms_masters_cache as cache

    return not _fresh(cache._read(endpoint), max_age_seconds, True)


def _wait_for_fresh(scope_key: str, max_age_seconds: float, require_complete: bool) -> dict:
    """别人正在刷 —— 等他出结果复用，不自己再登一次（那正是互踢的来源）。"""
    from pearnly_dms import dms_masters_cache as cache

    deadline = time.monotonic() + REFRESH_WAIT_SECONDS
    while time.monotonic() < deadline:
        cached = cache._read(scope_key)
        if _fresh(cached, max_age_seconds, require_complete):
            return cached["masters"]
        time.sleep(0.25)
    return {}


def _preserve_paints(fresh: dict, cached: Optional[dict]) -> dict:
    """全量刷新时保留颜色，但**只保留这台 DMS 还认得的车型**的颜色。

    车型被删了还留着旧颜色，会让"下拉里没有这车、却能提交它的颜色"这种事发生。
    """
    old = (cached or {}).get("masters") or {}
    old_paints = old.get("paints_by_car") or {}
    old_times = old.get("paints_refreshed_at") or {}
    valid_cars = {str(row[0]) for row in (fresh.get("cars") or []) if row}
    paints = {str(k): v for k, v in old_paints.items() if str(k) in valid_cars}
    times = {str(k): v for k, v in old_times.items() if str(k) in paints}
    if paints:
        fresh = {**fresh, "paints_by_car": paints, "paints_refreshed_at": times}
    return fresh


# ── 会话快照 ────────────────────────────────────────────────────────────────


def get_session_masters(
    endpoint: Any,
    *,
    max_age_seconds: float = SESSION_MAX_AGE_SECONDS,
    require_complete: bool = True,
) -> Dict[str, Any]:
    """有界新鲜的**完整**主档；并发的刷新会被合并成一次真实登录。"""
    from pearnly_dms import dms_masters_cache as cache

    scope_key = _scope(endpoint)
    cached = cache._read(scope_key)
    if _fresh(cached, max_age_seconds, require_complete):
        return cached["masters"]

    owner = _acquire(scope_key)
    if not owner:
        return _wait_for_fresh(scope_key, max_age_seconds, require_complete)
    try:
        # 等租约的这段时间里，前一个刷新的可能已经写好了
        cached = cache._read(scope_key)
        if _fresh(cached, max_age_seconds, require_complete):
            return cached["masters"]
        fresh = cache._fetch_masters_via_login(endpoint, require_complete=require_complete)
        if not _complete({"masters": fresh}, require_complete):
            # 空读/残缺**绝不覆盖**上一份好快照 —— 宁可这次返回空让调用方重试
            logger.warning("[dms masters] 刷新结果不完整，缓存保持不动")
            return {}
        fresh = _preserve_paints(dict(fresh), cached)
        cache._write_full_preserving_paints(scope_key, fresh)
        return fresh
    finally:
        _release(scope_key, owner)


# ── 颜色 ────────────────────────────────────────────────────────────────────


def _paint_fresh(masters: dict, car_id: str, max_age_seconds: float) -> bool:
    paints = masters.get("paints_by_car") or {}
    stamp = (masters.get("paints_refreshed_at") or {}).get(car_id)
    try:
        age = time.time() - float(stamp)
    except (TypeError, ValueError):
        return False
    return car_id in paints and age <= float(max_age_seconds)


def _paint_result(scope_key: str, car_id: str, max_age_seconds: float) -> Optional[list]:
    from pearnly_dms import dms_masters_cache as cache

    cached = cache._read(scope_key)
    masters = (cached or {}).get("masters") or {}
    if _paint_fresh(masters, car_id, max_age_seconds):
        return list((masters.get("paints_by_car") or {})[car_id])
    return None


def get_session_paints(
    endpoint: Any,
    car_id: str,
    *,
    max_age_seconds: float = PAINT_MAX_AGE_SECONDS,
    require_complete: bool = True,
) -> list:
    """某车型的有界新鲜颜色，跨并发用户共享。"""
    from pearnly_dms import dms_masters_cache as cache
    from pearnly_dms.dms_client_base import DmsClientError

    car_id = str(car_id or "")
    if not car_id:
        return []
    scope_key = _scope(endpoint)
    existing = _paint_result(scope_key, car_id, max_age_seconds)
    if existing is not None:
        return existing

    owner = _acquire(scope_key + ":paint:" + car_id)
    if not owner:
        # 后台预热或另一个操作员正在刷这个车型 —— 复用上一份完整结果，
        # 不让 LINE 的回复卡在 DMS 会话上（提交前那次权威复核仍然会实时读）。
        cached = cache._read(scope_key)
        stale = (((cached or {}).get("masters") or {}).get("paints_by_car") or {}).get(car_id)
        if isinstance(stale, list):
            return list(stale)
        deadline = time.monotonic() + REFRESH_WAIT_SECONDS
        while time.monotonic() < deadline:
            existing = _paint_result(scope_key, car_id, max_age_seconds)
            if existing is not None:
                return existing
            time.sleep(0.25)
        if require_complete:
            raise DmsClientError("DMS paint refresh timed out", "ERR_DMS_MASTER_UNAVAILABLE")
        return []
    try:
        existing = _paint_result(scope_key, car_id, max_age_seconds)
        if existing is not None:
            return existing
        rows = cache._fetch_paints_via_login(endpoint, car_id)
        if rows is None:
            if require_complete:
                raise DmsClientError(
                    f"DMS paint master unavailable for car {car_id!r}",
                    "ERR_DMS_MASTER_UNAVAILABLE",
                )
            return []
        cache._write(
            scope_key,
            {"paints_by_car": {car_id: list(rows)}, "paints_refreshed_at": {car_id: time.time()}},
            touch_refreshed_at=False,
        )
        return list(rows)
    finally:
        _release(scope_key + ":paint:" + car_id, owner)


def warm_cached_paints(
    endpoint: Any, *, max_age_seconds: float = BACKGROUND_MAX_AGE_SECONDS
) -> int:
    """在 LINE 请求路径**之外**预热最近用过的颜色（只预热用户选过的车型，有上限）。

    每个车型单独持租约，避免和交互式刷新打架；一次登录服务整批。
    """
    from pearnly_dms import dms_masters_cache as cache

    scope_key = _scope(endpoint)
    cached = cache._read(scope_key)
    masters = (cached or {}).get("masters") or {}
    paints = masters.get("paints_by_car") or {}
    stamps = masters.get("paints_refreshed_at") or {}
    candidates = []
    for car_id in paints:
        try:
            age = time.time() - float(stamps.get(car_id))
        except (TypeError, ValueError):
            age = float("inf")
        if age > float(max_age_seconds):
            candidates.append((float(stamps.get(car_id) or 0), str(car_id)))
    candidates.sort(reverse=True)

    leases = []
    for _stamp, car_id in candidates[: max(0, PAINT_WARM_LIMIT)]:
        owner = _acquire(scope_key + ":paint:" + car_id)
        if owner:
            leases.append((car_id, owner))
    if not leases:
        return 0
    try:
        rows_by_car = cache._fetch_paints_batch_via_login(
            endpoint, [car_id for car_id, _o in leases]
        )
        if not rows_by_car:
            return 0
        now = time.time()
        cache._write(
            scope_key,
            {
                "paints_by_car": {c: rows_by_car.get(c, []) for c, _o in leases},
                "paints_refreshed_at": {c: now for c, _o in leases},
            },
            touch_refreshed_at=False,
        )
        return len(leases)
    finally:
        for car_id, owner in leases:
            _release(scope_key + ":paint:" + car_id, owner)
