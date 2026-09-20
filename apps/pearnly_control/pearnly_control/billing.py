# -*- coding: utf-8 -*-
"""计费底座：三套产品共用的一套规则（余额 · 台账 · 定价 · 余额闸）。

搬自老产品 `services/billing/{pricing,charge,account_status}.py`，沿用既有单价与额度，
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

档位只选择识别模型，不改变客户定价。按旧线上正式定价收取，不将模型成本
倍数直接叠加给客户；展示、余额预检和扣费共用下面的单价。

不做影子双跑（用户已确认）：同一份文件只会被收一次费。
"""

from __future__ import annotations

import math
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import frappe
from frappe import _
from frappe.utils import now

WALLET = "Pearnly Wallet"
TXN = "Pearnly Credit Transaction"
TOPUP = "Pearnly Topup Request"
SUBSCRIPTION = "Pearnly Subscription"

# 价格：老产品 services/billing/pricing.py 的原值，别改着改着漂了
PDF_TIER1_LIMIT = 200
PDF_TIER1_PRICE = Decimal("1.50")
PDF_TIER2_PRICE = Decimal("0.75")
EXCEL_CHARS_PER_SATANG = 50
EXCEL_SATANG_PRICE = Decimal("0.01")
ROWS_PER_PAGE_BILLING = 40
DOC_QUOTA_REF_PRICE = Decimal("1.50")

TIERS = ("B", "A")
_PRODUCTS = ("cowork", "erp", "dms")

# 订阅套餐目录（老产品 services/billing/pricing.py 的 SUBSCRIPTION_PLANS 原值）。
# 周期 = 订阅日起 30 天；额度单位是"张"，字符档文件按 doc_quota_pages 折算成张。
SUBSCRIPTION_PLANS = {
    "S": {"quota": 100, "fee": Decimal("150"), "over_rate": Decimal("1.50")},
    "M": {"quota": 200, "fee": Decimal("250"), "over_rate": Decimal("1.25")},
    "L": {"quota": 500, "fee": Decimal("500"), "over_rate": Decimal("1.00")},
}
SUBSCRIPTION_CYCLE_DAYS = 30


def _money(x) -> Decimal:
    """一切金额过这个口：先 Decimal 再两位小数。float 进钱的路一律不认。"""
    from decimal import InvalidOperation
    try:
        value = Decimal(str(x or 0)).quantize(Decimal("0.01"))
        if not value.is_finite():
            raise InvalidOperation
        return value
    except (InvalidOperation, ValueError):
        frappe.throw(_("Invalid amount"))


def pdf_cost_thb(used_this_month: int, pages: int, tier: str = "B") -> Decimal:
    """PDF N 页的总价，跨界自动拆段（前 200 页一个价、超出另一个价）。"""
    n = max(0, int(pages or 0))
    if n == 0:
        return Decimal("0.00")
    used = max(0, int(used_this_month or 0))
    tier1 = min(n, max(0, PDF_TIER1_LIMIT - used))
    tier2 = n - tier1
    return _money(PDF_TIER1_PRICE * tier1 + PDF_TIER2_PRICE * tier2)


def excel_cost_thb(chars: int, tier: str = "B") -> Decimal:
    """Excel/Word/CSV 按字符计费：50 字符 = 1 萨当，向上取整。"""
    n = max(0, int(chars or 0))
    if n == 0:
        return Decimal("0.00")
    satang = math.ceil(n / EXCEL_CHARS_PER_SATANG)
    return _money(EXCEL_SATANG_PRICE * satang)


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
    frappe.throw(_("Unknown billing unit: {0}").format(kind))


# ── 钱包与台账 ──────────────────────────────────────────────────────────────


