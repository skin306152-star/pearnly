#!/usr/bin/env python3
"""在计费服务（billing-web）那套程序上建 / 更新智能体入口站点 fatcc.com（ADR-019，清单第 23 项）。

一次性维护进程，同 bootstrap-billing.py 的挂法（runtime.env 只读挂到 /run/secrets，不进常驻服务）：
站点只装 pearnly_control，写 `pearnly_assistant_entry*` 配置，首页指向入口页 ai；查询令牌由 billing.internal 的
根密钥派生（不打印）。只接受 fatcc.com，避免误建。可重复执行。

docker run --rm --user 0 --network pearnly-production_runtime \
  -v pearnly-production_sites-billing:/home/frappe/frappe-bench/sites \
  -v /opt/pearnly/deploy/infrastructure/production/bootstrap-ai-entry.py:/run/bootstrap-ai-entry.py:ro \
  -v /opt/pearnly/secrets/runtime.env:/run/secrets/runtime.env:ro \
  --entrypoint /home/frappe/frappe-bench/env/bin/python "$IMAGE" /run/bootstrap-ai-entry.py fatcc.com
"""
import json
import os
from pathlib import Path
import sys

SITE = sys.argv[1] if len(sys.argv) > 1 else ""
if SITE != "fatcc.com":
    raise SystemExit("Unexpected entry Site")
BILLING = "billing.internal"
DEFAULT_ISSUER = "https://auth.pearnly.com/realms/pearnly-business"

config = dict(line.split("=", 1) for line in Path("/run/secrets/runtime.env").read_text().splitlines() if "=" in line)
os.setgid(1000)
os.setuid(1000)
os.chdir("/home/frappe/frappe-bench/sites")
root = json.loads(Path(BILLING, "site_config.json").read_text()).get("pearnly_billing_token") or ""
if not root:
    raise SystemExit("billing.internal has no service token")
db_host = (config.get("PEARNLY_DB_HOST") or "mariadb").strip()
db_port = int((config.get("PEARNLY_DB_PORT") or "3306").strip())

import frappe  # noqa: E402
from frappe.installer import _new_site, install_app, update_site_config  # noqa: E402

if not Path(SITE, "site_config.json").exists():
    frappe.init(site=SITE, new_site=True)
    _new_site(db_name=None, site=SITE, db_type="mariadb", db_host=db_host, db_port=db_port,
              db_root_username="root", db_root_password=config["DB_ROOT_PASSWORD"],
              admin_password=config["SITE_ADMIN_PASSWORD"], mariadb_user_host_login_scope="%")
    frappe.destroy()
    print(f"PASS new site {SITE}", flush=True)
frappe.init(site=SITE)
frappe.connect()
frappe.set_user("Administrator")
if "pearnly_control" not in frappe.get_installed_apps():
    install_app("pearnly_control", set_as_patched=True)
    print("PASS install pearnly_control", flush=True)
from pearnly_control.assistant_entry import contracts  # noqa: E402

for key, value in {
    "pearnly_assistant_entry": 1,
    "pearnly_assistant_entry_url": f"https://{SITE}",
    "pearnly_assistant_entry_billing_url": "http://billing-web:8000",
    "pearnly_assistant_entry_billing_host": BILLING,
    "pearnly_assistant_entry_token": contracts.entry_token(root),
    "pearnly_assistant_entry_default_issuer": DEFAULT_ISSUER,
}.items():
    update_site_config(key, value)
frappe.db.set_single_value("Website Settings", "home_page", "ai")
frappe.db.commit()
frappe.clear_cache()
print(f"PASS entry site {SITE} configured (token not printed)", flush=True)
frappe.destroy()
