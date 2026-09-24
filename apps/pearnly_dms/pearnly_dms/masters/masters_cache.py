# -*- coding: utf-8 -*-
"""DMS 下拉主档（顾问/车型/颜色/分店…）的缓存。

旧主档 Redis 辅助缓存保留 12 小时，仅供明确的顾问配置/兼容管理调用；
LINE 读取已改用 snapshots.Directory 的五分钟完整快照，不再调用此处冷抓。
编辑只读新完整快照，最终提交另作实时权威复核。这里的保留期不是业务使用时限。
缓存按 Site、外部系统及实际读取凭据隔离；相同权威读取范围可合并刷新。

**失败语义（照抄，很重要）**：

· 普通读取时登录失败 → 回退**陈旧缓存**（状态诚实优先于报错），都没有才空 dict；
· `force_refresh` 时失败 → **直接空 dict（fail closed）**，绝不把旧主档冒充成刷新过的
  —— 调用方要的就是"现在 DMS 里到底是什么"。
"""

from __future__ import annotations

import logging
import hashlib
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
    "source_banks",
    "cheque_banks",
    "cashier_banks",
    "card_banks",
)

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
    return f"pearnly:dms:masters:{scope(eid)}"


def scope(endpoint: Any) -> str:
    """Share only the same Site, DMS and effective reader credentials.

    Sales without an authoritative reader keep their own permission scope. A
    credential change creates a new cache scope; no password is stored in keys.
    """
    import frappe
    from pearnly_core.endpoints import read_password
    from pearnly_core.session_lock import dms_login_key

    eid = _eid(endpoint)
    if eid.startswith("shared-v2:"):
        return eid
    doc = frappe.get_doc("Pearnly ERP Endpoint", eid)
    if not doc.enabled or doc.adapter != "mrerp_dms":
        return eid
    # 共用读取口：解不开密文只回空串，不会把框架技术细节排进消息队列。
    admin_password = read_password("Pearnly ERP Endpoint", eid, "admin_password")
    use_admin = bool(doc.admin_username and admin_password)
    username = doc.admin_username if use_admin else doc.username
    password = admin_password if use_admin else read_password("Pearnly ERP Endpoint", eid, "password")
    material = dms_login_key(doc.system_url, username) + "\0" + (password or "")
    return "shared-v2:" + hashlib.sha256(material.encode()).hexdigest()


