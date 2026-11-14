#!/usr/bin/env python3
"""本机 Mac 实验栈（maclab）的站点初始化，在容器里跑（由 scripts/maclab.sh `init` 调用，不单独用）。全部幂等：重复跑只补缺的。

    init.py billing <erp|cowork>   在本栈 billing-web 里：建账本站 billing.internal（计费专用、按租户签令牌），登记本栈的测试租户、
                                   记一笔本地测试额度，打印一行 ``LAB_BILLING {json}``（租户 + 按产品限定的令牌；maclab.sh 截下转给
                                   下一段，不回显、不落盘）。
    init.py erp                    在 ERP 站点（erp.maclab.localhost）：计费配置、全部 ERP 功能、开户收尾、本地密码登录、功能角色与
                                   工作区、测试员工（ERP 管理员 + 采购 / 销售 / 库存等岗位，能把电脑连上 ERP、能开采购单）。
                                   **不建公司**：CC 桌面版报账套时按税号自动建（express/companies.py），主档由 CC 读账套同步。
    init.py cowork                 在 Cowork 站点（cowork.maclab.localhost）：testenv/cowork-lab.py site 之后补本栈的差异
                                   （带端口的站点地址、调度器开着）。

和测试机实验栈一样的口径（testenv/site-init.py、cowork-lab.py）：只认本栈自己的空白站点（产品对得上、没接统一身份），
不接统一身份（站点用本地密码登录）、不配 LINE、不配识别（不产生付费调用）。

环境变量（maclab.sh 传，口令都来自本机 ~/.config/pearnly-maclab/<产品>.env，不进仓库）：
  MACLAB_SITE           站点名（erp.maclab.localhost / cowork.maclab.localhost）
  MACLAB_PUBLIC         测试机连过来的地址里的「主机:端口」（写进站点 host_name，服务端拼的地址带端口）
  MACLAB_DB_ROOT_PASSWORD   billing 段建账本站用
  LAB_BILLING           billing 段打印的那行 JSON（erp 段用）
  MACLAB_USER / MACLAB_USER_PASSWORD   erp 段的测试员工与他的本地密码
"""
import json
import os
import secrets
import sys
from pathlib import Path

SITES = Path("/home/frappe/frappe-bench/sites")
BILLING_SITE = "billing.internal"
TENANTS = {"erp": "T-MACLAB-ERP", "cowork": "T-MACLAB-COWORK"}
LAB_CREDIT_THB = 1000
SITE_SUFFIX = ".maclab.localhost"
# 测试员工的岗位：ERP 管理员（开户时发的「产品管理员 + 全部功能角色」，由 ensure_feature_roles 发）之外，再补团队管理里逐人勾的
# 菜单角色（同回归实验室的管理员岗位，scripts/regression/lab_init.py ROLES["erp"]["admin"]）。
ERP_EXTRA_ROLES = ["ERP Admin", "ERP Products", "ERP Stock", "ERP Purchase", "ERP Sales", "ERP Cashier", "ERP Tasks"]


def log(message):
    print(message, flush=True)


def billing(product):
    if product not in TENANTS:
        raise SystemExit(f"unknown product {product}")
    os.chdir(SITES)
    import frappe
    from frappe.installer import _new_site, install_app, update_site_config

    if not Path(BILLING_SITE, "site_config.json").exists():
        frappe.init(site=BILLING_SITE, new_site=True)
        _new_site(db_name=None, site=BILLING_SITE, db_type="mariadb", db_host="mariadb",
                  db_root_username="root", db_root_password=os.environ["MACLAB_DB_ROOT_PASSWORD"],
                  admin_password=secrets.token_hex(16), mariadb_user_host_login_scope="%")
        frappe.destroy()
        log("· 账本站点已建")
    frappe.init(site=BILLING_SITE)
    frappe.connect()
    frappe.set_user("Administrator")
    # 根令牌只生成一次：换了它，已下发给产品站点的租户令牌就全部失效。
    for key, value in {"pearnly_billing_only": True, "pearnly_billing_scoped_tokens": True,
                       "pearnly_billing_token": secrets.token_hex(32),
                       "pearnly_billing_management_token": secrets.token_hex(32)}.items():
        if not frappe.conf.get(key):
            update_site_config(key, value)
            frappe.conf[key] = value
    if "pearnly_control" not in frappe.get_installed_apps():
        install_app("pearnly_control")
        log("· 已装 pearnly_control")

    from pearnly_control import billing as ledger
    from pearnly_control.billing_auth import tenant_token

    tenant = TENANTS[product]
    if not frappe.db.exists("Pearnly Tenant", tenant):
        frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": f"Mac lab {product}", "status": "active"}
                       ).insert(ignore_permissions=True, set_name=tenant)
    ledger.wallet_of(tenant)
    if not ledger.balance_of(tenant):
        ledger.adjust(tenant, LAB_CREDIT_THB, note="maclab test credit; no real transfer", actor="Administrator")
        log(f"· 测试额度 {LAB_CREDIT_THB} THB")
    frappe.db.commit()
    token = tenant_token(frappe.conf.get("pearnly_billing_token"), tenant, product)
    frappe.destroy()
    print("LAB_BILLING " + json.dumps({"tenant": tenant, "token": token}), flush=True)