def wallet_of(tenant: str, *, create: bool = True):
    """取租户钱包。新租户余额是 0（不是"默认给点钱"）。"""
    # A stable parent row also serializes creation of the initially absent wallet.
    # All mutations lock this row first; wallet/quota/ledger update together.
    if create:
        frappe.db.sql("select name from `tabPearnly Tenant` where name=%s for update", (tenant,))
    name = frappe.db.get_value(WALLET, {"tenant": tenant}, "name", for_update=create)
    if name:
        return frappe.get_doc(WALLET, name, for_update=create)
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
        frappe.throw(_("grant only handles money in (topup / adjustment); use charge() for deductions"))
    amt = _money(amount_thb)
    if amt <= 0:
        frappe.throw(_("Amount must be greater than 0"))

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
    if product not in _PRODUCTS or kind not in ("pdf", "excel"):
        frappe.throw(_("Unknown billing unit: {0}").format(kind))
    if len(reference) > 140:
        frappe.throw(_("Invalid billing reference"))
    units = int(units or 0)
    if units <= 0:
        return {"ok": True, "charged_thb": 0.0,
                "balance_after": float(balance_of(tenant)), "reason": "no_units"}

    wallet = wallet_of(tenant)
    # 有订阅就先吃套餐额度：额度内的免费，超出的按套餐超额单价扣。
    # （老产品的口径：订阅与按量阶梯价互不干扰，有订阅走套餐、没订阅走阶梯。）
    fingerprint = hashlib.sha256(json.dumps(
        [product, kind, units, (tier or "B").upper()], separators=(",", ":")
    ).encode()).hexdigest()
    if reference:
        previous = frappe.db.get_value(
            TXN, {"tenant": tenant, "product": product, "type": "usage", "reference": reference},
            ["amount_thb", "balance_after", "request_hash"], as_dict=True, for_update=True,
        )
        if previous:
            if not previous.request_hash or previous.request_hash != fingerprint:
                frappe.throw(_("The billing reference was already used for another request"))
            return {"ok": True, "charged_thb": float(-_money(previous.amount_thb)),
                    "balance_after": float(previous.balance_after), "duplicate": True,
                    "kind": kind, "units": units, "tier": (tier or "B").upper()}
    elif frappe.conf.get("pearnly_billing_scoped_tokens"):
        frappe.throw(_("A billing reference is required"))
    sub = _active_by_cycle(tenant)
    if sub is not None:
        return _charge_with_subscription(tenant, wallet, sub, product=product, kind=kind,
                                         units=units, tier=tier, description=description,
                                         reference=reference, request_hash=fingerprint)

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
        request_hash=fingerprint, billing_mode="payg",
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


def _charge_with_subscription(tenant, wallet, sub, *, product, kind, units, tier,
                             description, reference, request_hash="") -> dict:
    """套餐内扣费：额度内免费，超出按 over_rate 从余额扣。"""
    quota_need = int(units) if kind == "pdf" else doc_quota_pages(units)
    quota = int(sub.quota or 0)
    used = int(sub.pages_used_this_cycle or 0)
    consumed = min(quota_need, max(0, quota - used))
    billable = quota_need - consumed
    over_rate = _money(sub.over_rate or 0)
    cost = _money(Decimal(billable) * over_rate) if billable > 0 else Decimal("0.00")

    new_balance = _money(Decimal(str(wallet.balance_thb or 0)) - cost)
    wallet.balance_thb = new_balance
    wallet.updated_at = now()
    wallet.save(ignore_permissions=True)

    sub.pages_used_this_cycle = used + quota_need
    sub.save(ignore_permissions=True)

    if cost > 0:
        _write_txn(tenant, "usage", -cost, new_balance,
                   description or _("{0} out-of-plan scan ({1} over quota)").format(product, billable),
                   reference, product=product, units=units,
                   pages=units if kind == "pdf" else 0, request_hash=request_hash,
                   billing_mode="subscription")
    elif consumed > 0:
        # 额度内：金额为 0，但**要留一行**，否则这个周期用了多少张在台账里看不到
        _write_txn(tenant, "usage", Decimal("0.00"), new_balance,
                   description or _("{0} in-plan scan ({1} pages, within quota)").format(product, consumed),
                   reference, product=product, units=units,
                   pages=units if kind == "pdf" else 0, request_hash=request_hash,
                   billing_mode="subscription")
    frappe.db.commit()
    return {
        "ok": True,
        "charged_thb": float(cost),
        "balance_after": float(new_balance),
        "kind": kind,
        "units": units,
        "tier": (tier or "B").upper(),
        "subscription": True,
        "quota_pages": quota_need,
        "billable_pages": billable,
    }


def _write_txn(tenant, txn_type, amount, balance_after, description, reference,
               *, product=None, units=0, pages=0, request_hash="", billing_mode="") -> None:
    """落一行台账。**只由本模块调** —— 别处写台账等于绕过钱的一致性。"""
    from pearnly_control.payments.usage import billing_month

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
        "request_hash": request_hash,
        "billing_mode": billing_mode,
        "billing_month": billing_month(now()) if billing_mode else "",
    }).insert(ignore_permissions=True)


