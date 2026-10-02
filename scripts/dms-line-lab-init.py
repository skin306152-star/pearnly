"""Idempotent isolated Site setup; creates only named local test Sites."""
import json
from pathlib import Path
import subprocess
from dms_line_lab.config import channel_config

sites = Path("sites")
sites.mkdir(exist_ok=True)
config_file = sites / "common_site_config.json"
config = json.loads(config_file.read_text()) if config_file.exists() else {}
if config.get("db_host") not in (None, "db"):
    raise SystemExit("Refusing to initialize an existing foreign bench")
config.update(db_host="db", redis_cache="redis://redis:6379/0",
              redis_queue="redis://redis:6379/1", redis_socketio="redis://redis:6379/1",
        socketio_port=8196, webserver_port=8000,
        pearnly_ocr_budget_redis="redis://redis:6379/6", workers={"ocr": {"timeout": 600}, "dms_sync": {"timeout": 420}}, developer_mode=1, serve_default_site=False)
config_file.write_text(json.dumps(config, indent=2))
apps_file = sites / "apps.txt"
apps = apps_file.read_text().splitlines() if apps_file.exists() else []
apps_file.write_text("\n".join(dict.fromkeys(apps + ["frappe", "erpnext", "pearnly_core", "pearnly_dms"])) + "\n")
for name in ("dms-line-lab.localhost", "dms-line-lab-b.localhost"):
    if not (sites / name / "site_config.json").exists():
        subprocess.run(["bench", "new-site", name, "--db-host", "db",
                        "--db-root-password", "line-lab-local-only",
                        "--admin-password", "line-lab-local-only",
                        "--mariadb-user-host-login-scope", "%"], check=True)
    subprocess.run(["bench", "--site", name, "install-app", "pearnly_core", "pearnly_dms"], check=True)
    subprocess.run(["bench", "--site", name, "migrate"], check=True)
    subprocess.run(["bench", "--site", name, "enable-scheduler"], check=True)
    site_config_file = sites / name / "site_config.json"
    site_config = json.loads(site_config_file.read_text())
    current_token = site_config.get("pearnly_line_channel_access_token", "")
    if current_token and current_token != "lab:" + name:
        raise SystemExit("Refusing to replace non-lab LINE credentials")
    site_config.update(channel_config(name))
    site_config_file.write_text(json.dumps(site_config, indent=2))
