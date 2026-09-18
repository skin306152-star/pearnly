# -*- coding: utf-8 -*-
"""发号的服务层。

一次发号做四件事：

1. 校验（域名前缀与全局唯一、账号邮箱、产品多选）；
2. 租户：按名称找到或新建；
3. 账号：在身份服务里建号，**已存在则拒绝**（不按邮箱合并到别人的账号）；
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

from pearnly_core import keycloak_admin as kc
from pearnly_control import features as features_mod

ISSUE = "Pearnly Issue"
TENANT = "Pearnly Tenant"

PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"
PASSWORD_LENGTH = 14

PRODUCT_LABEL = {"cowork": "Cowork", "erp": "ERP", "dms": "DMS"}


def _require_operator() -> str:
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("未登录"), frappe.AuthenticationError)
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
    existing = frappe.db.get_value(TENANT, {"tenant_name": tenant_name}, "name")
    if existing:
        return existing
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

    `target` 决定这次开户打到哪个环境（见 `Pearnly Target`）：本机样板，或服务器。
    控制面只在本机跑，但**开户动作是要打到服务器上的**，所以目标是一项配置。

    `apps` 每项：`{"app": "cowork", "domain_prefix": "acme", "display_name": ...}`。
    域名**只填前缀**，完整域名由服务端按统一规则生成（见 `pearnly_control.domains`）。

    功能不再作为入参：**选了哪个产品，那个产品的功能就全开**。
    """
    operator = _require_operator()

    if not isinstance(apps, list) or not apps:
        frappe.throw(_("至少要选一个产品"))

    account_email = (account_email or "").strip().lower()
    account_username = (account_username or "").strip()
    account_password = (account_password or "").strip()
    if not account_username or not account_password:
        frappe.throw(_("用户名和初始密码都要填：它们是用户首次登录的凭据"))
    # 开通的功能 = 所选产品的全集。控制面自己算，调用方无从勾选。
    selected_products = [row.get("app") for row in apps if row.get("app")]
    for product in selected_products:
        if product not in features_mod.PRODUCTS:
            frappe.throw(_("未知产品：{0}").format(product))
    features = {p: features_mod.codes_of(p) for p in features_mod.PRODUCTS
                if p in selected_products}

    # 这次开户打到哪个环境：本机样板，还是服务器。目标是一项配置，不写死。
    kc_client, target_name = kc.client_for_target(target)

    # 账号已存在就拒绝：绝不按邮箱把新租户挂到别人的账号上。
    if kc_client.find_user_id_by_email(account_email):
        frappe.throw(
            _("账号 {0} 在目标环境「{1}」里已存在。请走邀请已有账号流程，不要重复发号").format(
                account_email, target_name)
        )

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

    try:
        subject = kc_client.create_user(
            account_email,
            username=account_username,
            first_name=account_username[:140],
            password=account_password,
        )
        # 不再发邀请邮件：用户拿的就是初始密码，能直接登录。
        # 密码丢了走登录页的「忘记密码」（用邮箱收信），这条路是通的。
        tenant = _find_or_create_tenant(tenant_name, contact_name=contact_name)
    except Exception:
        frappe.db.rollback()
        doc.reload()
        doc.status = "failed"
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        raise

    doc.tenant = tenant
    doc.identity_subject = subject
    doc.status = "issued"
    doc.issued_at = now()
    doc.issued_by = operator
    doc.password_delivered_at = now()
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    result = {
        "issue": doc.name,
        "tenant": tenant,
        "target": target_name,
        "account_email": account_email,
        "account_username": account_username,
        "identity_subject": subject,
        # ⚠️ **不回显密码**：开户人自己设的，他知道；回显只会多一份明文副本。
        "delivery": "password",
        "features": features,
        "deployments": [
            {"app": row.app, "domain": row.domain,
             "domain_prefix": row.domain_prefix, "status": "pending"}
            for row in doc.apps
        ],
    }
    return result


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
        fields=["name", "target_name", "base_url", "realm", "is_default"],
        order_by="is_default desc, target_name asc",
        limit_page_length=0,
    )
