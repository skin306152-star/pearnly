"""Install isolated test Sites. Writes only the named lab volume, no external IO.

Run through infrastructure/compose/cowork-backend-lab.yml, never a shared bench.
Does not create production tenants, modify identity services or enable a scheduler.
"""
import json
import os
from pathlib import Path
import subprocess


def main():
    if os.environ.get("PEARNLY_COWORK_BACKEND_LAB") != "1":
        raise SystemExit("This command requires the dedicated Cowork backend lab")
    sites = Path("sites")
    sites.mkdir(exist_ok=True)
    marker = sites / ".cowork-backend-lab"
    allowed = {"cowork-backend-a.localhost", "cowork-backend-b.localhost",
               "cowork-backend-fresh.localhost", "cowork-backend-files.localhost",
               "cowork-backend-execution.localhost"}
    requested = set(os.environ.get("PEARNLY_COWORK_LAB_SITES", "").split()) or allowed
    if not requested <= allowed:
        raise SystemExit("Refusing an unknown lab Site")
    existing = {p.parent.name for p in sites.glob("*/site_config.json")}
    if existing - allowed or (existing and not marker.exists()):
        raise SystemExit("Refusing an existing or foreign Site volume")
    config_file = sites / "common_site_config.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    if config.get("db_host") not in (None, "db"):
        raise SystemExit("Refusing a foreign database")
    config.update(db_host="db", redis_cache="redis://redis:6379/0",
                  redis_queue="redis://redis:6379/1", redis_socketio="redis://redis:6379/1",
                  pearnly_ocr_budget_redis="redis://redis:6379/6",
                  developer_mode=1, pause_scheduler=1, disable_scheduler=1,
                  serve_default_site=False)
    config_file.write_text(json.dumps(config, indent=2) + "\n")
    marker.write_text("pearnly-cowork-backend-lab\n")
    (sites / "apps.txt").write_text("frappe\npearnly_core\npearnly_cowork\n")
    for name in sorted(requested):
        site_file = sites / name / "site_config.json"
        if not site_file.exists():
            subprocess.run(["bench", "new-site", name, "--db-host", "db",
                            "--db-root-password", "cowork-backend-lab-only",
                            "--admin-password", "cowork-backend-lab-only",
                            "--mariadb-user-host-login-scope", "%"], check=True)
        subprocess.run(["bench", "--site", name, "install-app", "pearnly_core", "pearnly_cowork"], check=True)
        site_config = json.loads(site_file.read_text())
        site_config.update(pearnly_product="cowork", pearnly_backend_lab=True,
                           pearnly_features=["intake", "ocr_history", "master_data", "push_log", "stocktake", "recon"],
                           pause_scheduler=1, disable_scheduler=1, mute_emails=1)
        site_file.write_text(json.dumps(site_config, indent=2) + "\n")
        subprocess.run(["bench", "--site", name, "migrate"], check=True)
    print("Cowork backend lab ready; guarded local Sites, no scheduler or external credentials")


if __name__ == "__main__":
    main()
