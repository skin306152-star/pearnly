#!/usr/bin/env python3
"""One-off maintenance process; root secret is not mounted in normal services."""
import json
import os
from pathlib import Path
import sys

site = sys.argv[1] if len(sys.argv) > 1 else "billing.internal"
if site not in {"billing.internal", "verify-billing.internal"}:
    raise SystemExit("Unexpected billing Site")
path = Path("/run/secrets/runtime.env")
config = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
os.setgid(1000)
os.setuid(1000)
os.chdir("/home/frappe/frappe-bench/sites")
common = Path("common_site_config.json")
values = json.loads(common.read_text()) if common.exists() else {}
values.update({"db_host": "mariadb", "db_port": 3306, "redis_cache": "redis://redis-cache:6379",
               "redis_queue": "redis://redis-queue:6379", "redis_socketio": "redis://redis-queue:6379",
               "socketio_port": 9000, "serve_default_site": False})
common.write_text(json.dumps(values))
apps = Path("apps.txt")
available = set(apps.read_text().splitlines()) if apps.exists() else set()
available.update({"frappe", "pearnly_control"})
apps.write_text("\n".join(sorted(available)) + "\n")

import frappe
from frappe.installer import _new_site, install_app, update_site_config

if not Path(site, "site_config.json").exists():
    frappe.init(site=site, new_site=True)
    _new_site(db_name=None, site=site, db_type="mariadb", db_host="mariadb",
              db_root_username="root", db_root_password=config["DB_ROOT_PASSWORD"],
              admin_password=config["SITE_ADMIN_PASSWORD"], mariadb_user_host_login_scope="%")
    frappe.destroy()
frappe.init(site=site)
frappe.connect()
frappe.set_user("Administrator")
for key, value in {
    "pearnly_billing_only": True, "pearnly_billing_scoped_tokens": True,
    "pearnly_billing_token": config["BILLING_ROOT_SECRET"],
    "pearnly_billing_management_token": config["BILLING_MANAGEMENT_SECRET"],
    "pearnly_billing_reviewers": ["skinzihao@gmail.com"],
    "allow_tests": site == "verify-billing.internal",
}.items():
    update_site_config(key, value)
if "pearnly_control" not in frappe.get_installed_apps():
    install_app("pearnly_control")
if not frappe.db.exists("User", "skinzihao@gmail.com"):
    frappe.get_doc({"doctype": "User", "email": "skinzihao@gmail.com", "first_name": "Pearnly reviewer",
                    "enabled": 0, "send_welcome_email": 0}).insert(ignore_permissions=True)
frappe.db.set_single_value("System Settings", "time_zone", "Asia/Bangkok")
frappe.db.commit()
frappe.destroy()
print("Billing Site initialized; credentials suppressed")
