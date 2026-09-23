# -*- coding: utf-8 -*-
"""订车/客户档编辑器（load / save / paints）的**一份请求级权威快照** —— 一次登录取齐。

照搬老产品 `services/erp/dms_edit_snapshot.py`（线上基准 501589f0），模块路径改成新平台。

## 为什么必须有这一层

老产品线上实测（2026-09-13）：编辑页 load 一个请求分别独立登录 DMS 两次（全量主档一次、
选中车型颜色又一次），save 三次（主档 + 颜色 + 客户四级地址标签），editor GET 6–10 秒、
POST save 9.8 秒。每次登录都是完整的 Playwright 会话 + 管理员只读闸。这里把「一个请求
真正要读的权威数据」收进**同一次** `run_logged_in(..., authoritative_read=True)`：

  · 主档 + 五类银行目录（同一次 `fetch_masters` / `fetch_payment_bank_masters`）；
  · 本次请求指定的车型颜色（load 是 qa 已选车型，save 是提交上来的车型）；
  · 客户四级地址标签（有客户资料时：provinces → districts → subdistricts → zipcodes）。

## 契约（调用方按这个读）

  · 只在**当前请求/当前草稿会话**内复用 —— 不读 12h 缓存、不把上次请求的结论当这次的事实；
    调用方每个请求都要重新取一份。
  · 只读：走管理员只读闸，写路径一律不经过本模块。
  · 失败（登录/抓取/半份主档）fail closed：回 None，调用方必须如实报错，不许拿旧缓存或
    销售裁剪视图顶上；某车型颜色这次没读到 → 该车型在 `paints` 里落 None（**读失败 ≠ 没颜色**），
    由调用方按 fail closed 处理该车型，而不把整次请求判成主档不可用。
  · 成功后把这份**本次权威快照**落 `dms_masters_cache`（与旧 force_refresh 同效：主档按 DMS
    现状，本次读到的车型颜色进 `paints_by_car`）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

GEO_FIELDS = (
    ("province_id", "province_name"),
    ("district_id", "district_name"),
    ("subdistrict_id", "subdistrict_name"),
    ("zipcode_id", "zipcode"),
)


def geo_levels(customer: Optional[Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    """客户四级地址标签要读的级联层级（缺上一级 id 的层级不读：读了也是空表）。"""
    if not customer:
        return ()
    province = str(customer.get("province_id") or "")
    district = str(customer.get("district_id") or "")
    subdistrict = str(customer.get("subdistrict_id") or "")
    levels: List[Tuple[str, str]] = [("provinces", "")]
    if province:
        levels.append(("districts", province))
    if district:
        levels.append(("subdistricts", district))
    if subdistrict:
        levels.append(("zipcodes", subdistrict))
    return tuple(levels)


def _plain_car_ids(car_ids: Iterable[Any]) -> Tuple[str, ...]:
    """车型 id 归一成字符串、去空值去重（同一个车型在一次请求里只读一次颜色）。"""
    out: List[str] = []
    for car in car_ids or ():
        value = str(car or "")
        if value and value not in out:
            out.append(value)
    return tuple(out)


def _shared_masters(endpoint: Any) -> Dict[str, Any]:
    """共享热快照里的**完整主档**（车型/颜色/银行/顾问/前缀…）；拿不到回 `{}`。

    失败**不抛**：主档还有"在同一次登录里现场抓全量"这条退路（`_fetch`），
    没必要因为快照不可用把整页判成打不开。
    """
    from pearnly_dms.adapter import master_shared

    try:
        masters = master_shared.get_session_masters(
            endpoint,
            max_age_seconds=master_shared.SESSION_MAX_AGE_SECONDS,
            require_complete=True,
        )
    except Exception:
        logger.warning("[dms editor] 共享主档快照不可用，退回现场读取", exc_info=True)
        return {}
    return dict(masters or {})


def _merge_customer_defaults(
    draft: Optional[Dict[str, Any]], live: Optional[Dict[str, Any]], *,
    preserve_empty_geo: bool = False,
) -> Dict[str, str]:
    """补当前客户资料；客户识别草稿可要求未解析地址及其子级继续留空。"""
    out = {str(key): str(value or "") for key, value in (draft or {}).items()}
    for key, value in (live or {}).items():
        target = "zipcode" if key == "zipcode_name" else str(key)
        if not str(out.get(target) or "").strip():
            out[target] = str(value or "")
    if preserve_empty_geo:
        unresolved = False
        for id_field, name_field in GEO_FIELDS:
            unresolved = unresolved or (
                id_field in (draft or {}) and not str(draft[id_field] or "").strip()
            )
            if unresolved:
                out[id_field] = out[name_field] = ""
    return out


def _read(
    endpoint: Any,
    cars: Tuple[str, ...],
    customer: Optional[Dict[str, Any]],
    customer_id: str,
    *,
    preserve_empty_geo: bool = False,
):
    """一次登录读主档、客户现值、颜色和地址级联；任何权威读取失败都 fail closed。"""

    # **主档走共享热快照**：它由后台预热维持（见 `masters.warm`），命中时 0 秒；
    # 没命中时它会用租约合并成一次刷新，不会让"编辑页 + 订车会话"各登一次。
    # 为什么可以：选项类数据（车型/颜色/银行/顾问/地址）晚 45–90 秒的代价是
    # "名字旧了/提交被拒 → 重选"，而**写之前仍有一次权威复核**；客户现值那部分
    # 依旧实时读，不在这里省。老产品就是"读走快照、写前复核"这个取舍。
    shared = _shared_masters(endpoint)

    def _fetch(client, adapter):
        from pearnly_dms.masters.company_banks import fetch_payment_bank_masters

        if shared:
            masters = dict(shared)
        else:
            # 快照拿不到（没被告知/读失败）→ 保持原样：在同一次登录里现场抓全量。
            masters = {
                **client.fetch_masters(strict=True),
                **fetch_payment_bank_masters(adapter, client=client),
            }
        warmed_paints = dict((shared or {}).get("paints_by_car") or {})
        paints: Dict[str, Optional[List[list]]] = {}
        for car_id in cars:
            # 快照里预热过的颜色直接用；没有才现场读（读失败留 None 交调用方 fail closed）。
            rows = warmed_paints.get(str(car_id))
            if rows is None:
                rows = client._bshsd_all("txtcarpaint", idcar=car_id)
            # None = 这一车型的颜色这次没读到（读失败 ≠ 空表）。留 None 交给调用方 fail
            # closed：别因为一个不该出现的车型 id 把整次请求判成主档不可用，也别把
            # 「没读到」当成「这车没颜色」。
            paints[car_id] = list(rows) if rows is not None else None
        live_customer: Dict[str, Any] = {}
        if customer_id:
            live_customer = dict(client.read_customer(customer_id) or {})
            from pearnly_dms.contracts.client_base import DmsClientError
            from pearnly_dms.contracts.id_validate import normalize_thai_id

            expected = normalize_thai_id(str((customer or {}).get("people_id") or ""))
            actual = normalize_thai_id(str(live_customer.get("people_id") or ""))
            if expected and actual != expected:
                raise DmsClientError(
                    "booking editor customer identity mismatch", "ERR_DMS_CUSTOMER_LOOKUP"
                )
        resolved_customer = _merge_customer_defaults(
            customer, live_customer, preserve_empty_geo=preserve_empty_geo
        )
        geo = {
            level: list(client.list_geo(level, parent) or [])
            for level, parent in geo_levels(resolved_customer)
        }
        return {
            "masters": masters,
            "paints": paints,
            "geo": geo,
            "customer": live_customer,
            "resolved_customer": resolved_customer,
        }

    from pearnly_dms.intake.service import run_logged_in

    return run_logged_in(endpoint, _fetch, authoritative_read=True)


def _store(endpoint: Any, snapshot: Dict[str, Any]) -> None:
    """本次权威快照落主档缓存（写库失败只在缓存层记账，不推翻已取得的快照）。"""
    from pearnly_dms.masters.masters_cache import write_authoritative_snapshot

    paints = snapshot["paints"]
    car_id = next(iter(paints), "")
    try:
        write_authoritative_snapshot(
            endpoint,
            snapshot["masters"],
            car_id=car_id,
            paints=paints.get(car_id) if car_id else None,
        )
    except Exception:
        logger.warning("[dms edit snapshot] master cache write failed", exc_info=True)


def read_edit_snapshot(
    endpoint: Any,
    *,
    car_ids: Iterable[Any] = (),
    customer: Optional[Dict[str, Any]] = None,
    customer_id: Any = "",
    preserve_empty_geo: bool = False,
) -> Optional[Dict[str, Any]]:
    """一次权威登录取齐编辑器要用的主档/银行/颜色/地址级联；失败回 None（fail closed）。

    返回 `{"masters", "paints": {car_id: rows}, "prefixes", "geo", "customer",
    "resolved_customer"}`。
    """
    # Only customer OCR editing opts in. Booking/legacy callers keep their defaults;
    # the protected path hydrates options only below still-resolved parent IDs.
    cars = _plain_car_ids(car_ids)
    result = _read(endpoint, cars, customer, str(customer_id or ""),
                   preserve_empty_geo=preserve_empty_geo)
    if not isinstance(result, dict) or result.get("ok") is False:
        # `run_logged_in` 的 _err dict（dict 但 ok=False）与异常回退路径都算失败。
        # **留一条日志**：返回值必须是 None（fail closed），但"为什么没读到"不能一起吞掉 ——
        # 上层只会看到一句"主档暂时不可用"，排障时没有任何线索（踩过）。
        reason = ""
        if isinstance(result, dict):
            reason = str(result.get("error_code") or "") + " " + str(result.get("raw_error") or "")
        logger.warning(
            "[dms edit snapshot] 读取失败，按不可用处理：%s", (reason or result)[:300]
        )
        return None
    snapshot = {
        "masters": result["masters"],
        "paints": result["paints"],
        "prefixes": list((result["masters"] or {}).get("prefixes") or []),
        "geo": result["geo"],
        "customer": result.get("customer") or {},
        "resolved_customer": result.get("resolved_customer") or {},
    }
    _store(endpoint, snapshot)
    return snapshot


__all__ = ["geo_levels", "read_edit_snapshot"]
