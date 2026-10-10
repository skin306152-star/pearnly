# -*- coding: utf-8 -*-
"""泰国税务局（RD）登记信息查询，多产品共用：13 位税号 → 官方抬头、总公司 / 分店、地址、VAT 登记日。

ERP 录对方档（`pearnly_erp_portal/masters/rd.py`，缓存表 `ERP Tax Lookup Cache`）与 CC 的税号查询工具共用这一套流程，
请求与解析规则在 `contracts.py`。缓存可替换：默认放站点 Redis 7 天（查不到也缓存，免得反复打外部接口）。
超时 5 秒、失败重试 1 次；失败不抛异常，返回 `{"ok": False, "error": ...}`。
"""

from __future__ import annotations

import json
from typing import Callable, Optional

import frappe
from frappe import _

from pearnly_core.revenue_department import contracts

TIMEOUT_SEC = 5
RETRY_TIMES = 1
CACHE_SECONDS = 7 * 86400

normalize_tin = contracts.normalize_tin


class RedisCache:
    """默认缓存：站点 Redis（`frappe.cache`），按税号、分店、服务分键。"""

    @staticmethod
    def _key(tin, branch, service):
        return f"pearnly_rd:{service}:{tin}:{branch}"

    def get(self, tin, branch, service) -> Optional[dict]:
        value = frappe.cache.get_value(self._key(tin, branch, service))
        return json.loads(value) if value else None

    def set(self, tin, branch, service, payload, is_success, error_msg=None) -> None:
        result = {"ok": True, "data": payload} if is_success else {"ok": False, "error": error_msg or "cached_error"}
        frappe.cache.set_value(self._key(tin, branch, service), json.dumps({**result, "cached": True}, ensure_ascii=False),
                               expires_in_sec=CACHE_SECONDS)


def soap_post(url: str, body: str) -> Optional[str]:
    """发 SOAP 1.2 请求；失败返回 None（超时 5 秒，重试 1 次）。"""
    import requests

    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    for _attempt in range(RETRY_TIMES + 1):
        try:
            response = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=TIMEOUT_SEC)
            if response.status_code == 200:
                return response.text
        except Exception:
            pass
    return None


def verify_tin(raw_tax_id, *, cache=None, post: Callable = soap_post) -> dict:
    """13 位税号是否真实存在（TIN Service）。"""
    cache = cache or RedisCache()
    tin = normalize_tin(raw_tax_id)
    if not tin:
        return {"ok": False, "error": "invalid_format"}
    cached = cache.get(tin, 0, "tin")
    if cached:
        return cached
    xml = post(contracts.TIN_ENDPOINT, contracts.TIN_SOAP_TPL.format(tax_id=tin))
    if not xml:
        return {"ok": False, "error": "rd_unreachable"}
    fields = contracts.raw_fields(xml)
    if not fields:
        # 解析不出来就不缓存：下次重试还能拿到真实响应。
        return {"ok": False, "error": "parse_error"}
    data = contracts.tin_payload(fields, tin)
    cache.set(tin, 0, "tin", data, True)
    return {"ok": True, "data": data, "cached": False}


def lookup_vat(raw_tax_id, branch: int = 0, *, cache=None, post: Callable = soap_post) -> dict:
    """VAT 注册主体的官方信息。返回 `{ok, data: {tax_id, name, address, ...}}`。"""
    cache = cache or RedisCache()
    tin = normalize_tin(raw_tax_id)
    if not tin:
        return {"ok": False, "error": "invalid_format"}
    try:
        branch = max(0, int(branch or 0))
    except (TypeError, ValueError):
        branch = 0
    cached = cache.get(tin, branch, "vat")
    if cached:
        return cached
    xml = post(contracts.VAT_ENDPOINT, contracts.VAT_SOAP_TPL.format(tax_id=tin, branch_no=branch))
    if not xml:
        return {"ok": False, "error": "rd_unreachable"}
    fields = contracts.raw_fields(xml)
    if not contracts.pick(fields, "NID", "ID"):
        # 没找到这家（也可能税号没注册 VAT）：缓存「查不到」，免得每次都打接口。
        cache.set(tin, branch, "vat", {}, False, "not_found")
        return {"ok": False, "error": "not_found"}
    data = contracts.vat_payload(fields, tin, _("Head Office"), _("Branch {0}"))
    cache.set(tin, branch, "vat", data, True)
    return {"ok": True, "data": data, "cached": False}


def lookup(raw_tax_id, branch: int = 0, *, cache=None, post: Callable = soap_post) -> dict:
    """查得到就回字段；查不到给出能说人话的原因：税号格式不对 / 没注册 VAT / 税局没有这家 / 外部接口连不上。"""
    if not normalize_tin(raw_tax_id):
        return {"ok": False, "error": "invalid_format"}
    result = lookup_vat(raw_tax_id, branch, cache=cache, post=post)
    if result.get("ok"):
        return {"ok": True, "data": dict(result["data"]), "cached": result.get("cached", False)}
    if result.get("error") != "not_found":
        return result
    # 查不到 VAT 登记时再看税号本身存不存在，好把「没注册 VAT」和「税号不存在」分开说。
    tin = verify_tin(raw_tax_id, cache=cache, post=post)
    if tin.get("ok") and tin["data"].get("exists"):
        return {"ok": False, "error": "not_vat_registered"}
    return {"ok": False, "error": result.get("error") or "not_found"}
