# -*- coding: utf-8 -*-
"""计费底座：三套产品共用的一套规则（余额 · 台账 · 定价 · 余额闸）。

搬自老产品 `services/billing/{pricing,charge,account_status}.py`，规则一条不改，
只把"放在哪"改了：老产品所有租户住同一个库，所以钱表在库里；
新平台每个租户是一个独立站点，只有**控制面**是所有租户、所有产品都认识的地方，
所以余额与台账记在这里（表归属见 `docs/p0/05-table-ownership.md`）。

三套产品用同一份规则的口子就是本模块的 `charge()` / `gate()` 两个函数 ——
产品站点不许自己算钱，也不许直接改余额。

## 定价（老产品原值）

| 单位 | 价格 |
|---|---|
| PDF / 图片 | 每月前 200 页 ฿1.50/页，超出后 ฿0.75/页 |
| Excel / Word / CSV | 50 字符 = ฿0.01，向上取整 |
| 文档折成"张"（套餐额度用） | 按量成本 ÷ ฿1.50，向上取整 |
| 银行对账单计费页数 | max(物理页数, ⌈行数/40⌉)，防一页塞很多笔被低估 |

**失败不收钱**：解析失败、或解析出 0 行的件不算钱（老产品全站口径）。

## A/B 两档

档位是**按任务**分的，不是按租户卖的：银行流水 / 总账 / VAT 报表 / 身份证走
A 档（高精），发票走 B 档（经济）。两档是两种业务，不是同一功能的好坏两档。

老产品这里有个缺陷：A 档成本约为 B 档的 3 到 5 倍，扣费却走同一套页价，
等于高精档白送。这里按用户给的区间取低端，收 `TIER_A_MULTIPLIER` 倍 ——
这是个生意决定，要改就改这一个常量。

不做影子双跑（用户已确认）：同一份文件只会被收一次费。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import now

WALLET = "Pearnly Wallet"
TXN = "Pearnly Credit Transaction"
TOPUP = "Pearnly Topup Request"

# 价格：老产品 services/billing/pricing.py 的原值，别改着改着漂了
PDF_TIER1_LIMIT = 200
PDF_TIER1_PRICE = Decimal("1.50")
PDF_TIER2_PRICE = Decimal("0.75")
EXCEL_CHARS_PER_SATANG = 50
EXCEL_SATANG_PRICE = Decimal("0.01")
ROWS_PER_PAGE_BILLING = 40
DOC_QUOTA_REF_PRICE = Decimal("1.50")

# A 档单价倍数。用户说"约为 B 档的 3 到 5 倍"，这里取低端 3 倍。
TIER_A_MULTIPLIER = Decimal("3")

TIERS = ("B", "A")
_PRODUCTS = ("cowork", "erp", "dms")


def _money(x) -> Decimal:
    """一切金额过这个口：先 Decimal 再两位小数。float 进钱的路一律不认。"""
    return Decimal(str(x or 0)).quantize(Decimal("0.01"))


def price_multiplier(tier: str) -> Decimal:
    return TIER_A_MULTIPLIER if (tier or "B").upper() == "A" else Decimal("1")


def pdf_cost_thb(used_this_month: int, pages: int, tier: str = "B") -> Decimal:
    """PDF N 页的总价，跨界自动拆段（前 200 页一个价、超出另一个价）。"""
    n = max(0, int(pages or 0))
    if n == 0:
        return Decimal("0.00")
    used = max(0, int(used_this_month or 0))
    tier1 = min(n, max(0, PDF_TIER1_LIMIT - used))
    tier2 = n - tier1
    return _money((PDF_TIER1_PRICE * tier1 + PDF_TIER2_PRICE * tier2) * price_multiplier(tier))


def excel_cost_thb(chars: int, tier: str = "B") -> Decimal:
    """Excel/Word/CSV 按字符计费：50 字符 = 1 萨当，向上取整。"""
    n = max(0, int(chars or 0))
    if n == 0:
        return Decimal("0.00")
    satang = math.ceil(n / EXCEL_CHARS_PER_SATANG)
    return _money(EXCEL_SATANG_PRICE * satang * price_multiplier(tier))


def doc_quota_pages(chars: int) -> int:
    """字符档文件折算成套餐额度"张"数（按量成本 ÷ ฿1.50，向上取整）。"""
    cost = excel_cost_thb(chars)
    if cost <= 0:
        return 0
    return math.ceil(cost / DOC_QUOTA_REF_PRICE)


def pdf_billing_units(page_count: int, row_count: int) -> int:
    """银行对账单的计费页数：max(物理页数, ⌈行数/40⌉)。

    居中口径：既不按行数计（老 bug 超收 10 到 34 倍），也不让"一页塞很多笔"
    的密集账单被低估。图片按 1 页。
    """
    pages = max(1, int(page_count or 0))
    rows = max(0, int(row_count or 0))
    return max(pages, math.ceil(rows / ROWS_PER_PAGE_BILLING))


def estimate_cost_thb(used_this_month: int, kind: str, units: int, tier: str = "B") -> Decimal:
    """这批活儿的估价。kind 只有 pdf / excel 两种（与老产品的扣费口径一致）。"""
    if kind == "pdf":
        return pdf_cost_thb(used_this_month, units, tier)
    if kind == "excel":
        return excel_cost_thb(units, tier)
    frappe.throw(_("未知的计费单位：{0}").format(kind))


# ── 钱包与台账 ──────────────────────────────────────────────────────────────


def wallet_of(tenant: str, *, create: bool = True):
    """取租户钱包。新租户余额是 0（不是"默认给点钱"）。"""
    name = frappe.db.get_value(WALLET, {"tenant": tenant}, "name")
    if name:
        return frappe.get_doc(WALLET, name)
    if not create:
        return None
    doc = frappe.get_doc({"doctype": WALLET, "tenant": tenant, "balance_thb": 0})
    doc.insert(ignore_permissions=True)
    return doc


def balance_of(tenant: str) -> Decimal:
    return _money(frappe.db.get_value(WALLET, {"tenant": tenant}, "balance_thb") or 0)


def grant(tenant: str, amount_thb, *, txn_type: str = "topup", description: str = "",
          reference: str = "", product: str | None = None) -> Decimal:
    """**加余额的唯一口**：钱包与台账一起写，一个事务。

    直接 UPDATE 余额而不落台账，钱账就对不上（老产品铁律：钱写入必须同时落两处）。
    """
    if txn_type not in ("topup", "adjustment"):
        frappe.throw(_("grant 只处理进账（topup / adjustment）；扣费走 charge()"))
    amt = _money(amount_thb)
    if amt <= 0:
        frappe.throw(_("金额必须大于 0"))

    wallet = wallet_of(tenant)
    new_balance = _money(Decimal(str(wallet.balance_thb or 0)) + amt)
    wallet.balance_thb = new_balance
    wallet.updated_at = now()
    wallet.save(ignore_permissions=True)
    _write_txn(tenant, txn_type, amt, new_balance, description, reference, product=product)
    frappe.db.commit()
    return new_balance


def charge(tenant: str, *, product: str, kind: str, units: int, tier: str = "B",
           description: str = "", reference: str = "") -> dict:
    """扣费。**调用方必须确认这笔活已经干完了** —— 扣了不退。

    允许扣成负数：用量已经发生（OCR 跑完了），余额是欠账，靠后续充值补。
    老产品就是这么做的，不是 bug。
    """
    units = int(units or 0)
    if units <= 0:
        return {"ok": True, "charged_thb": 0.0,
                "balance_after": float(balance_of(tenant)), "reason": "no_units"}

    wallet = wallet_of(tenant)
    ym = _bkk_year_month()
    used = _pages_used_this_month(tenant, ym) if kind == "pdf" else 0
    cost = estimate_cost_thb(used, kind, units, tier)
    if cost <= 0:
        return {"ok": True, "charged_thb": 0.0,
                "balance_after": float(wallet.balance_thb or 0), "reason": "zero_cost"}

    new_balance = _money(Decimal(str(wallet.balance_thb or 0)) - cost)
    wallet.balance_thb = new_balance
    wallet.updated_at = now()
    if kind == "pdf":
        wallet.pages_used_this_month = int(used) + units
    wallet.save(ignore_permissions=True)

    _write_txn(
        tenant, "usage", -cost, new_balance,
        description or f"{product} OCR {kind} units={units} tier={(tier or 'B').upper()}",
        reference, product=product, units=units, pages=units if kind == "pdf" else 0,
    )
    frappe.db.commit()
    return {
        "ok": True,
        "charged_thb": float(cost),
        "balance_after": float(new_balance),
        "kind": kind,
        "units": units,
        "tier": (tier or "B").upper(),
    }


def _write_txn(tenant, txn_type, amount, balance_after, description, reference,
               *, product=None, units=0, pages=0) -> None:
    """落一行台账。**只由本模块调** —— 别处写台账等于绕过钱的一致性。"""
    frappe.get_doc({
        "doctype": TXN,
        "tenant": tenant,
        "type": txn_type,
        "amount_thb": float(_money(amount)),
        "balance_after": float(_money(balance_after)),
        "product": product if product in _PRODUCTS else None,
        "units": int(units or 0),
        "pages": int(pages or 0),
        "description": (description or "")[:500],
        "reference": (reference or "")[:140],
    }).insert(ignore_permissions=True)


def adjust(tenant: str, amount_thb, *, note: str, actor: str = "") -> Decimal:
    """人工调账：正数补录、负数冲正。**必须写原因**。

    不走 `charge()` —— 调账是给定金额，不是按页/字符算出来的。
    """
    if not (note or "").strip():
        frappe.throw(_("调账必须写原因"))
    amt = _money(amount_thb)
    if amt == 0:
        frappe.throw(_("调账金额不能是 0"))

    wallet = wallet_of(tenant)
    new_balance = _money(Decimal(str(wallet.balance_thb or 0)) + amt)
    wallet.balance_thb = new_balance
    wallet.updated_at = now()
    wallet.save(ignore_permissions=True)
    _write_txn(
        tenant, "adjustment", amt, new_balance,
        f"人工调账 · {note}" + (f" · by {actor}" if actor else ""), "",
    )
    frappe.db.commit()
    return new_balance


def _bkk_year_month() -> str:
    """按 Asia/Bangkok（UTC+7）算月度锚 —— 阶梯价的"本月已用页数"跟着它走。

    老产品踩过：用 UTC 算会让月初 7 小时的用量落进上个月，阶梯价跟着错。
    """
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%Y-%m")


def _pages_used_this_month(tenant: str, ym: str) -> int:
    """本月已用页数 = 台账里这个月的 usage 行之和。

    老产品另存了一张 monthly_page_usage；这里不重复存 —— 同一件事存两处迟早
    对不上，而台账本来就是权威来源。
    """
    rows = frappe.db.sql(
        """
        select coalesce(sum(pages), 0) as p
        from `tabPearnly Credit Transaction`
        where tenant = %(tenant)s and type = 'usage'
          and left(creation, 7) = %(ym)s
        """,
        {"tenant": tenant, "ym": ym},
        as_dict=True,
    )
    return int((rows[0]["p"] if rows else 0) or 0)


# ── 余额闸（产品站点用）─────────────────────────────────────────────────────


def gate(tenant: str, *, kind: str, units: int, tier: str = "B") -> dict:
    """能不能干这批活。返回 allowed / error_code / 估价 / 余额。

    **三条出路必须分清楚**（老产品为这条写过注释，代价是有人被误导去充钱）：

    - allowed=True：钱够，放行；
    - error_code=insufficient_balance：**用户没钱** → 引导去充值（402）；
    - error_code=lookup_error：**我们自己查不出来** → 503 稍后重试。

      绝不能把系统故障报成"余额不足"，那会让会计去充一笔本来就够的钱。
    """
    try:
        wallet = wallet_of(tenant)
        balance = _money(wallet.balance_thb or 0)
        ym = _bkk_year_month()
        used = _pages_used_this_month(tenant, ym) if kind == "pdf" else 0
        est = estimate_cost_thb(used, kind, units, tier)
    except Exception as exc:  # noqa: BLE001
        frappe.log_error(str(exc)[:400], "billing.gate 查余额失败")
        return {"allowed": False, "error_code": "lookup_error",
                "reason": "余额查不出来，不是余额不足"}

    if est <= 0:
        return {"allowed": True, "balance_thb": float(balance),
                "estimate_thb": 0.0, "error_code": ""}
    if balance < est:
        return {"allowed": False, "error_code": "insufficient_balance",
                "balance_thb": float(balance), "estimate_thb": float(est),
                "reason": "余额不足，请先充值"}
    return {"allowed": True, "balance_thb": float(balance),
            "estimate_thb": float(est), "error_code": ""}


def wallet_summary(tenant: str) -> dict:
    """给界面看的：余额 + 本月用量 + 最近几笔。"""
    wallet = wallet_of(tenant)
    ym = _bkk_year_month()
    return {
        "tenant": tenant,
        "balance_thb": float(_money(wallet.balance_thb or 0)),
        "pages_used_this_month": _pages_used_this_month(tenant, ym),
        "year_month": ym,
        "transactions": frappe.get_all(
            TXN, filters={"tenant": tenant},
            fields=["name", "type", "amount_thb", "balance_after", "description", "creation"],
            order_by="creation desc", limit_page_length=20,
        ),
    }
