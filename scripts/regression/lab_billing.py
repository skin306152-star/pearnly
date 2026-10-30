"""回归实验室的计费：同一个 bench 里建一个本地控制面站点（`control.regression.localhost`，装 pearnly_control），给每个产品站点
（三个基础站点和两套固定数据的站点）各开一个测试租户、发按产品限定的服务令牌，并把产品站点的计费配置指过去（同 `scripts/dms-line-lab-billing.py` 的做法）。
没有计费服务时，首页每次打开都报一条「计费服务暂不可用」（417），页面快照的「页面报错」检查会被它淹掉。

不碰生产令牌、开户目标、真实转账或外发邮件；给每个测试租户记一笔本地测试余额（走正式审核函数，只在实验站点）。可重复跑。

    cd /home/frappe/frappe-bench && env/bin/python /workspace/scripts/regression/lab_billing.py
"""
import json
import os
import subprocess
from pathlib import Path

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

CONTROL = "control.regression.localhost"
# (租户名后缀, 产品, 站点)：基础站点的后缀就是产品名（沿用原来的租户）；两套固定数据的站点各自一个租户（10-30：CC 记对话路由要它）
PRODUCTS = [("erp", "erp", "erp.regression.localhost"), ("cowork", "cowork", "cowork.regression.localhost"),
            ("dms", "dms", "dms.regression.localhost"), ("erp-new", "erp", "erp-new.regression.localhost"),
            ("erp-old", "erp", "erp-old.regression.localhost"), ("cowork-new", "cowork", "cowork-new.regression.localhost"),
            ("cowork-old", "cowork", "cowork-old.regression.localhost")]
PASSWORD = "regression-lab-only"
CREDIT = 1000
NOTE = "Regression laboratory test credit; no real transfer"

sites = Path("sites")
apps = sites / "apps.txt"
installed = apps.read_text().splitlines()
if "pearnly_control" not in installed:
    apps.write_text("\n".join(installed + ["pearnly_control"]) + "\n")
if not (sites / CONTROL / "site_config.json").exists():
    subprocess.run(["bench", "new-site", CONTROL, "--db-host", "db", "--db-root-password", PASSWORD, "--admin-password", PASSWORD,
                    "--mariadb-user-host-login-scope", "%"], check=True)
    subprocess.run(["bench", "--site", CONTROL, "install-app", "pearnly_control"], check=True)
subprocess.run(["bench", "--site", CONTROL, "migrate"], check=True)
os.chdir(sites)
frappe.init(site=CONTROL, sites_path=".")
frappe.connect()
try:
    frappe.set_user("Administrator")
    from pearnly_control import billing, billing_api
    from pearnly_control.billing_auth import tenant_token

    root = billing_api.service_token()
    control_config = Path(CONTROL) / "site_config.json"
    config = json.loads(control_config.read_text())
    config["pearnly_billing_scoped_tokens"] = 1
    control_config.write_text(json.dumps(config, indent=2))
    for key, product, site in PRODUCTS:
        if not (Path(site) / "site_config.json").exists():
            continue
        tenant_name = f"Regression laboratory {key}"
        name = frappe.db.get_value("Pearnly Tenant", {"tenant_name": tenant_name}, "name")
        if not name:
            name = frappe.get_doc({"doctype": "Pearnly Tenant", "tenant_name": tenant_name}).insert().name
        if not frappe.db.get_value(billing.TOPUP, {"tenant": name, "note": NOTE}, "name"):
            request = frappe.get_doc({"doctype": billing.TOPUP, "tenant": name, "status": "pending", "requested_thb": CREDIT,
                                      "note": NOTE, "payer_name": "Regression laboratory"}).insert()
            billing_api.approve_local(request.name, "Administrator", actual_amount_thb=CREDIT, note=NOTE)
        # 控制面的「租户站点」登记：CC 建对话时经计费通道登记会话路由，只认登记在本租户名下的站点（10-30）。签发方填实验室占位
        if not frappe.db.exists("Pearnly Tenant Site", site):
            frappe.get_doc({"doctype": "Pearnly Tenant Site", "tenant": name, "product": product, "site": site,
                            "issuer": "http://keycloak.regression.localhost/realms/regression-lab"}).insert(ignore_permissions=True)
        site_config = Path(site) / "site_config.json"
        config = json.loads(site_config.read_text())
        config.update(pearnly_tenant=name, pearnly_billing_api="http://127.0.0.1:8000", pearnly_billing_host_header=CONTROL,
                      pearnly_billing_token=tenant_token(root, name, product))
        site_config.write_text(json.dumps(config, indent=2))
        print("BILLING", key, name, "balance", billing.balance_of(name), flush=True)
    frappe.db.commit()
finally:
    frappe.destroy()
