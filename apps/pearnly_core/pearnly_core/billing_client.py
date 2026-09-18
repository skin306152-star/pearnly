# -*- coding: utf-8 -*-
"""产品站点问控制面要余额 / 报扣费的**唯一客户端**。

三套产品（Cowork / ERP / DMS）都通过这里说话 —— 各写一份 HTTP 调用迟早会漂
（超时不一样、错误码翻译不一样、有人忘了带令牌）。这就是"共用一套计费底座"
在代码上的落点。

站点配置（开通时由 `set_billing` 步骤写入）：

    pearnly_tenant              本站在控制面是哪家租户（T-2026-0001）
    pearnly_billing_api         控制面地址（容器内网，如 http://control-web:8000）
    pearnly_billing_host_header 控制面的站点名（Frappe 靠 Host 认站点）
    pearnly_billing_token       服务令牌

**错误码的三种出路**（老产品栽过，这里照抄它的分法）：

- `""` 放行；
- `insufficient_balance`：**用户没钱** → 界面引导充值；
- `lookup_error`：**我们自己坏了** → 503 稍后重试。

  绝不能把系统故障报成"余额不足"，那会让会计去充一笔本来就够的钱。
"""

from __future__ import annotations

import logging

import frappe
import requests

logger = logging.getLogger(__name__)

TIMEOUT = 10


class BillingUnavailable(Exception):
    """连不上控制面 / 拿不到答案。调用方按 503 处理，**不要**说成余额不足。"""


def _conf() -> dict:
    return {
        "tenant": (frappe.conf.get("pearnly_tenant") or "").strip(),
        "api": (frappe.conf.get("pearnly_billing_api") or "").rstrip("/"),
        "host": (frappe.conf.get("pearnly_billing_host_header") or "").strip(),
        "token": (frappe.conf.get("pearnly_billing_token") or "").strip(),
    }


def configured() -> bool:
    c = _conf()
    return bool(c["tenant"] and c["api"] and c["token"])


def _call(method: str, params: dict | None = None) -> dict:
    c = _conf()
    if not configured():
        raise BillingUnavailable("本站还没配计费底座（pearnly_billing_*）")
    # 租户由本站自己带上：产品站点只可能替**自己那个租户**说话，
    # 不给自己填（会漏），也不接受别处传一个别人的租户号进来。
    payload = dict(params or {})
    payload["tenant"] = c["tenant"]
    headers = {"X-Pearnly-Billing-Token": c["token"], "Accept": "application/json"}
    if c["host"]:
        headers["Host"] = c["host"]
    url = f"{c['api']}/api/method/pearnly_control.billing_api.{method}"
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        raise BillingUnavailable(f"连不上计费底座：{exc}") from exc
    if resp.status_code != 200:
        raise BillingUnavailable(f"计费底座返回 HTTP {resp.status_code}")
    body = resp.json()
    if "message" not in body:
        raise BillingUnavailable(str(body)[:200])
    return body["message"]


# ── 产品用得到的几件事 ──────────────────────────────────────────────────────


def status(kind: str, units: int, tier: str = "B") -> dict:
    """开工前问一句。异常一律转成 `lookup_error`，不往上抛 ——
    调用方只需要分"钱够 / 没钱 / 查不出来"三种，不需要认识网络异常。"""
    try:
        return _call("service_status", {"kind": kind, "units": int(units or 0), "tier": tier})
    except BillingUnavailable as exc:
        logger.warning("billing.status 查不到：%s", exc)
        return {"allowed": False, "error_code": "lookup_error", "reason": str(exc)}


def charge(product: str, kind: str, units: int, *, tier: str = "B",
           reference: str = "", description: str = "") -> dict:
    """干完活后报账。失败只记日志 —— **不能因为记账失败就把已经做完的活说成失败**。"""
    try:
        return _call("service_charge", {
            "product": product, "kind": kind, "units": int(units or 0), "tier": tier,
            "reference": reference, "description": description,
        })
    except BillingUnavailable as exc:
        logger.error("billing.charge 没记上（%s %s units=%s）：%s", product, kind, units, exc)
        return {"ok": False, "error": str(exc)}


def wallet() -> dict:
    return _call("service_wallet", {})


def topup_request(amount_thb: float, note: str = "", payer_name: str = "") -> dict:
    return _call("service_topup_request",
                 {"amount_thb": amount_thb, "note": note, "payer_name": payer_name})


def topup_status() -> dict:
    return _call("service_topup_status", {})


def home() -> dict:
    """首页（订阅与计费）要的全部：余额 + 流水 + 当前套餐 + 套餐目录。"""
    return _call("service_home", {})


def subscribe(plan_code: str) -> dict:
    return _call("service_subscribe", {"plan_code": plan_code})


def records(tab: str, start: str = "", end: str = "", limit: int = 500) -> dict:
    return _call("service_records",
                 {"tab": tab, "start": start, "end": end, "limit": int(limit)})
