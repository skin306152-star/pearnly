# -*- coding: utf-8 -*-
"""泰国税务局（RD）登记信息查询：填了税号点一下，把官方抬头与地址带出来。

从旧项目搬过来的能力（`services/rd/rd_api.py`，生产 SHA `ec2e5c957e6b`），
请求形状与解析口径照它，落点换成本仓原生的缓存 DocType：

· **TIN Service**：13 位税号是否真实存在（`checktinpinservice.asmx`）；
· **VAT Service**：VAT 注册主体的官方信息（`vatserviceRD3.asmx`）—— 抬头、地址、分支、邮编、省、VAT 首次登记日；
· **缓存 7 天**（`ERP Tax Lookup Cache`），命中就不再打外部接口；
· 超时 5 秒、失败重试 1 次；**失败不抛异常**，返回 `{"ok": False, "error": ...}`，
  录入与识别照常走（外部接口挂掉不该拦住开单）。

与旧项目的一处**有意差异**：旧解析表只认 `vProvinceName` / `vThumbolName` 这类带 Name 的标签，
而实测线上返回的是 `vProvince` / `vThambol` / `vAmphur`（见 `tests/test_erp_tax_lookup.py` 里
抓下来的真实响应），所以这里按**别名**取值，两种形状都能读出来 —— 否则地址会缺省/缺区。
"""

from __future__ import annotations

import json
from typing import Any, Optional

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

from pearnly_erp_portal.masters import rd_contracts as contracts

CACHE = "ERP Tax Lookup Cache"

TIMEOUT_SEC = 5
RETRY_TIMES = 1
CACHE_DAYS = 7

normalize_tin = contracts.normalize_tin


# ── 缓存 ────────────────────────────────────────────────────────────────────


def _cache_key(tax_id: str, branch: int, service: str) -> str:
    return f"TAX-{tax_id}-{branch}-{service}"


def _cache_get(tax_id: str, branch: int, service: str) -> Optional[dict]:
    name = _cache_key(tax_id, branch, service)
    row = frappe.db.get_value(CACHE, name, ["payload", "is_success", "error_msg", "expires_at"],
                             as_dict=True)
    if not row or not row.expires_at or row.expires_at <= now_datetime():
        return None
    if row.is_success:
        return {"ok": True, "data": json.loads(row.payload or "{}"), "cached": True}
    return {"ok": False, "error": row.error_msg or "cached_error", "cached": True}


def _cache_set(tax_id: str, branch: int, service: str, payload: Any,
               is_success: bool, error_msg: Optional[str] = None) -> None:
    name = _cache_key(tax_id, branch, service)
    values = {"doctype": CACHE, "tax_id": tax_id, "branch_no": branch, "service": service,
              "is_success": 1 if is_success else 0, "error_msg": error_msg,
              "fetched_at": now_datetime(), "expires_at": add_days(now_datetime(), CACHE_DAYS),
              "payload": json.dumps(payload, ensure_ascii=False)}
    try:
        if frappe.db.exists(CACHE, name):
            doc = frappe.get_doc(CACHE, name)
            doc.update(values)
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc({**values, "name": name}).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        # 缓存写失败不该影响这次查询：外部信息已经拿到了。
        frappe.log_error(title="ERP tax lookup cache write failed")
        frappe.db.rollback()


# ── SOAP ────────────────────────────────────────────────────────────────────


def _soap_post(url: str, body: str) -> Optional[str]:
    """发 SOAP 1.2 请求；失败返回 None（超时 5 秒，重试 1 次）。"""
    import requests

    headers = {"Content-Type": "application/soap+xml; charset=utf-8"}
    for attempt in range(RETRY_TIMES + 1):
        try:
            response = requests.post(url, data=body.encode("utf-8"), headers=headers,
                                     timeout=TIMEOUT_SEC)
            if response.status_code == 200:
                return response.text
        except Exception:
            pass
        if attempt < RETRY_TIMES:
            continue
    return None


def verify_tin(raw_tax_id) -> dict:
    """13 位税号是否真实存在（TIN Service）。"""
    tin = normalize_tin(raw_tax_id)
    if not tin:
        return {"ok": False, "error": "invalid_format"}
    cached = _cache_get(tin, 0, "tin")
    if cached:
        return cached
    xml = _soap_post(contracts.TIN_ENDPOINT, contracts.TIN_SOAP_TPL.format(tax_id=tin))
    if not xml:
        return {"ok": False, "error": "rd_unreachable"}
    fields = contracts.raw_fields(xml)
    if not fields:
        # 解析不出来就不缓存：下次重试还能拿到真实响应。
        return {"ok": False, "error": "parse_error"}
    data = contracts.tin_payload(fields, tin)
    _cache_set(tin, 0, "tin", data, True)
    return {"ok": True, "data": data, "cached": False}


def lookup_vat(raw_tax_id, branch: int = 0) -> dict:
    """VAT 注册主体的官方信息。返回 `{ok, data: {tax_id, name, address, ...}}`。"""
    tin = normalize_tin(raw_tax_id)
    if not tin:
        return {"ok": False, "error": "invalid_format"}
    try:
        branch = max(0, int(branch or 0))
    except (TypeError, ValueError):
        branch = 0
    cached = _cache_get(tin, branch, "vat")
    if cached:
        return cached
    xml = _soap_post(contracts.VAT_ENDPOINT,
                     contracts.VAT_SOAP_TPL.format(tax_id=tin, branch_no=branch))
    if not xml:
        return {"ok": False, "error": "rd_unreachable"}
    fields = contracts.raw_fields(xml)
    if not contracts.pick(fields, "NID", "ID"):
        # 没找到这家（也可能税号没注册 VAT）：缓存"查不到"，免得每次开单都打接口。
        _cache_set(tin, branch, "vat", {}, False, "not_found")
        return {"ok": False, "error": "not_found"}
    data = contracts.vat_payload(fields, tin, _("Head Office"), _("Branch {0}"))
    _cache_set(tin, branch, "vat", data, True)
    return {"ok": True, "data": data, "cached": False}


def lookup(raw_tax_id, branch: int = 0) -> dict:
    """给界面用的一个入口：查得到就回字段，查不到给出**能说人话的原因**。

    失败原因分三种，界面按它出不同提示：税号格式不对 / 税局那边没有这家登记 /
    外部接口现在连不上（本实验室容器就是这种情况）。
    """
    if not normalize_tin(raw_tax_id):
        return {"ok": False, "error": "invalid_format"}
    result = lookup_vat(raw_tax_id, branch)
    if result.get("ok"):
        data = dict(result["data"])
        return {"ok": True, "data": data, "cached": result.get("cached", False)}
    if result.get("error") != "not_found":
        return result
    # 查不到 VAT 登记时再看税号本身存不存在，好把"没注册 VAT"和"税号不存在"分开说。
    tin = verify_tin(raw_tax_id)
    if tin.get("ok") and tin["data"].get("exists"):
        return {"ok": False, "error": "not_vat_registered"}
    return {"ok": False, "error": result.get("error") or "not_found"}
