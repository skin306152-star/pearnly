# -*- coding: utf-8 -*-
"""DMS 下拉主档（顾问/车型/颜色/分店…）的缓存。

为什么缓存：**每次登录 DMS 抓全量太贵**（一次登录要十几秒，主档还有好几页）。
缓存 12 小时内直接复用。

和老产品的两处实现差异（逻辑一样）：

· 老产品存 Postgres 表；我们存 Frappe 的 Redis 缓存 —— 它本来就是缓存，
  过期/被逐出都只是"下次重新抓"，不存在数据正确性问题；
· 端点的键用**文档名**（我们是 Frappe，「一个端点 = 一个文档」）。

**失败语义（照抄，很重要）**：

· 普通读取时登录失败 → 回退**陈旧缓存**（状态诚实优先于报错），都没有才空 dict；
· `force_refresh` 时失败 → **直接空 dict（fail closed）**，绝不把旧主档冒充成刷新过的
  —— 调用方要的就是"现在 DMS 里到底是什么"。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 12 * 3600
_COMPLETE_KEYS = (
    "cars",
    "place_books",
    "term_sales",
    "branches",
    "regis_behalfs",
    "advisors",
    "company_banks",
)


def _eid(endpoint: Any) -> str:
    """端点标识：收字符串（文档名）/ dict / Frappe 文档都行。"""
    if isinstance(endpoint, str):
        return endpoint
    if isinstance(endpoint, dict):
        return str(endpoint.get("id") or endpoint.get("name") or "")
    return str(getattr(endpoint, "name", "") or "")


def _key(eid: str) -> str:
    return f"pearnly:dms:masters:{eid}"


# ── 缓存读写 ─────────────────────────────────────────────────────────────
def _read(endpoint: Any) -> Optional[Dict[str, Any]]:
    """读缓存 → `{"masters": dict, "age_seconds": float}`；缺失/异常 → None（视为 miss）。"""
    import frappe

    eid = _eid(endpoint)
    if not eid:
        return None
    try:
        blob = frappe.cache().get_value(_key(eid))
    except Exception:
        logger.warning("[dms masters] 读缓存失败，按 miss 处理", exc_info=True)
        return None
    if not isinstance(blob, dict):
        return None
    masters = blob.get("masters")
    if not isinstance(masters, dict):
        return None
    return {"masters": masters, "age_seconds": max(0.0, time.time() - float(blob.get("ts") or 0))}


def _write(endpoint: Any, masters: Dict[str, Any]) -> None:
    import frappe

    eid = _eid(endpoint)
    if not eid:
        return
    try:
        frappe.cache().set_value(
            _key(eid),
            {"masters": masters or {}, "ts": time.time()},
            expires_in_sec=CACHE_TTL_SECONDS,
        )
    except Exception:
        logger.warning("[dms masters] 写缓存失败（不影响本次结果）", exc_info=True)


# ── 登录抓取（失败即软回退） ──────────────────────────────────────────────
def _fetch_masters_via_login(endpoint: Any, *, require_complete: bool = False):
    """登录 DMS 抓全量主档；登录/抓取失败（`run_logged_in` 回 err dict）→ None。"""
    from pearnly_dms.dms_company_banks import fetch_company_banks
    from pearnly_dms.dms_intake import run_logged_in

    def _fetch(client, adapter):
        return {
            **client.fetch_masters(strict=require_complete),
            "company_banks": fetch_company_banks(adapter),
        }

    res = run_logged_in(endpoint, _fetch)
    if isinstance(res, dict) and res.get("ok") is False:
        return None
    return res


def _fetch_paints_via_login(endpoint: Any, car_id: str) -> Optional[List[list]]:
    """登录 DMS 抓某车型的颜色（**翻页取全** —— 只取第一页会漏掉第 2 页起的颜色）；失败 → None。"""
    from pearnly_dms.dms_intake import run_logged_in

    res = run_logged_in(endpoint, lambda cl, ad: cl._bshsd_all("txtcarpaint", idcar=car_id))
    if isinstance(res, dict):
        return None
    return res


# ── 对外：主档 / 颜色 ────────────────────────────────────────────────────
def get_masters(
    endpoint: Any, *, force_refresh: bool = False, require_complete: bool = False
) -> Dict[str, Any]:
    """主档：缓存 <12h 直接回；过期/缺失 → 登录抓全量 + 落缓存。

    普通读取登录失败时回退陈旧缓存（**状态诚实优先于报错**），都没有则空 dict；
    `force_refresh` 失败则直接空 dict，避免把旧主档冒充成实时数据。

    普通刷新保留已惰性缓存的 `paints_by_car`；**强制刷新则按 DMS 现状清掉旧颜色缓存**
    （否则 DMS 新增/删除的颜色会被旧色遮住）。
    """
    cached = _read(endpoint)
    cache_usable = bool(cached) and "company_banks" in cached["masters"]
    if require_complete and cache_usable:
        cache_usable = all(isinstance(cached["masters"].get(key), list) for key in _COMPLETE_KEYS)
    if not force_refresh and cache_usable and cached["age_seconds"] < CACHE_TTL_SECONDS:
        return cached["masters"]
    fresh = _fetch_masters_via_login(endpoint, require_complete=require_complete)
    if fresh is None:
        if force_refresh:
            return {}
        return cached["masters"] if cached else {}
    if not force_refresh and cached:
        pbc = (cached["masters"] or {}).get("paints_by_car")
        if pbc:
            fresh = {**fresh, "paints_by_car": pbc}
    _write(endpoint, fresh)
    return fresh


def read_fresh_masters(endpoint: Any) -> Optional[Dict[str, Any]]:
    """**只读**缓存主档（<12h），绝不触发登录冷抓。miss / 过期 → None。

    提交校验用它：能被提交的选项必来自已暖的缓存；请求内再冷抓 = 多余的二次登录
    → 宁可 None 让调用方重开选择流程。
    """
    cached = _read(endpoint)
    if cached and cached["age_seconds"] < CACHE_TTL_SECONDS:
        return cached["masters"]
    return None


def get_paints(
    endpoint: Any,
    car_id: str,
    masters: Optional[Dict[str, Any]] = None,
    *,
    require_complete: bool = False,
) -> List[list]:
    """某车型的颜色主档（惰性）。已缓存直接回；否则登录抓 + 并入 `paints_by_car` 落缓存。

    传入 `masters`（调用方同请求已读的 blob）则复用，省一次读。
    """
    car_id = str(car_id or "")
    if masters is None:
        cached = _read(endpoint)
        masters = (cached["masters"] if cached else {}) or {}
    pbc = dict(masters.get("paints_by_car") or {})
    if car_id in pbc:
        return pbc[car_id]
    paints = _fetch_paints_via_login(endpoint, car_id)
    if paints is None:
        if require_complete:
            from pearnly_dms.dms_client_base import DmsClientError

            raise DmsClientError(
                f"DMS paint master unavailable for car {car_id!r}",
                "ERR_DMS_MASTER_UNAVAILABLE",
            )
        return []
    pbc[car_id] = paints
    _write(endpoint, {**masters, "paints_by_car": pbc})
    return paints


def refresh_from_client(endpoint: Any, client: Any) -> None:
    """订车成功后就地全量刷主档（会话已活，**零额外登录**）。保留 `paints_by_car`。"""
    try:
        masters = client.fetch_masters()
    except Exception:
        logger.warning("[dms masters] 就地刷新失败（不影响本次结果）", exc_info=True)
        return
    cached = _read(endpoint)
    if cached:
        old = cached["masters"] or {}
        pbc = old.get("paints_by_car")
        if pbc:
            masters = {**masters, "paints_by_car": pbc}
        if "company_banks" in old:
            masters = {**masters, "company_banks": old["company_banks"]}
    _write(endpoint, masters)
