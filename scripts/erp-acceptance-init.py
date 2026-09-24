"""Initialize synthetic tenants in the dedicated immutable ERP acceptance stack.

Local writes only: installs the production ERP app set into two empty Sites.
No production credentials, external requests, data deletion, or source mounting.
Refuses existing foreign volumes. Rerunning preserves existing Site accounts/data.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess


ROOT = Path("/home/frappe/frappe-bench")
SITES = (("erp-acceptance-fresh.localhost",) if os.environ.get("PEARNLY_ERP_ACCEPTANCE_FRESH") == "1"
         else ("erp-pos-a.localhost", "erp-pos-b.localhost"))
APPS = ("frappe", "pearnly_core", "pearnly_erp_portal")


def bench(*args):
    subprocess.run(["bench", *args], cwd=ROOT, check=True)


def configure_system(name):
    """Read back from testerp production on 2026-09-24; keep user language separate."""
    import frappe

    assert os.environ.get("PEARNLY_ERP_ACCEPTANCE") == "1" and name in SITES
    os.chdir(ROOT / "sites")
    frappe.init(site=name, sites_path=str(ROOT / "sites"))
    frappe.connect()
    assert frappe.conf.pearnly_erp_acceptance
    frappe.set_user("Administrator")
    frappe.db.set_single_value("System Settings", {"time_zone": "Asia/Bangkok", "language": "th",
                              "date_format": "yyyy-mm-dd", "time_format": "HH:mm:ss"})
    frappe.db.commit()
    frappe.clear_cache()
    frappe.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure-runtime", action="store_true",
                        help="Update only common runtime configuration of initialized acceptance Sites")
    args = parser.parse_args()
    if os.environ.get("PEARNLY_ERP_ACCEPTANCE") != "1":
        raise SystemExit("Dedicated ERP acceptance container required")
    sites = ROOT / "sites"
    marker = sites / ".erp-acceptance"
    existing = {p.parent.name for p in sites.glob("*/site_config.json")}
    if existing - set(SITES) or (existing and not marker.exists()):
        raise SystemExit("Refusing a foreign Site volume")
    if args.configure_runtime and (existing != set(SITES) or not marker.is_file()):
        raise SystemExit("Initialized acceptance Sites required for runtime-only configuration")
    if args.configure_runtime and any(not json.loads((sites / name / "site_config.json").read_text())
                                      .get("pearnly_erp_acceptance") for name in SITES):
        raise SystemExit("Refusing a non-acceptance Site")
    common_file = sites / "common_site_config.json"
    common = json.loads(common_file.read_text()) if common_file.exists() else {}
    if common.get("db_host") not in (None, "db"):
        raise SystemExit("Refusing a foreign database")
    # db is an alias for production's mariadb service; legacy lab test guards
    # require it. Separate cache/queue, queue DB 2 and runtime mode match production.
    common.update(db_host="db", redis_cache="redis://redis-cache:6379",
                  redis_queue="redis://redis-queue:6379/2",
                  redis_socketio="redis://redis-queue:6379/2", socketio_port=9000,
                  developer_mode=0, serve_default_site=False,
                  pause_scheduler=0, disable_scheduler=0,
                  # Match production/site-step.py: custom OCR queue and its
                  # dedicated non-evicting capacity database are runtime config.
                  pearnly_ocr_budget_redis="redis://redis-queue:6379/6",
                  workers={**common.get("workers", {}), "ocr": {"timeout": 600}})
    common_file.write_text(json.dumps(common, indent=2) + "\n")
    if args.configure_runtime:
        print("Acceptance common runtime configured; Site settings and data preserved")
        return
    (sites / "apps.txt").write_text("\n".join(APPS) + "\n")
    marker.write_text("pearnly-erp-acceptance\n")
    (sites / ".erp-pos-lab").write_text("synthetic acceptance tenants\n")
    catalog = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/features.json"
    features = ["home_subscription", *[r["code"] for r in json.loads(catalog.read_text())["features"]]]
    for name in SITES:
        config_file = sites / name / "site_config.json"
        if not config_file.exists():
            bench("new-site", name, "--db-host", "db", "--db-root-password", "erp-acceptance-only",
                  "--admin-password", "erp-acceptance-only", "--mariadb-user-host-login-scope", "%")
            bench("--site", name, "install-app", *APPS[1:])
        config = json.loads(config_file.read_text())
        config.update(pearnly_product="erp", pearnly_features=features,
                      pearnly_erp_pos_lab=True, pearnly_erp_acceptance=True,
                      developer_mode=0, pause_scheduler=0, disable_scheduler=0, mute_emails=1)
        config_file.write_text(json.dumps(config, indent=2) + "\n")
        bench("--site", name, "migrate")
        configure_system(name)
        bench("--site", name, "enable-scheduler")
        bench("--site", name, "list-apps")
    print(f"{len(SITES)} synthetic acceptance Sites initialized; production app set and runtime mode")


if __name__ == "__main__":
    main()
