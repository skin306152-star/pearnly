# -*- coding: utf-8 -*-
"""发号的服务层。

一次发号做四件事：

1. 校验（域名前缀与全局唯一、账号邮箱、产品多选）；
2. 租户：按名称找到或新建；
3. 账号：在身份服务里建号，**已存在则拒绝**（不按邮箱合并到别人的账号）；
   ERP / DMS 在业务账号池、Cowork 在自己的账号池（用户 2026-09-26 分池），两类都开就两个池各建一个；
4. 产出开通清单：每个产品一行（含各自域名）+ 该产品的**全部**功能。

**功能不勾了**（用户 2026-09-18 定）：选了哪个产品，那个产品的功能就全开，
所以这里没有"功能参数"这回事。

凭据交付有两种方式（`delivery`）：
- `invite`（默认，正式流程）：账号先不设密码，由身份服务发**邀请邮件**，用户自己设；
- `password`：当场生成一次性初始密码，只在返回值里出现一次（邮件发不到时本机验证用）。
两条路都**不把凭据落库**。

真正的建站（Site、库、装 App、证书）不在这一步——本层只保证"要开什么被
完整且无歧义地记下来"，执行与验收另算。
"""

import secrets
import string

import frappe
from frappe import _
from frappe.utils import now
from pearnly_core.execution import state as pearnly_state

from pearnly_core import keycloak_admin as kc
from pearnly_control.account import identity_pools
from pearnly_control import features as features_mod
from pearnly_control.onboarding.targets import cloud_target_names, require_cloud_target

ISSUE = "Pearnly Issue"
TENANT = "Pearnly Tenant"

PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"
PASSWORD_LENGTH = 14

PRODUCT_LABEL = {"cowork": "Cowork", "erp": "ERP", "dms": "DMS"}


def _require_operator() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    frappe.only_for("System Manager")
    return user


def _generate_password() -> str:
    while True:
        pwd = "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))
        # 至少各含一位，避免刚好生成的密码过不了强度校验
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!@#$%^&*" for c in pwd)):
            return pwd


def _find_or_create_tenant(tenant_name: str, *, contact_name: str | None = None,
                           contact_phone: str | None = None) -> str:
    # Company names are labels, never tenant identities. Issued requests are
    # already idempotent by issue id; distinct issues must not merge companies.
    doc = frappe.get_doc({
        "doctype": TENANT,
        "tenant_name": tenant_name,
        "contact_name": contact_name,
        "contact_phone": contact_phone,
    })
    doc.insert()
    return doc.name


def issue(
    tenant_name: str,
    account_email: str,
    apps: list[dict],
    *,
    target: str | None = None,
    account_username: str | None = None,
    account_password: str | None = None,
    contact_name: str | None = None,
    notes: str | None = None,
) -> dict:
    """发号。返回开通清单。

    **账号三件套**：`account_email`（邮箱）+ `account_username`（用户名）+
    `account_password`（初始密码）。用户拿到初始密码当天就能登录，邮箱和用户名都能当登录名。

    `target` 决定这次开户打到哪个环境（见 `Pearnly Target`）：仅限已配置的云端目标。
    控制面只在本机跑，但**开户动作是要打到服务器上的**，所以目标是一项配置。

    `apps` 每项：`{"app": "cowork", "domain_prefix": "acme", "display_name": ...}`。
    域名**只填前缀**，完整域名由服务端按统一规则生成（见 `pearnly_control.domains`）。

    功能不再作为入参：**选了哪个产品，那个产品的功能就全开**。
    """
    operator = _require_operator()

    if not isinstance(apps, list) or not apps:
        frappe.throw(_("Pick at least one product"))

    account_email = (account_email or "").strip().lower()
    account_username = (account_username or "").strip()
    account_password = (account_password or "").strip()
    if not account_username or not account_password:
        frappe.throw(_("Username and initial password are required"))
    # 开通的功能 = 所选产品的全集。控制面自己算，调用方无从勾选。
    selected_products = [row.get("app") for row in apps if row.get("app")]
    for product in selected_products:
        if product not in features_mod.PRODUCTS:
            frappe.throw(_("Unknown product: {0}").format(product))
    features = {p: features_mod.codes_of(p) for p in features_mod.PRODUCTS
                if p in selected_products}

    # 本地后台只向明确配置的云端目标开户，缺配置时拒绝；所选产品的账号池都要配齐。
    target_name = require_cloud_target(target)
    for product in identity_pools.pools_for(selected_products):
        _kc_client, target_name = kc.client_for_target(target_name, product)

    apps_payload = []
    for row in apps:
        # 兼容老调用方直接给完整域名：拆出前缀。新调用方只给前缀。
        prefix = (row.get("domain_prefix") or "").strip().lower()
        if not prefix:
            full = (row.get("domain") or "").strip().lower()
            prefix = full.split(".")[0] if full else ""
        apps_payload.append({
            "app": row["app"],
            "domain_prefix": prefix,
            "display_name": row.get("display_name"),
        })

    doc = frappe.get_doc({
        "doctype": ISSUE,
        "tenant_name": tenant_name,
        "account_email": account_email,
        "account_username": account_username,
        "account_password": account_password,
        # delivery 字段保留在表里（老单子还在用），但表单不再展示：凭据就是开户人给的初始密码
        "delivery": "password",
        "notes": notes,
        "target": target_name,
        "apps": apps_payload,
        "cowork_features": _feature_rows("cowork", features.get("cowork")),
        "erp_features": _feature_rows("erp", features.get("erp")),
        "dms_features": _feature_rows("dms", features.get("dms")),
    })
    doc.insert()  # validate 会算域名前缀→域名、查冲突、汇总已开通功能
    frappe.db.commit()  # Preserve the request before touching an external identity system.
    return _activate(doc, operator, target_name=target_name)


