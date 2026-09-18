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

# 订车付款要用到的另外四类银行目录（线上把它们也算进 `_COMPLETE_KEYS`）。
#
# ⚠️ **暂时不算进"完整"判据** —— 但原因已经变了，别再照旧理由判断：
#
# · 取数**已经搬完**（`dms_company_banks.fetch_payment_bank_masters`，本批搬的）；
# · 卡住的是**权威只读**：这四类目录在销售账号下常被裁成空表，
#   空表会被完整度判据当成"没抓到" → 完整度永远不满足 → 每次读主档都重新登录 DMS
#   → 又变成互踢 + 每步白等几十秒（正是当初延后要躲的那个后果）。
#   线上靠 `_run_logged_in(..., authoritative_read=True)` 借管理员会话读，才敢并进去。
# · 新平台的 `dms_intake.run_logged_in` 现在**还没有** `authoritative_read` 这个开关。
#
# 所以：**下一步把 `authoritative_read` 加上，再把这两处（抓取 + 判据）一次并进去**，
# 不要只并判据 —— 只并判据就是上面那个失败模式。
_PENDING_COMPLETE_KEYS = ("source_banks", "cheque_banks", "cashier_banks", "card_banks")

# 银行类目录的键（与 dms_company_banks 的付款渠道同集）。
_BANK_KEYS = ("company_banks", "source_banks", "cheque_banks", "cashier_banks", "card_banks")


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


def _write(endpoint: Any, masters: Dict[str, Any], *, touch_refreshed_at: bool = True) -> None:
    """写缓存。

    `touch_refreshed_at=False`：**只并入颜色**，不动整体的新鲜度时间戳。
    为什么要有它：抓某个车型的颜色可能会花几秒，而这几秒里别的请求可能刚做完一次
    全量刷新 —— 颜色请求不能把"整体主档"的时间戳往前推，否则别人会以为主档也刚刷过。
    （线上用 SQL 的 jsonb 合并实现；这里用"重读当前 blob 再合并"，语义等价。）
    """
    import frappe

    eid = _eid(endpoint)
    if not eid:
        return
    try:
        if not touch_refreshed_at:
            current = frappe.cache().get_value(_key(eid)) or {}
            cur_masters = current.get("masters") or {}
            merged = dict(cur_masters)
            for k in ("paints_by_car", "paints_refreshed_at"):
                if masters.get(k):
                    merged[k] = {**(cur_masters.get(k) or {}), **masters[k]}
            masters = merged
        frappe.cache().set_value(
            _key(eid),
            {"masters": masters or {}, "ts": time.time()},
            expires_in_sec=CACHE_TTL_SECONDS,
        )
    except Exception:
        logger.warning("[dms masters] 写缓存失败（不影响本次结果）", exc_info=True)


def _write_full_preserving_paints(endpoint: Any, masters: Dict[str, Any]) -> None:
    """整份替换主档，但**不丢掉并发期间拿到的颜色**。

    全量刷新要好几秒；这期间某个颜色请求可能已经跑完。所以不能光用"刷新前读到的那个
    blob"去合并 —— 要在**写入那一刻**重读当前缓存，把它的颜色并进来
    （线上是在同一条 SQL 里合并；这里读-合-写，窗口极小且语义一致）。
    """
    import frappe

    eid = _eid(endpoint)
    if not eid:
        return
    try:
        current = frappe.cache().get_value(_key(eid)) or {}
        cur = current.get("masters") or {}
        pbc = {**(cur.get("paints_by_car") or {}), **(masters.get("paints_by_car") or {})}
        prt = {**(cur.get("paints_refreshed_at") or {}), **(masters.get("paints_refreshed_at") or {})}
        if pbc:
            masters = {**masters, "paints_by_car": pbc, "paints_refreshed_at": prt}
        frappe.cache().set_value(
            _key(eid),
            {"masters": masters or {}, "ts": time.time()},
            expires_in_sec=CACHE_TTL_SECONDS,
        )
    except Exception:
        logger.warning("[dms masters] 整份写缓存失败（不影响本次结果）", exc_info=True)


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


def _fetch_paints_batch_via_login(endpoint: Any, car_ids: List[str]) -> Optional[Dict[str, List[list]]]:
    """一次登录抓**多个**车型的颜色（预热用；逐个抓要登 N 次，会把会话踢来踢去）。"""
    from pearnly_dms.dms_intake import run_logged_in

    want = [str(c) for c in car_ids if c]
    if not want:
        return {}

    def _fetch(client, _adapter):
        return {car: (client._bshsd_all("txtcarpaint", idcar=car) or []) for car in want}

    res = run_logged_in(endpoint, _fetch)
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
    force_refresh: bool = False,
) -> List[list]:
    """某车型的颜色主档（惰性）。已缓存直接回；否则登录抓 + 并入 `paints_by_car` 落缓存。

    传入 `masters`（调用方同请求已读的 blob）则复用，省一次读。

    `force_refresh=True` 时**不认缓存**，按 DMS 现状抓 —— 用于"刚在 DMS 改过颜色、
    马上要按新数据判"的场合；抓不到就按 `require_complete` 决定抛错还是空表（fail closed）。
    """
    car_id = str(car_id or "")
    if masters is None:
        cached = _read(endpoint)
        masters = (cached["masters"] if cached else {}) or {}
    pbc = dict(masters.get("paints_by_car") or {})
    if car_id in pbc and not force_refresh:
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
    _write(
        endpoint,
        {**masters, "paints_by_car": pbc, "paints_refreshed_at": {car_id: time.time()}},
        touch_refreshed_at=False,
    )
    return paints


def write_authoritative_snapshot(
    endpoint: Any,
    masters: Dict[str, Any],
    *,
    car_id: str = "",
    paints: Optional[List[list]] = None,
) -> None:
    """用**本次权威只读复核已取到的**快照落缓存 —— 成功后再二次登录/抓取一律不做。

    旧路径在订车成功后拿手上的会话再抓一次全量主档：**销售会话那一抓会把管理员的完整
    缓存覆盖成销售裁剪视图**（车型/银行看起来变空），还白付一轮远程读取。
    这里只吃调用方已经在提交前抓过的权威行。

    两个容易写错的地方（线上踩过，注释留在那儿）：
      · 颜色：`None` = 这次没读到（该车型旧条目原样保留）；`[]` = **权威结论"这车已无颜色"**
        → 必须清掉旧色。用 falsy 判断会把旧色留下来。
      · 银行类目录：同样只看"这个 key 在不在这次快照里"。存在且为 `[]` 是**权威删除结果**，
        不能被旧值盖回去。
    """
    if not masters:
        return
    cached = _read(endpoint)
    old = (cached or {}).get("masters") or {}
    merged = dict(masters)
    pbc = dict(old.get("paints_by_car") or {})
    prt = dict(old.get("paints_refreshed_at") or {})
    if paints is not None and car_id:
        pbc[str(car_id)] = list(paints)
        prt[str(car_id)] = time.time()
    if pbc or (paints is not None and car_id):
        merged["paints_by_car"] = pbc
        merged["paints_refreshed_at"] = prt
    for key in _BANK_KEYS:
        if key not in merged and old.get(key):
            merged[key] = old[key]
    _write(endpoint, merged)


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
