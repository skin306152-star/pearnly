# -*- coding: utf-8 -*-
"""计费底座的对外口子：**产品站点**用的（服务令牌）+ **控制面界面**用的（Desk 会话）。

两类调用方，两种鉴权，别混：

- 产品站点（DMS / Cowork / ERP）用**服务令牌**（`X-Pearnly-Billing-Token`）：
  它们是机器，没有控制面的登录会话。令牌是一次生成、开通时下发到各站点的
  （见 `provisioning._billing_payload`）。
- 控制面界面用**Desk 会话**（管理员本人点「同意」）。钱的动作要给得出人名，
  所以审核这条路只认登录身份，不认令牌。
"""

from __future__ import annotations

import secrets

import frappe
from frappe import _
from frappe.utils import now

from pearnly_control import billing
from pearnly_control.payments.transactions import retry_service

TOKEN_KEY = "pearnly_billing_token"


def service_token(*, create: bool = True) -> str:
    """服务令牌：控制面与产品站点共享的那一个。站点配置里没有就生成一个。"""
    token = frappe.conf.get(TOKEN_KEY)
    if token or not create:
        return token or ""
    from frappe.installer import update_site_config

    token = secrets.token_urlsafe(32)
    update_site_config(TOKEN_KEY, token)
    frappe.conf[TOKEN_KEY] = token
    frappe.db.commit()
    return token


def _require_admin() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if "System Manager" not in frappe.get_roles(user):
        frappe.throw(_("Only a platform admin can handle top-ups and balances"), frappe.PermissionError)
    return user


def _require_service(tenant: str, product: str | None = None) -> None:
    """产品站点来的调用。令牌不对就当没这回事（不透露"令牌存在但错了"）。"""
    expected = service_token(create=False)
    got = (frappe.get_request_header("X-Pearnly-Billing-Token") or "").strip()
    if frappe.conf.get("pearnly_billing_scoped_tokens"):
        from pearnly_control.billing_auth import valid_token

        scope = frappe.get_request_header("X-Pearnly-Billing-Product") or ""
        valid = valid_token(expected, (tenant or "").strip(), scope, got)
        if product is not None and product != scope:
            valid = False
    else:
        valid = bool(expected and got and secrets.compare_digest(got, expected))
    if not valid:
        frappe.throw(_("Invalid service token"), frappe.AuthenticationError)


def _tenant_or_throw(tenant: str) -> str:
    tenant = (tenant or "").strip()
    if not tenant or not frappe.db.exists("Pearnly Tenant", tenant):
        frappe.throw(_("No such tenant: {0}").format(tenant or _("(empty)")))
    return tenant


# ── 产品站点调用（服务令牌）─────────────────────────────────────────────────


@frappe.whitelist(allow_guest=True)
@retry_service
def service_status(tenant: str, kind: str, units: int = 0, tier: str = "B") -> dict:
    """开工前的余额闸。`error_code` 三种：空 / insufficient_balance / lookup_error。"""
    _require_service(tenant)
    return billing.gate(_tenant_or_throw(tenant), kind=kind, units=int(units or 0), tier=tier)


@frappe.whitelist(allow_guest=True, methods=["POST"])
@retry_service
def service_charge(tenant: str, product: str, kind: str, units: int, tier: str = "B",
                   reference: str = "", description: str = "") -> dict:
    """干完活后扣费。调用方必须是"确实识别成功了"的那一刻。"""
    _require_service(tenant, product)
    tenant = _tenant_or_throw(tenant)
    # 排队拿租户锁之前先结束这次请求的读快照（上面查租户存在已经开始了一个）：MariaDB 11.8 开着快照隔离，快照早于排队
    # 的话，拿到锁时前一笔已经改过的钱包一读就是 1020，整笔回滚、在计费线程里睡着重试、再排一次队；同一租户集中扣费时
    # 计费线程被这些重试占满，各租户问余额都超时（独立复查 2026-10-01，本机 MariaDB 11.8.9 复现）。到这里还没写过东西。
    frappe.db.commit()
    return billing.charge(
        tenant, product=product, kind=kind, units=int(units or 0),
        tier=tier, reference=reference, description=description,
    )


@frappe.whitelist(allow_guest=True)
@retry_service
def service_wallet(tenant: str) -> dict:
    """产品站点上的「套餐与余额」页读这个。"""
    _require_service(tenant)
    return billing.wallet_summary(_tenant_or_throw(tenant))