def issue_from_doc(issue_name: str) -> dict:
    """对**已经存好的**发号单执行"发号"。

    为什么需要这条入口：Desk 上那张表单是先存草稿再点按钮的两步动作，
    和 `issue()` 的"一次把参数全给齐"不是同一条路。但"发号"这件事本身只有一种做法，
    所以两者共用 `_activate()`。

    幂等：已经 issued 的单子重复点不会重复建账号（账号已存在会被 `_activate` 拒绝，
    这里直接返回现状）。
    """
    operator = _require_operator()
    doc = frappe.get_doc(ISSUE, issue_name)
    if identity_pools.issued(doc):
        return _result(doc, doc.target or "")
    if doc.status == "failed":
        frappe.throw(_("The last issue attempt failed; set it back to draft before issuing again"))
    target_name = require_cloud_target(doc.target, use_default=False)
    for product in identity_pools.pools_for([row.app for row in doc.apps]):
        _kc_client, target_name = kc.client_for_target(target_name, product)
    doc.target = target_name
    return _activate(doc, operator, target_name=target_name)


def _activate(doc, operator: str, *, target_name: str) -> dict:
    with pearnly_state.store().lock(pearnly_state.store().make_key(f"pearnly:issue:{doc.name}"), timeout=120, blocking_timeout=2):
        doc.reload()
        if identity_pools.issued(doc):
            return _result(doc, doc.target or target_name)
        doc.target = target_name
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return _activate_locked(doc, operator, target_name=target_name)


def _activate_locked(doc, operator: str, *, target_name: str) -> dict:
    """把一张**已保存**的发号单真正发出去：在身份服务建号 + 建/找租户。

    两条入口（API 一次发号、Desk 存草稿后点发号）共用这一份 ——
    分开写迟早会有一条路忘了改。
    """
    target_name = require_cloud_target(target_name, use_default=False)
    # 每个要建号的池一个管理客户端（ERP / DMS 在业务池，Cowork 在自己的池）。
    clients = [(product, kc.client_for_target(target_name, product)[0])
               for product in identity_pools.pools_for([row.app for row in doc.apps])]

    # 账号已存在就拒绝：绝不按邮箱把新租户挂到别人的账号上。任何一个池里已有都不建。
    for product, kc_client in clients:
        if kc_client.find_user_id_by_email(doc.account_email):
            frappe.throw(
                _("Account {0} already exists in target \"{1}\". Invite the existing account instead of issuing again.").format(
                    doc.account_email, target_name)
            )

    created = []
    try:
        # ⚠️ 必须用 get_password()：`account_password` 是 Frappe 的 Password 字段，
        # 直接读属性拿到的是**密文**（`__Auth` 里的那段），拿它建号等于建了个假密码 ——
        # 现象是"账号建出来了、就是登不进去"（Desk 路径踩过；API 路径因为密码来自参数
        # 所以一直是好的，两边共用 _activate 之后才暴露）。
        initial_password = doc.get_password("account_password", raise_exception=False)
        if not initial_password:
            frappe.throw(_("This issue has no usable initial password; fill it in again before issuing"))
        for product, kc_client in clients:
            subject = kc_client.create_user(
                doc.account_email,
                username=doc.account_username,
                first_name=doc.account_username[:140],
                password=initial_password,
            )
            created.append((kc_client, subject))
            doc.set(identity_pools.subject_field(product), subject)
        # 不再发邀请邮件：用户拿的就是初始密码，能直接登录。
        # 密码丢了走登录页的「忘记密码」（用邮箱收信），这条路是通的。
        tenant = _find_or_create_tenant(doc.tenant_name)
        # 开户单选的识别引擎落到租户上（ADR-022）；开户计划的 set_ocr 读租户这一份。
        if (doc.get("ocr_engine") or "Google") != (frappe.db.get_value(TENANT, tenant, "ocr_engine") or "Google"):
            frappe.db.set_value(TENANT, tenant, "ocr_engine", doc.get("ocr_engine") or "Google")
        # 开出来的租户**余额是 0**（用户 2026-09-18 确认的口径）：
        # 钱包这时候就建好，免得第一次用 OCR 时才发现没账本。
        from pearnly_control import billing, infra

        if not infra.is_cloud(target_name):
            billing.wallet_of(tenant)
        doc.tenant = tenant
        doc.status = "issued"
        doc.issued_at = now()
        doc.issued_by = operator
        doc.password_delivered_at = now()
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        # Only compensate the exact identities created by this attempt.
        for kc_client, subject in created:
            kc_client.delete_user(subject)
        doc.reload()
        doc.status = "failed"
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        raise

    return _result(doc, target_name)


