"""Install the task-owned, offline DMS snapshot laboratory.

Run inside dms-snapshot-lab.yml from /home/frappe/frappe-bench. Creates only
dms-snapshot-lab.localhost in this project's new volumes. No scheduler or worker
is started; LINE configuration contains explicit fake lab-only values.
"""
import json
import os
from pathlib import Path
import runpy


SITE = "dms-snapshot-lab.localhost"
MARKER = ".dms-snapshot-lab"


def main():
    if os.environ.get("PEARNLY_DMS_SNAPSHOT_LAB") != "1":
        raise SystemExit("Requires the task-owned DMS snapshot laboratory")
    if Path.cwd() != Path("/home/frappe/frappe-bench"):
        raise SystemExit("Requires the isolated bench working directory")
    sites = Path("sites")
    existing = {path.parent.name for path in sites.glob("*/site_config.json")}
    if existing - {SITE} or (existing and not (sites / MARKER).exists()):
        raise SystemExit("Refusing a foreign Site volume")

    setup = runpy.run_path(str(Path(__file__).with_name("unified-release-lab-init.py")))
    setup["main"].__globals__["SITE_APPS"] = {SITE: "pearnly_dms"}
    os.environ["PEARNLY_RELEASE_LAB"] = "1"
    os.environ["PEARNLY_RELEASE_LAB_SITES"] = SITE
    (sites / MARKER).write_text("pearnly-dms-snapshot-lab\n")
    setup["main"]()
    runtime = runpy.run_path(str(Path(__file__).parent / "production/configure-dms-runtime.py"))
    runtime["install"](sites / "common_site_config.json")

    config_path = sites / SITE / "site_config.json"
    config = json.loads(config_path.read_text())
    config.update(
        pearnly_dms_snapshot_lab=True, developer_mode=1, allow_tests=True,
        pearnly_line_channel_access_token="lab:" + SITE,
        pearnly_line_channel_secret="dms-snapshot-lab-only",
        pearnly_ocr_budget_redis="redis://redis:6379/6",
        pause_scheduler=1, disable_scheduler=1, mute_emails=1,
    )
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"READY {SITE}: isolated DB/Redis, no scheduler or external credentials")


if __name__ == "__main__":
    main()
