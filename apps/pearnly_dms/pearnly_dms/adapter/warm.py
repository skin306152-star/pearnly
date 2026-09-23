# -*- coding: utf-8 -*-
"""主档**后台预热**：让第一个点进来的人不用等一次全量登录。

## 为什么要它（不是优化，是"第一下别卡"）

订车逐问和浏览器编辑页都依赖 DMS 的完整主档。没有预热时，用户点第一下就要现场
登录 + 翻页抓全量（实测这条权威读要 **12.3 秒**），而且每个新会话都可能重来一次。
老产品的做法是：**每分钟检查一次新鲜度，超过 45 秒才派发一次刷新**，于是用户点到
的时候快照通常还是热的（会话内 90 秒口径直接命中）。

端口是移植时漏掉的那一块：`adapter/master_shared.py` 里的 `needs_refresh()` /
`warm_cached_paints()` 都在，但**全仓没有任何地方调用**，scheduler 里也没有这一项 ——
表现就是"每次都是冷启动"（2026-09-21 实测编辑页 12.3 秒，订车会话快照热时 0.0 秒）。

## 与老产品的差异

老产品是"多 OA 一个库"，要按租户把 endpoint 去重、只刷**共享范围**（配了管理员凭据组
的那条），免得每个销售的账号各刷一份。新平台一站一租户一条连接，范围天然唯一；
这里保留"只刷共享范围"这条判据：没配管理员凭据的租户保持原样（不额外登录）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

ENDPOINT_DOCTYPE = "Pearnly ERP Endpoint"


def _endpoints() -> List[str]:
    """本站点启用的 mrerp_dms 连接名。"""
    import frappe

    return frappe.get_all(
        ENDPOINT_DOCTYPE,
        filters={"adapter": "mrerp_dms", "enabled": 1},
        pluck="name",
        limit_page_length=20,
    ) or []


def _is_shared(endpoint: str) -> bool:
    """配了独立管理员凭据组 = 共享范围（照老产品 `is_shared_scope` 的判据）。"""
    from pearnly_dms.masters import masters_cache

    try:
        return str(masters_cache.scope(endpoint)).startswith("shared-v2:")
    except Exception:
        logger.warning("[dms warm] 判共享范围失败，跳过 %s", endpoint, exc_info=True)
        return False


def sweep() -> Dict[str, Any]:
    """每分钟被 scheduler 调用：只对"真该刷的共享范围"派发一次刷新。**不在这里登录。**

    三道闸（2026-09-23 加，原来是"过期就派发"）：

    1. **熔断期不派发** —— 连着几次连不上时，派出去的任务也是白跑，还占着长队列 worker
       一两分钟（生产实测每次 17–58 秒）；冷却期由 `pacing` 统一算。
    2. **没人用就降频** —— 最近 30 分钟没人碰过这条连接时，主档放到 10 分钟才刷一次。
       半夜每分钟敲对方一次没有任何意义，对方还慢。
    3. 过期才派发（原有判据不变）。
    """
    import frappe

    from pearnly_dms.adapter import master_shared
    from pearnly_dms.config import pacing
    from pearnly_dms.masters import masters_cache as cache

    checked = queued = cooling = idle = 0
    for endpoint in _endpoints():
        if not _is_shared(endpoint):
            continue
        checked += 1
        if pacing.cooldown_left(endpoint):
            cooling += 1
            continue
        try:
            cached = cache._read(endpoint)
            age = float((cached or {}).get("age_seconds") or float("inf"))
            if not pacing.should_warm(pacing.used_recently(endpoint),
                                      background_max_age=master_shared.BACKGROUND_MAX_AGE_SECONDS,
                                      age_seconds=age):
                idle += 1
                continue
        except Exception:
            logger.warning("[dms warm] 新鲜度检查失败 %s", endpoint, exc_info=True)
            continue
        try:
            frappe.enqueue(
                "pearnly_dms.adapter.warm.refresh",
                endpoint=endpoint,
                queue="long",
                timeout=900,
                # 同一范围同一分钟只排一次：scheduler 抖动/重放不会登两次。
                job_id=f"dms-master-warm-{endpoint}-{frappe.utils.now_datetime():%Y%m%d%H%M}",
                deduplicate=True,
                enqueue_after_commit=True,
            )
            queued += 1
        except Exception:
            logger.warning("[dms warm] 派发失败 %s", endpoint, exc_info=True)
    if checked or queued or cooling:
        logger.info("[dms warm] checked=%s queued=%s cooling=%s idle=%s",
                    checked, queued, cooling, idle)
    return {"checked": checked, "queued": queued, "cooling": cooling, "idle": idle}


def refresh(endpoint: str) -> Dict[str, Any]:
    """后台刷新一次：完整主档 + 颜色预热。失败只记日志（下一次 sweep 会再来）。

    预算走**共用口径的后台那一档**（90 秒）—— 这是后台任务、没人等着看结果，
    没有理由跟着用户那条 30/60/90 的阶梯走；老默认的 15 秒在这里必然超时
    （2026-09-23：48 小时里 2492 次刷新、成功 0 次，全卡在打开登录页）。
    """
    from pearnly_dms.adapter import master_shared
    from pearnly_dms.config import pacing

    masters = master_shared.get_session_masters(
        endpoint,
        max_age_seconds=master_shared.BACKGROUND_MAX_AGE_SECONDS,
        require_complete=True,
        background=True,
    )
    if not masters:
        state = pacing.summary(endpoint)
        logger.warning("[dms warm] 刷新没拿到完整主档 %s（连续失败 %s 次，冷却剩 %s 秒，最近错误 %s）",
                       endpoint, state.get("failures"), state.get("cooldown_left"),
                       state.get("last_code"))
        return {"status": "incomplete", **state}
    warmed = 0
    try:
        warmed = master_shared.warm_cached_paints(
            endpoint, max_age_seconds=master_shared.BACKGROUND_MAX_AGE_SECONDS,
            background=True,
        )
    except Exception:
        logger.warning("[dms warm] 颜色预热失败 %s", endpoint, exc_info=True)
    logger.info("[dms warm] refreshed %s paints_warmed=%s", endpoint, warmed)
    return {"status": "fresh", "paints_warmed": warmed}


__all__ = ["sweep", "refresh"]