def _open_site(product):
    site = os.environ["MACLAB_SITE"]
    if not site.endswith(SITE_SUFFIX):
        raise SystemExit(f"Refusing: {site} is not a maclab site")
    os.chdir(SITES)
    import frappe

    frappe.init(site=site)
    frappe.connect()
    frappe.set_user("Administrator")
    if frappe.conf.get("pearnly_product") != product or frappe.conf.get("pearnly_oidc_issuer"):
        raise SystemExit(f"Refusing: {site} is not a blank {product} maclab site")
    return frappe


def _site_basics(frappe):
    """两个产品共同的差异：带端口的站点地址、调度器开着（新站点默认关，`push.poll` / `masters.advance` 每分钟靠它）。"""
    from frappe.installer import update_site_config

    public = os.environ["MACLAB_PUBLIC"]
    for key, value in {"host_name": f"http://{public}", "enable_scheduler": True}.items():
        update_site_config(key, value)
        frappe.conf[key] = value
    frappe.db.set_single_value("System Settings", "enable_scheduler", 1)


def erp():
    frappe = _open_site("erp")
    from frappe.installer import update_site_config
    from pearnly_control import features as catalog
    from pearnly_core import provisioning

    lab = json.loads(os.environ["LAB_BILLING"])
    codes = catalog.codes_of("erp")
    for key, value in {"pearnly_tenant": lab["tenant"], "pearnly_billing_api": "http://billing-web:8000",
                       "pearnly_billing_host_header": BILLING_SITE, "pearnly_billing_token": lab["token"],
                       "pearnly_features": codes, "pearnly_company_name": "Mac lab ERP"}.items():
        update_site_config(key, value)
        frappe.conf[key] = value
    _site_basics(frappe)
    provisioning.finalize_site()
    # 线上收尾会关掉本地密码登录（业务账号只从统一身份登录）；实验栈没有身份服务，关了谁都进不去（同 cowork-lab.py）。
    frappe.db.set_single_value("System Settings", "disable_user_pass_login", 0)

    email = os.environ["MACLAB_USER"].strip().lower()
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({"doctype": "User", "email": email, "username": email.split("@")[0].replace(".", ""),
                               "first_name": "Mac lab", "last_name": "ERP", "send_welcome_email": 0,
                               "user_type": "System User", "language": "zh", "time_zone": "Asia/Bangkok",
                               "roles": [{"role": "Desk User"}]})
        user.new_password = os.environ["MACLAB_USER_PASSWORD"]
        user.insert(ignore_permissions=True)
        log("· 测试员工已建")
    roles = catalog.roles_payload("erp", codes)
    provisioning.ensure_feature_roles(json.dumps({**roles, "admin_user": email}), admin_user=email)
    workspace = catalog.workspace_payload("erp", codes, title="Mac lab ERP")
    workspace["features"] = codes
    workspace["roles"] = (list(roles.get("base_roles") or []) + [roles["admin_role"]]
                          + [row["role"] for row in roles.get("roles") or []])
    provisioning.ensure_product_workspace(json.dumps(workspace))
    user = frappe.get_doc("User", email)
    have = {row.role for row in user.roles}
    for role in ERP_EXTRA_ROLES:
        if role not in have and frappe.db.exists("Role", role):
            user.append("roles", {"role": role})
    user.save(ignore_permissions=True)
    frappe.db.commit()

    from pearnly_erp_portal.express import desktop

    allowed = desktop.allowed(email)
    log(f"· 测试员工 {email}：能连电脑 = {allowed}")
    frappe.destroy()
    if not allowed:
        raise SystemExit("test employee cannot register a CC Desktop computer")
    log("MACLAB_ERP_OK")


def cowork():
    frappe = _open_site("cowork")
    _site_basics(frappe)
    frappe.db.commit()
    frappe.destroy()
    log("MACLAB_COWORK_OK")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "billing":
        billing(sys.argv[2])
    elif mode == "erp":
        erp()
    elif mode == "cowork":
        cowork()
    else:
        raise SystemExit("usage: init.py billing <erp|cowork> | erp | cowork")