@frappe.whitelist(allow_guest=True, methods=["POST"])
def service_topup_request(tenant: str, amount_thb: float, note: str = "",
                          payer_name: str = "", proof=None) -> dict:
    """租户在产品站点上点「充值」→ 在这里落一条待审核申请。

    只落申请，**不动余额** —— 钱到不到账由控制面的审核决定。
    """
    _require_service(tenant)
    tenant = _tenant_or_throw(tenant)
    amt = billing._money(amount_thb)
    if amt <= 0 or amt > 500000:
        frappe.throw(_("The top-up amount must be between ฿0.01 and ฿500,000"))
    from pearnly_control.payments.topup import decode_proof, attach
    content, suffix = decode_proof(frappe.parse_json(proof))
    doc = frappe.get_doc({
        "doctype": billing.TOPUP,
        "tenant": tenant,
        "status": "pending",
        "requested_thb": amt,
        "note": (note or "")[:500],
        "payer_name": (payer_name or "")[:200],
        "requested_at": now(),
    })
    doc.insert(ignore_permissions=True)
    attach(doc, content, suffix)
    frappe.db.commit()
    return {"request": doc.name, "status": doc.status, "requested_thb": float(amt)}


@frappe.whitelist(allow_guest=True)
def service_topup_status(tenant: str) -> dict:
    """租户看自己最近几次充值的状态（待审核 / 已到账 / 已拒绝）。"""
    _require_service(tenant)
    tenant = _tenant_or_throw(tenant)
    return {
        "items": frappe.get_all(
            billing.TOPUP, filters={"tenant": tenant, "records_deleted_at": ["is", "not set"]},
            fields=["name", "status", "requested_thb", "approved_thb", "creation",
                    "review_note"],
            order_by="creation desc", limit_page_length=20,
        )
    }


