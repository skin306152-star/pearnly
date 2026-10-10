#!/usr/bin/env python3
"""在测试环境的账本容器（billing-web）里建/更新智能体入口站点 testai.pearnly.com（ADR-019）。

形态与正式入口一致：站点只装 pearnly_control，配置 `pearnly_assistant_entry*`，首页指向入口页 ai；
查询令牌由本栈账本 billing.internal 的根密钥派生（不打印）。登录走线上 auth.pearnly.com 的 pearnly-ai 客户端。
只允许 testai.* 站点名，避免误建正式入口。
"""
import json
import os
from pathlib import Path

SITES = Path("/home/frappe/frappe-bench/sites")
BILLING = "billing.internal"
DEFAULT_ISSUER = "https://auth.pearnly.com/realms/pearnly-business"


def main():
    site = os.environ["TESTENV_SITE"]
    if not site.startswith("testai.") or not site.endswith(".pearnly.com"):
        raise SystemExit(f"Refusing entry site {site}")
    os.chdir(SITES)
    if not Path(BILLING, "site_config.json").exists():
        raise SystemExit("billing.internal is not imported in this stack")
    root = json.loads(Path(BILLING, "site_config.json").read_text()).get("pearnly_billing_token") or ""
    if not root:
        raise SystemExit("billing.internal has no service token")

    import frappe
    from frappe.installer import _new_site, install_app, update_site_config

    if not Path(site, "site_config.json").exists():
        frappe.init(site=site, new_site=True)
        _new_site(db_name=None, site=site, db_type="mariadb", db_host="mariadb", db_root_username="root",
                  db_root_password=os.environ["TESTENV_DB_ROOT_PASSWORD"],
                  admin_password=os.environ.get("TESTENV_ADMIN_PASSWORD", "testenv-admin"),
                  mariadb_user_host_login_scope="%")
        frappe.destroy()
        print(f"PASS new site {site}", flush=True)

    frappe.init(site=site)
    frappe.connect()
    if "pearnly_control" not in frappe.get_installed_apps():
        install_app("pearnly_control", set_as_patched=True)
        print("PASS install pearnly_control", flush=True)
    from pearnly_control.assistant_entry import contracts

    for key, value in {
        "pearnly_assistant_entry": 1,
        "pearnly_assistant_entry_url": f"https://{site}",
        "pearnly_assistant_entry_billing_url": "http://billing-web:8000",
        "pearnly_assistant_entry_billing_host": BILLING,
        "pearnly_assistant_entry_token": contracts.entry_token(root),
        "pearnly_assistant_entry_default_issuer": DEFAULT_ISSUER,
    }.items():
        update_site_config(key, value)
    frappe.db.set_single_value("Website Settings", "home_page", "ai")
    frappe.db.commit()
    frappe.clear_cache()
    print(f"PASS entry site {site} configured (token not printed)", flush=True)
    frappe.destroy()


if __name__ == "__main__":
    main()
