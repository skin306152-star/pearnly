"""Install only dedicated release-lab Sites, with no schedulers or external IO.

Execute inside infrastructure/compose/unified-release-lab.yml. The marker,
Site allowlist and database host checks prevent reusing a shared bench.
"""
import json
import os
from pathlib import Path
import subprocess


SITE_APPS = {
    "release-erp.localhost": "pearnly_erp_portal",
    "release-dms.localhost": "pearnly_dms",
    "release-cowork.localhost": "pearnly_cowork",
    "release-control.localhost": "pearnly_control",
    "release-dms-upgrade.localhost": "pearnly_dms",
    "release-erp-line-a.localhost": "pearnly_erp_portal",
    "release-erp-line-b.localhost": "pearnly_erp_portal",
}
PRODUCTS = {
    "pearnly_erp_portal": "erp", "pearnly_dms": "dms",
    "pearnly_cowork": "cowork", "pearnly_control": "control",
}
PASSWORD = "unified-release-lab-only"


def bench(*args):
    result = subprocess.run(["bench", *args], text=True, capture_output=True)
    if result.returncode:
        print(result.stdout[-12000:])
        print(result.stderr[-12000:])
        result.check_returncode()
    print(f"PASS bench {' '.join(args[:3])}", flush=True)


def main():
    if os.environ.get("PEARNLY_RELEASE_LAB") != "1":
        raise SystemExit("Requires the dedicated unified-release laboratory")
    sites = Path("sites")
    marker = sites / ".unified-release-lab"
    existing = {p.parent.name for p in sites.glob("*/site_config.json")}
    requested = set(os.environ.get("PEARNLY_RELEASE_LAB_SITES", "").split()) or {
        "release-erp.localhost", "release-dms.localhost", "release-cowork.localhost",
        "release-control.localhost",
    }
    if not requested <= set(SITE_APPS):
        raise SystemExit("Refusing unknown lab Sites")
    if existing - set(SITE_APPS) or (existing and not marker.exists()):
        raise SystemExit("Refusing foreign Site volume")
    config_file = sites / "common_site_config.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    if config.get("db_host") not in (None, "db"):
        raise SystemExit("Refusing foreign database host")
    config.update(
        db_host="db", redis_cache="redis://redis:6379/0",
        redis_queue="redis://redis:6379/1", redis_socketio="redis://redis:6379/2",
        max_queued_jobs=100000, pause_scheduler=1, disable_scheduler=1,
        mute_emails=1, serve_default_site=False,
    )
    config_file.write_text(json.dumps(config, indent=2) + "\n")
    marker.write_text("pearnly-release-lab\n")
    (sites / "apps.txt").write_text(
        "frappe\npearnly_core\n" + "\n".join(dict.fromkeys(SITE_APPS.values())) + "\n"
    )
    for name in sorted(requested):
        app = SITE_APPS[name]
        site_file = sites / name / "site_config.json"
        if not site_file.exists():
            bench("new-site", name, "--db-host", "db", "--db-root-password", PASSWORD,
                  "--admin-password", PASSWORD, "--mariadb-user-host-login-scope", "%")
        site_config = json.loads(site_file.read_text())
        site_config.update(
            pearnly_product=PRODUCTS[app], pearnly_release_lab=True,
            pause_scheduler=1, disable_scheduler=1, mute_emails=1,
        )
        catalog = Path(f"apps/{app}/{app}/features.json")
        if catalog.exists():
            features = json.loads(catalog.read_text())["features"]
            site_config["pearnly_features"] = ["home_subscription", *[v["code"] for v in features]]
        site_file.write_text(json.dumps(site_config, indent=2) + "\n")
        bench("--site", name, "install-app", "pearnly_core", app)
        bench("--site", name, "migrate")
        result = subprocess.run(["bench", "--site", name, "list-apps"],
                                check=True, text=True, capture_output=True)
        if "erpnext" in result.stdout.split():
            raise SystemExit(f"{name}: unexpected ERPNext installed")
        print(f"READY {name}: {result.stdout.strip()}", flush=True)
    print("Release laboratory ready; isolated network, no scheduler or external credentials")


if __name__ == "__main__":
    main()
