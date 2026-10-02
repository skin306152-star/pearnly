#!/usr/bin/env python3
"""One-off maintenance process; root secret is not mounted in normal services."""
import json
import os
from pathlib import Path
import sys

site = sys.argv[1] if len(sys.argv) > 1 else "billing.internal"
if site not in {"billing.internal", "verify-billing.internal"}:
    raise SystemExit("Unexpected billing Site")
def endpoints(runtime):
    """数据库与 Redis 的地址（runtime.env 可改；默认是同一台机器 compose 里的服务名）。扩容时把数据库或 Redis 挪到别的机器，
    只改 runtime.env 的 PEARNLY_DB_HOST / PEARNLY_DB_PORT / PEARNLY_REDIS_CACHE_URL / PEARNLY_REDIS_QUEUE_URL，不改代码；
    开户与计费初始化不再把它们写回服务名（2026-10-02 可拆分自检）。"""
    return {"db_host": (runtime.get("PEARNLY_DB_HOST") or "mariadb").strip(),
            "db_port": int((runtime.get("PEARNLY_DB_PORT") or "3306").strip()),
            "cache": (runtime.get("PEARNLY_REDIS_CACHE_URL") or "redis://redis-cache:6379").strip().rstrip("/"),
            "queue": (runtime.get("PEARNLY_REDIS_QUEUE_URL") or "redis://redis-queue:6379").strip().rstrip("/")}


path = Path("/run/secrets/runtime.env")
config = dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
member_config = json.loads(Path("/run/secrets/member-identity.json").read_text())
member_keys = {"pearnly_member_identity_issuer", "pearnly_member_identity_admin_client_id",
               "pearnly_member_identity_admin_client_secret", "pearnly_member_identity_ownership_key"}
# Cowork's own account pool (optional until it exists): all three keys or none.
cowork_keys = {"pearnly_member_identity_cowork_issuer", "pearnly_member_identity_cowork_admin_client_id",
               "pearnly_member_identity_cowork_admin_client_secret"}
present = set(member_config)
if (present - cowork_keys != member_keys or present & cowork_keys not in (set(), cowork_keys)
        or not all(member_config.values())):
    raise SystemExit("Private member identity configuration is incomplete")
os.setgid(1000)
os.setuid(1000)
os.chdir("/home/frappe/frappe-bench/sites")
common = Path("common_site_config.json")
values = json.loads(common.read_text()) if common.exists() else {}
hosts = endpoints(config)
values.update({"db_host": hosts["db_host"], "db_port": hosts["db_port"], "redis_cache": hosts["cache"],
               "redis_queue": hosts["queue"], "redis_socketio": hosts["queue"],
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
    _new_site(db_name=None, site=site, db_type="mariadb", db_host=hosts["db_host"], db_port=hosts["db_port"],
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
    **member_config,
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
