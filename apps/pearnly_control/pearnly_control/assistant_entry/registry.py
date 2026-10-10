"""计费服务上的「租户站点」登记与查询（ADR-019，只在计费站点 `billing.internal` 运行）。

登记由开户计划经管理通道写入（`billing_control` 的 `register`，每个部署一次，可重复执行）：站点域名、
站点实际用的签发方（账号池）、开户管理员的账号名、邮箱与身份。加的成员已在 `Pearnly Member Identity`。
入口查询只读这两处，不碰 Keycloak：
- 登录前只回答「这个账号在哪个账号池」，不透露公司与站点；
- 登录后按「账号池 + 已验证的身份」列出能进的站点。
"""
import frappe

from pearnly_control.assistant_entry import contracts

SITE = "Pearnly Tenant Site"
MEMBER = "Pearnly Member Identity"


def record(tenant, product, site, issuer, owner_username="", owner_email="", owner_subject="") -> None:
    """登记或更新一个部署（按站点域名唯一）。字段只增不清：空值不覆盖已有值。"""
    site = contracts.site(site)
    values = {"tenant": tenant, "product": product, "issuer": contracts.issuer(issuer),
              "owner_username": (owner_username or "").strip().lower(), "owner_email": (owner_email or "").strip().lower(),
              "owner_subject": (owner_subject or "").strip()}
    if frappe.db.exists(SITE, site):
        frappe.db.set_value(SITE, site, {k: v for k, v in values.items() if v})
        return
    frappe.get_doc({"doctype": SITE, "site": site, **values}).insert(ignore_permissions=True)


def issuers_for(login: str) -> list[str]:
    """这个账号（用户名或邮箱）所在的账号池。不存在返回空列表。"""
    login = contracts.login(login)
    field = "owner_email" if "@" in login else "owner_username"
    found = frappe.get_all(SITE, filters={field: login}, pluck="issuer")
    member = frappe.db.get_value(MEMBER, {"email" if "@" in login else "username": login}, ["tenant", "product"], as_dict=True)
    if member:
        found += frappe.get_all(SITE, filters={"tenant": member.tenant, "product": member.product}, pluck="issuer")
    return contracts.ordered_issuers(found)


def sites_for(issuer: str, subject: str, username: str = "") -> list[dict]:
    """已在 `issuer` 账号池验证过的身份能进的站点：开户管理员（按身份，旧登记按账号名）与加的成员。"""
    issuer = contracts.issuer(issuer)
    rows = frappe.get_all(SITE, filters={"issuer": issuer, "owner_subject": subject},
                          fields=["site", "tenant", "product"])
    if not rows and username:
        rows = frappe.get_all(SITE, filters={"issuer": issuer, "owner_username": username.lower(), "owner_subject": ""},
                              fields=["site", "tenant", "product"])
    for member in frappe.get_all(MEMBER, filters={"subject": subject}, fields=["tenant", "product"]):
        rows += frappe.get_all(SITE, filters={"issuer": issuer, "tenant": member.tenant, "product": member.product},
                               fields=["site", "tenant", "product"])
    names = dict(frappe.get_all("Pearnly Tenant", filters={"name": ["in", list({r.tenant for r in rows}) or [""]]},
                                fields=["name", "tenant_name"], as_list=True))
    unique = {r.site: {"site": r.site, "product": r.product, "company": names.get(r.tenant) or r.tenant} for r in rows}
    return sorted(unique.values(), key=lambda r: (r["company"], r["product"]))