# ── 缓存读写 ─────────────────────────────────────────────────────────────
def _read(endpoint: Any) -> Optional[Dict[str, Any]]:
    """读缓存 → `{"masters": dict, "age_seconds": float}`；缺失/异常 → None（视为 miss）。"""
    import frappe

    eid = _eid(endpoint)
    if not eid:
        return None
    try:
        # Pollers must see another worker's refresh, not Frappe's request-local copy.
        blob = frappe.cache().get_value(_key(eid), use_local_cache=False)
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
    读改写在同一短锁内完成，且只并入本次实际读取的车型。
    """
    import frappe

    eid = _eid(endpoint)
    if not eid:
        return
    try:
        cache = frappe.cache()
        key = _key(eid)
        with cache.lock(cache.make_key(key + ":merge"), timeout=10, blocking_timeout=5):
            stamp = time.time()
            if not touch_refreshed_at:
                current = cache.get_value(key, use_local_cache=False) or {}
                stamp = current.get("ts") or 0
                masters = _merge_paints(current.get("masters") or {}, masters)
            cache.set_value(key, {"masters": masters or {}, "ts": stamp},
                            expires_in_sec=CACHE_TTL_SECONDS)
    except Exception:
        logger.warning("[dms masters] 写缓存失败（不影响本次结果）", exc_info=True)


def _merge_paints(base: dict, incoming: dict) -> dict:
    """Merge per-car results by actual read time, including authoritative empty rows."""
    out = dict(base)
    paints = dict(base.get("paints_by_car") or {})
    stamps = dict(base.get("paints_refreshed_at") or {})
    valid = {str(row[0]) for row in base.get("cars", []) if row}
    for car, rows in (incoming.get("paints_by_car") or {}).items():
        stamp = float((incoming.get("paints_refreshed_at") or {}).get(car) or 0)
        if ("cars" not in base or str(car) in valid) and stamp >= float(stamps.get(car) or 0):
            paints[car], stamps[car] = rows, stamp
    if "cars" in base:
        paints = {key: rows for key, rows in paints.items() if str(key) in valid}
        stamps = {key: value for key, value in stamps.items() if key in paints}
    out.update(paints_by_car=paints, paints_refreshed_at=stamps)
    return out


def _write_full_preserving_paints(endpoint: Any, masters: Dict[str, Any]) -> Optional[dict]:
    """整份替换主档，但**不丢掉并发期间拿到的颜色**。

    全量刷新要好几秒；这期间某个颜色请求可能已经跑完。所以不能光用"刷新前读到的那个
    blob"去合并 —— 要在**写入那一刻**重读当前缓存，把它的颜色并进来
    在短 Redis 锁内合并，以单车型读取时间决定新旧，删除车型不复活旧颜色。
    """
    import frappe

    eid = _eid(endpoint)
    if not eid:
        return
    try:
        cache = frappe.cache()
        key = _key(eid)
        with cache.lock(cache.make_key(key + ":merge"), timeout=10, blocking_timeout=5):
            current = cache.get_value(key, use_local_cache=False) or {}
            masters = _merge_paints(masters, current.get("masters") or {})
            cache.set_value(key, {"masters": masters, "ts": time.time()},
                            expires_in_sec=CACHE_TTL_SECONDS)
            return masters
    except Exception:
        logger.warning("[dms masters] 整份写缓存失败（不影响本次结果）", exc_info=True)


# ── 登录抓取（失败即软回退） ──────────────────────────────────────────────
def _fetch_masters_via_login(endpoint: Any, *, require_complete: bool = False,
                             background: bool = False):
    """登录 DMS 抓全量主档；登录/抓取失败（`run_logged_in` 回 err dict）→ None。

    走**权威只读**：配了独立管理员凭据组的租户借管理员会话读 —— 销售账号常看不到
    车型/银行全表，拿不完整视图当"主档是空的"会静默把存在的车/银行判成不存在；
    而五类银行目录现在也算进完整度判据，看不到就永远不满足、每次重读都要重登。
    管理员登录失败 → `ERR_DMS_ADMIN_AUTH` 落成 None，由调用方 fail closed，不静默退回销售。

    `background=True`：这次是后台任务在刷（没人等着看结果）→ 用**共用预算口径**里
    后台那一档（一次给足 90 秒，不走用户在等的 30/60/90 阶梯），见
    `pearnly_dms.config.pacing`。
    """
    from pearnly_dms.masters.company_banks import fetch_payment_bank_masters
    from pearnly_dms.adapter.operations import run_logged_in

    def _fetch(client, adapter):
        return {
            **client.fetch_masters(strict=require_complete),
            **fetch_payment_bank_masters(adapter, client=client),
        }

    res = run_logged_in(endpoint, _fetch, authoritative_read=True, background=background,
                        catch_up=not background)
    if isinstance(res, dict) and res.get("ok") is False:
        # **把失败原因留在日志里**：这一段只返回 None，光看"没拿到完整主档"分不清
        # "连不上"、"管理员凭据不对"还是"某个下拉读不出来"——2026-09-23 排查时
        # 就是被这句话挡住了（48 小时 2492 次刷新全失败，却看不出为什么）。
        logger.warning("[dms masters] 全量刷新失败 code=%s raw=%s",
                       res.get("error_code"), str(res.get("raw_error") or "")[:200])
        return None
    return res


def _fetch_paints_via_login(endpoint: Any, car_id: str) -> Optional[List[list]]:
    """登录 DMS 抓某车型的颜色（**翻页取全** —— 只取第一页会漏掉第 2 页起的颜色）；失败 → None。

    同理走**权威只读**：颜色主档在销售账号下可能被裁成空表，空表会被误判成"这车没颜色"。
    """
    from pearnly_dms.adapter.operations import run_logged_in

    res = run_logged_in(
        endpoint,
        lambda cl, ad: cl._bshsd_all("txtcarpaint", idcar=car_id),
        authoritative_read=True,
        catch_up=True,
    )
    return res if isinstance(res, list) else None


def _fetch_paints_batch_via_login(endpoint: Any, car_ids: List[str],
                                  *, background: bool = False) -> Optional[Dict[str, List[list]]]:
    """一次登录抓**多个**车型的颜色（预热用；逐个抓要登 N 次，会把会话踢来踢去）。

    `background=True`：后台预热在跑（没人等着）→ 预算走共用口径的后台那一档。
    """
    from pearnly_dms.adapter.operations import run_logged_in

    want = [str(c) for c in car_ids if c]
    if not want:
        return {}

    def _fetch(client, _adapter):
        return {car: (client._bshsd_all("txtcarpaint", idcar=car) or []) for car in want}

    res = run_logged_in(endpoint, _fetch, authoritative_read=True, background=background)
    return res if isinstance(res, dict) and res.get("ok") is not False else None


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
    # 判据要求**完整的**键集都在（含五类银行目录）—— 缺任何一类就当缓存不可用，
    # 否则会把一份残缺的主档当成"已经取全"，而完整度判据又要它完整，来回打架。
    cache_usable = bool(cached) and all(key in cached["masters"] for key in _COMPLETE_KEYS)
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
            from pearnly_dms.contracts.client_base import DmsClientError

            raise DmsClientError(
                f"DMS paint master unavailable for car {car_id!r}",
                "ERR_DMS_MASTER_UNAVAILABLE",
            )
        return []
    _write(
        endpoint,
        {"paints_by_car": {car_id: paints}, "paints_refreshed_at": {car_id: time.time()}},
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
    _write_full_preserving_paints(endpoint, merged)


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
        # 就地刷新走的是**销售会话**的 `fetch_masters()`，不含任何银行目录。
        # 不把它们从旧缓存带过来，缓存就会瞬间缺五类键 → 判据判不可用 → 下次又重登。
        for key in _BANK_KEYS:
            if key not in masters and old.get(key):
                masters = {**masters, key: old[key]}
    _write(endpoint, masters)