@frappe.whitelist(allow_guest=True)
@retry_service
def service_home(tenant: str) -> dict:
    """产品首页（订阅与计费）要的全部东西，一次取完。

    三次调用变一次：这一页是打开频率最高的一页，不该为了显示一行余额打三个来回。
    """
    _require_service(tenant)
    tenant = _tenant_or_throw(tenant)
    summary = billing.wallet_summary(tenant)
    from pearnly_control.payments.topup import account
    from pearnly_control.payments.pricing import public_rates
    return {
        "pricing": public_rates(),
        "collection_account": account(),
        **summary,
        "subscription": billing.subscription_view(tenant),
        "plans": billing.plans(),
        "pending_topups": frappe.get_all(
            billing.TOPUP, filters={"tenant": tenant, "status": "pending"},
            fields=["name", "requested_thb", "creation"],
            order_by="creation desc", limit_page_length=10,
        ),
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
@retry_service
def service_subscribe(tenant: str, plan_code: str) -> dict:
    """订阅套餐：从余额扣月费。余额不够就返回 insufficient_balance（界面引导充值）。"""
    _require_service(tenant)
    return billing.subscribe(_tenant_or_throw(tenant), plan_code)


@frappe.whitelist(allow_guest=True)
def service_records(tenant: str, tab: str = "usage", start: str = "", end: str = "",
                    limit: int = 500, offset: int = 0) -> dict:
    """记录明细按区间取数。`tab` 只有 usage / topup 两种 ——
    识别记录在产品站点本地（`Pearnly OCR Record`），不在这儿取。"""
    _require_service(tenant)
    tenant = _tenant_or_throw(tenant)
    limit = max(1, min(int(limit or 500), 5000))
    offset = max(0, int(offset or 0))
    if tab not in ("usage", "topup"):
        frappe.throw(_("Invalid value"))
    filters = {"tenant": tenant, "records_deleted_at": ["is", "not set"]}
    if tab == "topup":
        filters["creation"] = ("between", [start or "1970-01-01", end or "2999-12-31"])
        rows = frappe.get_all(
            billing.TOPUP, filters=filters,
            fields=["name", "creation", "status", "requested_thb", "approved_thb",
                    "payer_name", "note", "review_note", "reviewed_at"],
            order_by="creation desc, name desc", limit_page_length=limit, limit_start=offset,
        )
    else:
        filters["type"] = "usage"
        filters["creation"] = ("between", [start or "1970-01-01", end or "2999-12-31"])
        rows = frappe.get_all(
            billing.TXN, filters=filters,
            fields=["name", "creation", "product", "units", "pages", "amount_thb",
                    "balance_after", "description", "reference"],
            order_by="creation desc, name desc", limit_page_length=limit, limit_start=offset,
        )
    total = frappe.db.count(billing.TOPUP if tab == "topup" else billing.TXN, filters)
    return {"tab": tab, "rows": rows, "total": total}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def service_delete_records(tenant: str, tab: str, names, actor: str) -> dict:
    _require_service(tenant)
    from pearnly_control.payments.history import delete_records
    return delete_records(_tenant_or_throw(tenant), tab, names, actor)


# ── 控制面界面调用（Desk 会话）──────────────────────────────────────────────


@frappe.whitelist()
def admin_summary(tenant: str) -> dict:
    _require_admin()
    from pearnly_control import billing_remote
    if billing_remote.enabled():
        return billing_remote.call("summary", tenant=tenant)
    return billing.wallet_summary(_tenant_or_throw(tenant))


@frappe.whitelist()
def admin_pending_topups() -> dict:
    _require_admin()
    from pearnly_control import billing_remote
    if billing_remote.enabled():
        return {"items": list(billing_remote.topups(pending=True))}
    return {
        "items": frappe.get_all(
            billing.TOPUP, filters={"status": "pending"},
            fields=["name", "tenant", "requested_thb", "payer_name", "note", "creation"],
            order_by="creation asc", limit_page_length=100,
        )
    }


@frappe.whitelist(methods=["POST"])
def admin_approve(request: str, actual_amount_thb: float | None = None, note: str = "") -> dict:
    """同意充值：**改实收金额 → 立刻入账 + 落台账**（同一事务）。

    实收金额可以和申请金额不一样（转账少转、多转都真实存在）——
    以银行到账为准，不以申请为准。
    """
    admin = _require_admin()
    from pearnly_control import billing_remote
    if billing_remote.enabled():
        return billing_remote.review("approve", request, admin, actual_amount_thb, note)
    return approve_local(request, admin, actual_amount_thb, note)


@retry_service  # 大批扣费时钱包一直在变，同意充值读钱包会撞 1020：整笔回滚重试；已提交过的不重试（独立复查 2026-10-01）
def approve_local(request, admin, actual_amount_thb=None, note=""):
    frappe.db.sql("select name from `tabPearnly Topup Request` where name=%s for update", (request,))
    doc = frappe.get_doc(billing.TOPUP, request, for_update=True)
    if doc.status != "pending":
        frappe.throw(_("This request has already been handled (currently: {0})").format(doc.status))

    amt = billing._money(actual_amount_thb if actual_amount_thb is not None else doc.requested_thb)
    if amt <= 0:
        frappe.throw(_("The received amount must be greater than 0"))

    doc.status = "approved"
    doc.approved_thb = amt
    doc.review_note = (note or "")[:500]
    doc.reviewed_by = admin
    doc.reviewed_at = now()
    doc.save(ignore_permissions=True)

    new_balance = billing.grant(
        doc.tenant, amt, txn_type="topup",
        description=_("Top-up approved · request {0}").format(doc.name),
        reference=doc.name,
    )
    frappe.db.commit()
    return {"ok": True, "request": doc.name, "tenant": doc.tenant,
            "approved_thb": float(amt), "new_balance": float(new_balance)}


@frappe.whitelist(methods=["POST"])
def admin_reject(request: str, note: str = "") -> dict:
    admin = _require_admin()
    from pearnly_control import billing_remote
    if billing_remote.enabled():
        return billing_remote.review("reject", request, admin, note=note)
    return reject_local(request, admin, note)


def reject_local(request, admin, note=""):
    frappe.db.sql("select name from `tabPearnly Topup Request` where name=%s for update", (request,))
    doc = frappe.get_doc(billing.TOPUP, request, for_update=True)
    if doc.status != "pending":
        frappe.throw(_("This request has already been handled (currently: {0})").format(doc.status))
    if not (note or "").strip():
        frappe.throw(_("A rejection needs a reason; the tenant will see it"))
    doc.status = "rejected"
    doc.review_note = note.strip()[:500]
    doc.reviewed_by = admin
    doc.reviewed_at = now()
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "request": doc.name, "status": doc.status}


@frappe.whitelist(methods=["POST"])
def admin_adjust(tenant: str, amount_thb: float, note: str) -> dict:
    """人工调账（补录、补偿、冲正）。**必须写原因** —— 无说明的调账等于黑账。"""
    admin = _require_admin()
    from pearnly_control import billing_remote
    if billing_remote.enabled():
        return billing_remote.call("adjust", tenant=tenant, amount=amount_thb, note=note, actor=admin)
    tenant = _tenant_or_throw(tenant)
    new_balance = retry_service(billing.adjust)(tenant, float(amount_thb or 0), note=note, actor=admin)
    return {"ok": True, "tenant": tenant, "new_balance": float(new_balance)}


@frappe.whitelist()
def admin_slip(request: str):
    """Authenticated management download; cloud private paths stay private."""
    _require_admin()
    import base64
    from pearnly_control import billing_remote
    from pearnly_control.payments.topup import evidence
    data = billing_remote.call("slip", request=request) if billing_remote.enabled() else evidence(request)
    frappe.local.response.update({"type": "download", "filename": data["filename"],
        "filecontent": base64.b64decode(data["content"], validate=True), "display_content_as": "attachment"})
