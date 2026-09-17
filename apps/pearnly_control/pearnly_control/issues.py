# -*- coding: utf-8 -*-
"""发号的服务层。

一次发号做四件事：

1. 校验（域名格式与全局唯一、账号邮箱、产品多选、OCR 档位）；
2. 租户：按名称找到或新建；
3. 账号：在身份服务里建号，**已存在则拒绝**（不按邮箱合并到别人的账号）；
4. 产出开通清单：每个产品一行，含各自的域名。

初始密码**不落库**，只在返回值里出现一次。

真正的建站（Site、库、装 App、证书）不在这一步——本层只保证"要开什么被
完整且无歧义地记下来"，执行与验收另算。
"""

import secrets
import string

import frappe
from frappe import _
from frappe.utils import now

from pearnly_core import keycloak_admin as kc

ISSUE = "Pearnly Issue"
TENANT = "Pearnly Tenant"

# 控制面往哪套配置里建租户账号：**业务 realm**。
# 控制面自己是用平台 realm 登录的，但租户账号属于业务 realm，两者不能混。
BUSINESS = "pearnly_business_oidc"

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
    account_name: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    ocr_economy: bool = True,
    ocr_enterprise: bool = False,
    notes: str | None = None,
) -> dict:
    """发号。返回开通清单，以及**只出现一次**的初始密码。"""
    operator = _require_operator()

    if not isinstance(apps, list) or not apps:
        frappe.throw(_("至少要选一个产品"))

    account_email = (account_email or "").strip().lower()

    # 账号已存在就拒绝：绝不按邮箱把新租户挂到别人的账号上。
    if kc.find_user_id_by_email(account_email, BUSINESS):
        frappe.throw(
            _("账号 {0} 在身份服务里已存在。请走邀请已有账号流程，不要重复发号").format(account_email)
        )

    doc = frappe.get_doc({
        "doctype": ISSUE,
        "tenant_name": tenant_name,
        "account_email": account_email,
        "account_name": account_name,
        "ocr_economy": 1 if ocr_economy else 0,
        "ocr_enterprise": 1 if ocr_enterprise else 0,
        "notes": notes,
        "apps": [
            {"app": row["app"], "domain": row["domain"], "display_name": row.get("display_name")}
            for row in apps
        ],
    })
    doc.insert()  # validate 会查域名冲突

    password = _generate_password()
    try:
        subject = kc.create_user(
            account_email,
            first_name=(account_name or tenant_name)[:140],
            password=password,
            prefix=BUSINESS,
        )
        tenant = _find_or_create_tenant(
            tenant_name, contact_name=contact_name, contact_phone=contact_phone
        )
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

    return {
        "issue": doc.name,
        "tenant": tenant,
        "account_email": account_email,
        "identity_subject": subject,
        # 只在这里出现一次；本系统不保存明文
        "initial_password": password,
        "ocr": {
            "economy_b": bool(ocr_economy),
            "enterprise_a": bool(ocr_enterprise),
        },
        "deployments": [
            {"app": row.app, "domain": row.domain, "status": "pending"}
            for row in doc.apps
        ],
    }


def list_issues(*, status=None, limit=50) -> list[dict]:
    _require_operator()
    filters = {"status": status} if status else {}
    return frappe.get_all(
        ISSUE, filters=filters,
        fields=["name", "status", "tenant_name", "account_email", "ocr_summary",
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
    }