def adjust(tenant: str, amount_thb, *, note: str, actor: str = "") -> Decimal:
    """人工调账：正数补录、负数冲正。**必须写原因**。

    不走 `charge()` —— 调账是给定金额，不是按页/字符算出来的。
    """
    if not (note or "").strip():
        frappe.throw(_("An adjustment must have a reason"))
    amt = _money(amount_thb)
    if amt == 0:
        frappe.throw(_("An adjustment cannot be 0"))

    wallet = wallet_of(tenant)
    new_balance = _money(Decimal(str(wallet.balance_thb or 0)) + amt)
    wallet.balance_thb = new_balance
    wallet.updated_at = now()
    wallet.save(ignore_permissions=True)
    _write_txn(
        tenant, "adjustment", amt, new_balance,
        _("Manual adjustment · {0}").format(note) + (f" · by {actor}" if actor else ""), "",
    )
    frappe.db.commit()
    return new_balance


def _bkk_year_month() -> str:
    """按 Asia/Bangkok（UTC+7）算月度锚 —— 阶梯价的"本月已用页数"跟着它走。

    老产品踩过：用 UTC 算会让月初 7 小时的用量落进上个月，阶梯价跟着错。
    """
    return (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%Y-%m")


def _pages_used_this_month(tenant: str, ym: str) -> int:
    """Only payg pages advance the Bangkok calendar-month price threshold."""
    from pearnly_control.payments.usage import pages_used

    return pages_used(tenant, ym)


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
    except frappe.QueryDeadlockError:
        raise  # The complete DB-only service transaction owns bounded retries.
    except Exception as exc:  # noqa: BLE001
        frappe.log_error(str(exc)[:400], "billing.gate 查余额失败")
        return {"allowed": False, "error_code": "lookup_error",
                "reason": _("The balance could not be read; this is not 'insufficient balance'")}

    if est <= 0:
        return {"allowed": True, "balance_thb": float(balance),
                "estimate_thb": 0.0, "error_code": ""}

    # 有订阅且额度吃得下 → 免费放行（不看余额）；吃不下 → 只有超额那部分要钱
    sub = _active_by_cycle(tenant)
    quota_need = int(units) if kind == "pdf" else doc_quota_pages(units)
    payable = est
    if sub is not None:
        remaining = max(0, int(sub.quota or 0) - int(sub.pages_used_this_cycle or 0))
        if remaining >= quota_need:
            return {"allowed": True, "balance_thb": float(balance),
                    "estimate_thb": 0.0, "error_code": "", "subscription": True,
                    "quota_remaining": remaining}
        billable = quota_need - remaining
        payable = _money(Decimal(billable) * _money(sub.over_rate or 0))

    if balance < payable:
        return {"allowed": False, "error_code": "insufficient_balance",
                "balance_thb": float(balance), "estimate_thb": float(payable),
                "reason": _("Insufficient balance; please top up first")}
    return {"allowed": True, "balance_thb": float(balance),
            "estimate_thb": float(payable), "error_code": ""}


def wallet_summary(tenant: str) -> dict:
    """给界面看的：余额 + 本月用量 + 最近几笔。"""
    wallet = wallet_of(tenant, create=False)
    ym = _bkk_year_month()
    from pearnly_control.payments.usage import pages_used
    return {
        "tenant": tenant,
        "balance_thb": float(_money(wallet.balance_thb if wallet else 0)),
        "pages_used_this_month": pages_used(tenant, ym, lock=False),
        "year_month": ym,
        "transactions": frappe.get_all(
            TXN, filters={"tenant": tenant},
            fields=["name", "type", "amount_thb", "balance_after", "description", "creation"],
            order_by="creation desc", limit_page_length=20,
        ),
    }


# ── 订阅 ────────────────────────────────────────────────────────────────────


def plans() -> list[dict]:
    """套餐目录（给界面渲染那三张卡）。"""
    return [
        {"code": code, "quota": spec["quota"], "fee": float(spec["fee"]),
         "over_rate": float(spec["over_rate"]), "cycle_days": SUBSCRIPTION_CYCLE_DAYS}
        for code, spec in SUBSCRIPTION_PLANS.items()
    ]


def subscription_of(tenant: str):
    """当前**有效**订阅。没有 / 已取消 / 已过期都返回 None（当作按量计费）。"""
    name = frappe.db.get_value(SUBSCRIPTION, {"tenant": tenant}, "name")
    if not name:
        return None
    doc = frappe.get_doc(SUBSCRIPTION, name)
    if doc.status != "active":
        return None
    if doc.cycle_end and str(now()) > str(doc.cycle_end):
        return None
    return doc


def subscription_view(tenant: str) -> dict | None:
    """给界面看的订阅状态（含剩余额度）。"""
    sub = subscription_of(tenant)
    if not sub:
        return None
    return {
        "plan_code": sub.plan_code,
        "status": sub.status,
        "quota": int(sub.quota or 0),
        "pages_used_this_cycle": int(sub.pages_used_this_cycle or 0),
        "remaining": max(0, int(sub.quota or 0) - int(sub.pages_used_this_cycle or 0)),
        "over_rate": float(sub.over_rate or 0),
        "monthly_fee": float(sub.monthly_fee or 0),
        "cycle_end": str(sub.cycle_end or ""),
    }


def subscribe(tenant: str, plan_code: str) -> dict:
    """订阅 / 换套餐：**从余额扣月费**，余额不够就是 402（老产品口径）。

    换套餐 = 重新开一个周期（旧的额度作废）。这是老产品的做法，不是 bug ——
    订阅是"买这个周期的额度"，改单就是重新买。
    """
    code = (plan_code or "").strip().upper()
    spec = SUBSCRIPTION_PLANS.get(code)
    if not spec:
        frappe.throw(_("No such plan: {0}").format(plan_code))

    fee = _money(spec["fee"])
    wallet = wallet_of(tenant)
    balance = _money(wallet.balance_thb)
    if balance < fee:
        return {
            "ok": False,
            "error": "insufficient_balance",
            "balance_thb": float(balance),
            "needed_thb": float(fee),
            "reason": _("Balance is not enough for the monthly fee (need ฿{0}, have ฿{1})").format(fee, balance),
        }

    new_balance = _money(Decimal(str(wallet.balance_thb or 0)) - fee)
    wallet.balance_thb = new_balance
    wallet.updated_at = now()
    wallet.save(ignore_permissions=True)

    start = now()
    end = frappe.utils.add_days(start, SUBSCRIPTION_CYCLE_DAYS)
    sub_name = frappe.db.get_value(SUBSCRIPTION, {"tenant": tenant}, "name", for_update=True)
    if sub_name:
        sub = frappe.get_doc(SUBSCRIPTION, sub_name, for_update=True)
    else:
        sub = frappe.get_doc({"doctype": SUBSCRIPTION, "tenant": tenant})
    sub.plan_code = code
    sub.status = "active"
    sub.cycle_start = start
    sub.cycle_end = end
    sub.quota = int(spec["quota"])
    sub.over_rate = float(spec["over_rate"])
    sub.monthly_fee = float(fee)
    sub.pages_used_this_cycle = 0
    sub.save(ignore_permissions=True)

    # 月费要落台账：类型用 usage（这是消耗，不是充值）
    _write_txn(tenant, "usage", -fee, new_balance,
               _("Plan {0} ({1} days · {2} pages)").format(
                   code, SUBSCRIPTION_CYCLE_DAYS, spec["quota"]
               ),
               sub.name, units=0, billing_mode="subscription_fee")
    frappe.db.commit()
    return {"ok": True, "plan_code": code, "charged_thb": float(fee),
            "balance_after": float(new_balance), "cycle_end": str(end),
            "subscription": subscription_view(tenant)}


def _active_by_cycle(tenant: str):
    """本周期还没到期的订阅 —— 扣费走这个。

    **不做自动续订**（用户 2026-09-18 定）：套餐就是买 30 天的额度，到期结束，
    要续就重新订阅。所以这里只看周期有没有到点，不看别的状态。
    """
    name = frappe.db.get_value(SUBSCRIPTION, {"tenant": tenant}, "name", for_update=True)
    if not name:
        return None
    doc = frappe.get_doc(SUBSCRIPTION, name, for_update=True)
    if doc.status != "active" or not doc.cycle_end or str(now()) > str(doc.cycle_end):
        return None
    return doc
