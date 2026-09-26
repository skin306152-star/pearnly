#!/usr/bin/env python3
"""Cowork 测试站点的实验室配置（只在测试机的 Cowork 栈里跑，由 run.sh `lab-init` 调用）。

为什么需要它：Cowork 线上还没有租户，没法像 testdms / testerp 那样从线上整站导入。
这里把空白站点 `cowork.test.localhost` 配成能走录入工作台的样子，两段各在自己的容器里跑：

  billing  （billing-web）建本栈自己的账本站 `billing.internal`（与线上 bootstrap-billing 同形：
           计费专用、按租户签令牌），登记测试租户并给一笔测试额度，输出该租户的 cowork 令牌。
           令牌只在测试机上两个容器进程之间传，不回 Mac、不落仓库。
  site     （cowork-web）给站点写计费配置、开通全部 Cowork 功能、建管理员账号与公司资料、
           打开 MR.ERP 推送开关。

有意不做（与线上不同，交付时如实说明）：
- 不接统一身份：站点用**本地密码**登录（`/api/method/login`）。线上业务站点没有这条路；
  Cowork 的账号池拆分（`pearnly-cowork` realm）完成后再换成真实登录。
- 不配 OCR（真实识别是付费调用，另行授权后再配）、不配 LINE、不建 ERP 连接（由人在网页里填）。
全部幂等：重复跑只补缺的，不改已有数据。
"""
import json
import os
import secrets
import sys
from pathlib import Path

SITES = Path("/home/frappe/frappe-bench/sites")
BILLING_SITE = "billing.internal"
TENANT = "T-TESTCOWORK"
LAB_CREDIT_THB = 1000


def log(message):
    print(message, flush=True)


def billing():
    os.chdir(SITES)
    import frappe
    from frappe.installer import _new_site, install_app, update_site_config

    if not Path(BILLING_SITE, "site_config.json").exists():
        frappe.init(site=BILLING_SITE, new_site=True)
        _new_site(db_name=None, site=BILLING_SITE, db_type="mariadb", db_host="mariadb",
                  db_root_username="root", db_root_password=os.environ["TESTENV_DB_ROOT_PASSWORD"],
                  admin_password=secrets.token_hex(16), mariadb_user_host_login_scope="%")
        frappe.destroy()
        log("· 账本站点已建")
    frappe.init(site=BILLING_SITE)
    frappe.connect()
    frappe.set_user("Administrator")
    # 令牌只生成一次：换了根令牌，已下发给产品站点的租户令牌就全部失效。
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

    if not frappe.db.exists("Pearnly Tenant", TENANT):
        frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": "Test Cowork (testenv)",
                        "status": "active"}).insert(ignore_permissions=True, set_name=TENANT)
    ledger.wallet_of(TENANT)
    if not ledger.balance_of(TENANT):
        ledger.adjust(TENANT, LAB_CREDIT_THB, note="testenv Cowork lab credit", actor="Administrator")
        log(f"· 测试额度 {LAB_CREDIT_THB} THB")
    frappe.db.commit()
    token = tenant_token(frappe.conf.get("pearnly_billing_token"), TENANT, "cowork")
    frappe.destroy()
    # run.sh 只截这一行，转给 site 段；不打印到别处。
    print("LAB_BILLING " + json.dumps({"tenant": TENANT, "token": token}), flush=True)


def site():
    name = os.environ["TESTENV_SITE"]
    admin_email = os.environ["LAB_ADMIN_EMAIL"]
    admin_password = os.environ["LAB_ADMIN_PASSWORD"]
    lab = json.loads(os.environ["LAB_BILLING"])
    os.chdir(SITES)
    import frappe
    from frappe.installer import update_site_config

    frappe.init(site=name)
    frappe.connect()
    frappe.set_user("Administrator")
    if frappe.conf.get("pearnly_product") != "cowork" or frappe.conf.get("pearnly_oidc_issuer"):
        raise SystemExit("Refusing: not a blank Cowork test site")

    from pearnly_control import features as catalog

    codes = catalog.codes_of("cowork")
    for key, value in {
        "pearnly_tenant": lab["tenant"], "pearnly_billing_api": "http://billing-web:8000",
        "pearnly_billing_host_header": BILLING_SITE, "pearnly_billing_token": lab["token"],
        "pearnly_features": codes, "pearnly_cowork_erp_push_enabled": 1,
    }.items():
        update_site_config(key, value)
        frappe.conf[key] = value

    from pearnly_core import provisioning

    provisioning.finalize_site()
    # 线上收尾会关掉本地密码登录（业务账号只从统一身份登录）；实验室站点没有身份服务，
    # 关了就谁都进不去。这是实验室与线上唯一刻意不同的登录差异，见文件头。
    frappe.db.set_single_value("System Settings", "disable_user_pass_login", 0)

    if not frappe.db.exists("User", admin_email):
        user = frappe.get_doc({"doctype": "User", "email": admin_email, "username": "labadmin",
                               "first_name": "labadmin", "send_welcome_email": 0,
                               "user_type": "System User", "roles": [{"role": "Desk User"}]})
        user.new_password = admin_password
        user.insert(ignore_permissions=True)
        log("· 管理员账号已建")
    roles = catalog.roles_payload("cowork", codes)
    provisioning.ensure_feature_roles(json.dumps({**roles, "admin_user": admin_email}), admin_user=admin_email)
    workspace = catalog.workspace_payload("cowork", codes, title="Test Cowork")
    workspace["features"] = codes
    workspace["roles"] = (list(roles.get("base_roles") or []) + [roles["admin_role"]]
                          + [row["role"] for row in roles.get("roles") or []])
    provisioning.ensure_product_workspace(json.dumps(workspace))
    if not frappe.db.exists("Cowork Workspace", {"enabled": 1}):
        frappe.get_doc({"doctype": "Cowork Workspace", "display_name": "Test Cowork",
                        "enabled": 1}).insert(ignore_permissions=True)
        log("· 公司资料已建")
    from pearnly_cowork.team.setup import sync_roles

    sync_roles()
    frappe.db.commit()
    frappe.destroy()
    log("COWORK_LAB_OK")


if __name__ == "__main__":
    {"billing": billing, "site": site}[sys.argv[1]]()
