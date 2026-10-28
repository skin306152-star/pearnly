"""业务流程回归实验室（fcb65cca 入口审计，10-28）：两个 ERP 站点 + 一个本地计费控制面，岗位账号走正式开户同一套函数。

- `erp-new.flows.localhost`：像 polymer 的新开户租户（一家公司、7 个仓、没有期初库存），数据由 `seed.py new` 建；
- `erp-old.flows.localhost`：像 mrerp 的老租户（有存量、各类单据都有），数据由 `seed.py old` 建；
- `control.flows.localhost`：本地计费控制面（没有它首页每次打开都报「计费服务暂不可用」），给两个站点各开测试租户、记测试余额。

站点收尾与功能角色授权走正式开户同一套函数（`pearnly_core.provisioning.finalize_site` / `ensure_feature_roles`，
清单取控制面 `features.roles_payload`，功能全开）。岗位账号 `<岗位>@<站点前缀>.flows`，界面中文。
只在任务自有的实验室跑（PEARNLY_FLOWS_LAB=1），拒绝陌生的站点卷；可重复跑，只补缺的。

    cd /home/frappe/frappe-bench && env/bin/python /workspace/scripts/regression/flows/lab.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PASSWORD = "flows-lab-only"
SITES = {"erp-new.flows.localhost": "erp-new", "erp-old.flows.localhost": "erp-old"}
CONTROL = "control.flows.localhost"
# 岗位 → 角色：管理员 = 产品管理员 + 全部功能角色；员工照团队管理真实发的「ERP Menu <菜单>」
JOBS = {"admin": ["ERP Admin", "ERP Products", "ERP Stock", "ERP Purchase", "ERP Sales", "ERP Cashier", "ERP Tasks"],
        "products": ["ERP Menu products", "ERP Menu profile"],
        "stock": ["ERP Menu inventory", "ERP Menu stock-in", "ERP Menu stock-out", "ERP Menu stock-card",
                  "ERP Menu stock-count", "ERP Menu warehouses", "ERP Menu profile"],
        "purchase": ["ERP Menu purchase", "ERP Menu suppliers", "ERP Menu profile"],
        "sales": ["ERP Menu sales", "ERP Menu clients", "ERP Menu profile"],
        "cashier": ["ERP Menu cashier", "ERP Menu pos-receipts", "ERP Menu pos-shifts", "ERP Menu profile"]}


def bench(*args, check=True):
    result = subprocess.run(["bench", *args], text=True, capture_output=True)
    if result.returncode and check:
        print(result.stdout[-4000:]); print(result.stderr[-4000:]); result.check_returncode()
    print("PASS bench", " ".join(args[:4]), flush=True)


def py(code, *args):
    subprocess.run(["../env/bin/python", "-c", code, *args], check=True, cwd="sites")


USERS = r'''
import json, sys
import frappe
site, prefix, jobs = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
frappe.init(site=site, sites_path="."); frappe.connect()
try:
    for job, roles in jobs.items():
        email = f"{job}@{prefix}.flows"
        wanted = ["Desk User", *roles]
        for role in wanted:
            if not frappe.db.exists("Role", role):
                frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
        doc = frappe.get_doc("User", email) if frappe.db.exists("User", email) else frappe.get_doc(
            {"doctype": "User", "email": email, "first_name": job, "username": f"{job}{prefix.replace('-', '')}",
             "send_welcome_email": 0, "enabled": 1})
        doc.set("roles", [])
        doc.language, doc.user_type = "zh", "System User"
        doc.flags.no_welcome_mail = True
        for role in wanted:
            doc.append("roles", {"role": role})
        doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
    frappe.db.commit()
    from pearnly_control import features as catalog
    from pearnly_core import provisioning
    provisioning.finalize_site()
    codes = [item["code"] for item in json.load(open(frappe.get_app_path("pearnly_erp_portal", "features.json")))["features"]]
    print("ROLES", provisioning.ensure_feature_roles(json.dumps(catalog.roles_payload("erp", codes)),
                                                     admin_user=f"admin@{prefix}.flows"), flush=True)
    admin = frappe.get_doc("User", f"admin@{prefix}.flows")
    have = {row.role for row in admin.roles}
    for role in jobs["admin"]:
        if role not in have and frappe.db.exists("Role", role):
            admin.append("roles", {"role": role})
    admin.save(ignore_permissions=True)
    frappe.db.commit()
finally:
    frappe.destroy()
'''

BILLING = r'''
import json, sys
from pathlib import Path
import frappe
control, sites = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=control, sites_path="."); frappe.connect()
try:
    frappe.set_user("Administrator")
    from pearnly_control import billing, billing_api
    from pearnly_control.billing_auth import tenant_token
    root = billing_api.service_token()
    path = Path(control) / "site_config.json"
    config = json.loads(path.read_text()); config["pearnly_billing_scoped_tokens"] = 1; path.write_text(json.dumps(config, indent=2))
    note = "Flows laboratory test credit; no real transfer"
    for site, prefix in sites.items():
        tenant = f"Flows laboratory {prefix}"
        name = frappe.db.get_value("Pearnly Tenant", {"tenant_name": tenant}, "name") or frappe.get_doc(
            {"doctype": "Pearnly Tenant", "tenant_name": tenant}).insert().name
        if not frappe.db.get_value(billing.TOPUP, {"tenant": name, "note": note}, "name"):
            request = frappe.get_doc({"doctype": billing.TOPUP, "tenant": name, "status": "pending", "requested_thb": 1000,
                                      "note": note, "payer_name": "Flows laboratory"}).insert()
            billing_api.approve_local(request.name, "Administrator", actual_amount_thb=1000, note=note)
        path = Path(site) / "site_config.json"
        config = json.loads(path.read_text())
        config.update(pearnly_tenant=name, pearnly_billing_api="http://127.0.0.1:8000", pearnly_billing_host_header=control,
                      pearnly_billing_token=tenant_token(root, name, "erp"))
        path.write_text(json.dumps(config, indent=2))
        print("BILLING", site, name, billing.balance_of(name), flush=True)
    frappe.db.commit()
finally:
    frappe.destroy()
'''


def main():
    if os.environ.get("PEARNLY_FLOWS_LAB") != "1":
        raise SystemExit("Requires the task-owned flows laboratory")
    sites = Path("sites")
    existing = {p.parent.name for p in sites.glob("*/site_config.json")}
    if existing - set(SITES) - {CONTROL}:
        raise SystemExit("Refusing a foreign Site volume")
    config_file = sites / "common_site_config.json"
    config = json.loads(config_file.read_text() or "{}") if config_file.exists() else {}
    config.update(db_host="db", redis_cache="redis://redis:6379/0", redis_queue="redis://redis:6379/1",
                  redis_socketio="redis://redis:6379/2", pause_scheduler=1, disable_scheduler=1, mute_emails=1,
                  serve_default_site=False)
    config_file.write_text(json.dumps(config, indent=2) + "\n")
    (sites / "apps.txt").write_text("frappe\npearnly_core\npearnly_control\npearnly_erp_portal\npearnly_cowork\npearnly_dms\n")
    if not (sites / "assets").exists():
        (sites / "assets").symlink_to("/home/frappe/frappe-bench/assets")
    features = json.loads(Path("apps/pearnly_erp_portal/pearnly_erp_portal/features.json").read_text())["features"]
    for site, prefix in SITES.items():
        site_file = sites / site / "site_config.json"
        if not site_file.exists():
            bench("new-site", site, "--db-host", "db", "--db-root-password", PASSWORD, "--admin-password", PASSWORD,
                  "--mariadb-user-host-login-scope", "%")
        site_config = json.loads(site_file.read_text())
        site_config.update(pearnly_product="erp", pause_scheduler=1, disable_scheduler=1, mute_emails=1,
                           pearnly_features=["home_subscription", *[item["code"] for item in features]],
                           pearnly_company_name=prefix, developer_mode=0, allow_tests=True, host_name=f"http://{site}:8000")
        site_file.write_text(json.dumps(site_config, indent=2) + "\n")
        if not (sites / site / ".installed").exists():
            bench("--site", site, "install-app", "pearnly_core", "pearnly_erp_portal")
            (sites / site / ".installed").write_text("1")
        bench("--site", site, "migrate")
        py(USERS, site, prefix, json.dumps(JOBS))
        print("READY", site, flush=True)
    if not (sites / CONTROL / "site_config.json").exists():
        bench("new-site", CONTROL, "--db-host", "db", "--db-root-password", PASSWORD, "--admin-password", PASSWORD,
              "--mariadb-user-host-login-scope", "%")
        bench("--site", CONTROL, "install-app", "pearnly_control")
    bench("--site", CONTROL, "migrate")
    py(BILLING, CONTROL, json.dumps(SITES))


if __name__ == "__main__":
    main()