def _result(doc, target_name: str) -> dict:
    """发号结果。⚠️ **不回显密码**：开户人自己设的，他知道。"""
    return {
        "issue": doc.name,
        "tenant": doc.tenant,
        "target": target_name,
        "account_email": doc.account_email,
        "account_username": doc.account_username,
        "identity_subject": doc.identity_subject,
        "cowork_identity_subject": doc.get("cowork_identity_subject"),
        "delivery": "password",
        "features": {
            p: [row.feature for row in doc.get(f"{p}_features") or []]
            for p in features_mod.PRODUCTS
        },
        "deployments": [
            {"app": row.app, "domain": row.domain,
             "domain_prefix": row.domain_prefix, "status": "pending"}
            for row in doc.apps
        ],
    }


def _feature_rows(product: str, codes) -> list[dict]:
    """把功能代码转成子表行。代码可以是短码（intake）或完整名（cowork-intake）。"""
    rows = []
    for raw in codes or []:
        code = str(raw or "").strip()
        if not code:
            continue
        name = code if code.startswith(f"{product}-") else features_mod.feature_name(product, code)
        rows.append({"feature": name})
    return rows


def list_issues(*, status=None, limit=50) -> list[dict]:
    _require_operator()
    filters = {"status": status} if status else {}
    return frappe.get_all(
        ISSUE, filters=filters,
        fields=["name", "status", "tenant_name", "account_email", "feature_summary",
                "issued_at", "tenant"],
        order_by="creation desc", limit_page_length=int(limit),
    )


def deployments_of(issue_name: str) -> list[dict]:
    """某张发号单产出的开通清单。"""
    _require_operator()
    doc = frappe.get_doc(ISSUE, issue_name)
    return [
        {"app": row.app, "label": PRODUCT_LABEL.get(row.app, row.app),
         "domain": row.domain, "display_name": row.display_name}
        for row in doc.apps
    ]


# ── 接口层 ──────────────────────────────────────────────────────────────────


@frappe.whitelist(methods=["POST"])
def api_issue(**kwargs):
    kwargs.pop("cmd", None)
    return issue(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_issue_from_doc(issue):
    """Desk 表单上的「发号」按钮走这条（单子已经存好了）。"""
    return issue_from_doc(issue)


@frappe.whitelist()
def api_list_issues(**kwargs):
    kwargs.pop("cmd", None)
    return list_issues(**kwargs)


@frappe.whitelist()
def api_deployments_of(issue_name):
    return deployments_of(issue_name)


@frappe.whitelist()
def api_context():
    """发号页要的东西：操作人 + CSRF 令牌。

    放在接口里而不是模板变量里，有两个原因：
    1. 模板变量在这个页面上没有被渲染成值（见 docs/p4/02-issue-ui.md 的记录），
       与其硬上不如换个确定能用的做法；
    2. 这个接口本身要求登录，未登录会直接报错——页面不承载任何数据，
       所有敏感动作都在这个接口之后。
    """
    from frappe.sessions import get_csrf_token

    return {
        "operator": _require_operator(),
        "csrf_token": get_csrf_token(),
        "targets": list_targets(),
    }


def list_targets() -> list[dict]:
    """可选的开户目标，供页面下拉。"""
    _require_operator()
    return frappe.get_all(
        "Pearnly Target",
        filters={"name": ["in", cloud_target_names()]},
        fields=["name", "target_name", "base_url", "realm", "is_default"],
        order_by="is_default desc, target_name asc",
        limit_page_length=0,
    )
