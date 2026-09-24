#!/usr/bin/env python3
"""在测试环境的产品容器里建/迁移一个测试租户站点。

形态与正式开户（infrastructure/production/site-step.py）一致：以 sites 目录为工作目录、
用原生 _new_site、只装 pearnly_core + 该产品自己的 App、common_site_config 按产品组
分配 Redis 库、encryption_key 只写一次。

有意不做的：不接正式账本身份、不配 LINE、不启用 OCR（测试环境不产生付费调用）。
"""
import json
import os
from pathlib import Path
import subprocess
import sys

PRODUCT_BY_APP = {
    "pearnly_dms": "dms",
    "pearnly_erp_portal": "erp",
    "pearnly_cowork": "cowork",
}
QUEUE_DB = {"dms": 1, "erp": 2, "cowork": 3}
SITE_APPS = ["frappe", "pearnly_core", "pearnly_dms", "pearnly_cowork", "pearnly_erp_portal"]
BENCH = Path("/home/frappe/frappe-bench")
SITES = BENCH / "sites"


def bench(*args):
    result = subprocess.run(["bench", *args], cwd=BENCH, text=True, capture_output=True)
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

    # 与正式开户同一个工作目录约定：Frappe 以当前目录作为 sites 路径。
    os.chdir(SITES)
    marker = Path(".pearnly-testenv")
    existing = {p.parent.name for p in Path(".").glob("*/site_config.json")}
    if existing and not marker.exists():
        raise SystemExit("Refusing foreign Site volume")

    common_file = Path("common_site_config.json")
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
    Path("apps.txt").write_text("\n".join(SITE_APPS) + "\n")

    import frappe
    from frappe.installer import _new_site, install_app, update_site_config

    if not Path(site, "site_config.json").exists():
        frappe.init(site=site, new_site=True)
        _new_site(db_name=None, site=site, db_type="mariadb", db_host="mariadb",
                  db_root_username="root",
                  db_root_password=os.environ["TESTENV_DB_ROOT_PASSWORD"],
                  admin_password=os.environ.get("TESTENV_ADMIN_PASSWORD", "testenv-admin"),
                  mariadb_user_host_login_scope="%")
        frappe.destroy()

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

    # 与正式开户同顺序：先 pearnly_core，再该产品自己的 App。
    for app_name in ("pearnly_core", app):
        if app_name not in frappe.get_installed_apps():
            install_app(app_name)
    installed = list(frappe.get_installed_apps())
    frappe.destroy()

    bench("--site", site, "migrate")
    print(json.dumps({"site": site, "group": group, "app": app, "installed_apps": installed}))
    print("SITE_INIT_OK")


if __name__ == "__main__":
    sys.exit(main())
