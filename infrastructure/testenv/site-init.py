#!/usr/bin/env python3
"""在测试环境的产品容器里建/迁移一个测试租户站点。

与正式开户保持同一形态：common_site_config 按产品组分配 Redis 库，
站点只装 pearnly_core + 该产品自己的 App，encryption_key 只写一次。
不写正式账本、不启用 OCR、默认不配 LINE。
"""
import json
import os
from pathlib import Path
import subprocess
import sys

os.chdir("/home/frappe/frappe-bench")

PRODUCT_BY_APP = {
    "pearnly_dms": "dms",
    "pearnly_erp_portal": "erp",
    "pearnly_cowork": "cowork",
}
QUEUE_DB = {"dms": 1, "erp": 2, "cowork": 3}
SITE_APPS = ["frappe", "pearnly_core", "pearnly_dms", "pearnly_cowork", "pearnly_erp_portal"]


def bench(*args):
    result = subprocess.run(["bench", *args], text=True, capture_output=True)
    if result.returncode:
        print(result.stdout[-8000:])
        print(result.stderr[-8000:])
        result.check_returncode()
    print(f"PASS bench {' '.join(args[:4])}", flush=True)


def main():
    site = os.environ["TESTENV_SITE"]
    group = os.environ["TESTENV_GROUP"]
    app = os.environ["TESTENV_APP"]
    if PRODUCT_BY_APP.get(app) != group:
        raise SystemExit(f"Refusing {app} for group {group}")

    sites = Path("sites")
    marker = sites / ".pearnly-testenv"
    existing = {p.parent.name for p in sites.glob("*/site_config.json")}
    if existing and not marker.exists():
        raise SystemExit("Refusing foreign Site volume")

    common_file = sites / "common_site_config.json"
    common = json.loads(common_file.read_text()) if common_file.exists() else {}
    if common.get("db_host") not in (None, "mariadb"):
        raise SystemExit("Refusing foreign database host")
    queue_db = QUEUE_DB[group]
    common.update(
        db_host="mariadb", db_port=3306,
        redis_cache="redis://redis-cache:6379",
        redis_queue=f"redis://redis-queue:6379/{queue_db}",
        redis_socketio=f"redis://redis-queue:6379/{queue_db}",
        socketio_port=9000, serve_default_site=False,
        workers={**common.get("workers", {}), "ocr": {"timeout": 600}},
    )
    common_file.write_text(json.dumps(common))
    marker.write_text("pearnly-testenv\n")
    (sites / "apps.txt").write_text("\n".join(SITE_APPS) + "\n")

    if not (sites / site / "site_config.json").exists():
        bench("new-site", site, "--db-host", "mariadb", "--db-root-username", "root",
              "--db-root-password", os.environ["TESTENV_DB_ROOT_PASSWORD"],
              "--admin-password", os.environ.get("TESTENV_ADMIN_PASSWORD", "testenv-admin"),
              "--mariadb-user-host-login-scope", "%")

    import frappe
    from frappe.installer import install_app, update_site_config
    frappe.init(site=site)
    frappe.connect()
    frappe.set_user("Administrator")

    def config(**kwargs):
        for key, value in kwargs.items():
            update_site_config(key, value)
            frappe.conf[key] = value
        frappe.clear_cache()

    if not frappe.conf.get("encryption_key"):
        from cryptography.fernet import Fernet
        config(encryption_key=Fernet.generate_key().decode())
    config(host_name="http://" + site, developer_mode=0, maintenance_mode=0,
           enable_scheduler=True, pearnly_product=group)

    if app not in frappe.get_installed_apps():
        install_app(app)
    installed = [a for a in frappe.get_installed_apps()]
    frappe.destroy()

    bench("--site", site, "migrate")
    print(json.dumps({"site": site, "group": group, "app": app, "installed_apps": installed}))
    print("SITE_INIT_OK")


if __name__ == "__main__":
    sys.exit(main())
