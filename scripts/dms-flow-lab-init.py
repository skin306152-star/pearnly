"""Create only the disposable offline DMS Site; no workers or external IO.

Uses the audited release-lab setup with an exact Site allowlist. Only this
Compose project's new Site/DB volumes may be used. Never imports credentials.
"""
import json
import os
from pathlib import Path
import runpy


SITE = "dms-flow-lab.localhost"


def main():
    if os.environ.get("PEARNLY_DMS_FLOW_LAB") != "1":
        raise SystemExit("Requires the task-owned DMS flow laboratory")
    existing = {p.parent.name for p in Path("sites").glob("*/site_config.json")}
    if existing - {SITE}:
        raise SystemExit("Refusing a foreign Site volume")
    setup = runpy.run_path(str(Path(__file__).with_name("unified-release-lab-init.py")))
    setup["main"].__globals__["SITE_APPS"] = {SITE: "pearnly_dms"}
    os.environ["PEARNLY_RELEASE_LAB"] = "1"
    os.environ["PEARNLY_RELEASE_LAB_SITES"] = SITE
    setup["main"]()
    path = Path("sites") / SITE / "site_config.json"
    config = json.loads(path.read_text())
    config.update(pearnly_dms_flow_lab=True, developer_mode=1,
                  pearnly_line_channel_access_token="lab:" + SITE,
                  pearnly_line_channel_secret="dms-flow-lab-only",
                  pearnly_ocr_budget_redis="redis://redis:6379/6",
                  allow_tests=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"READY {SITE}: task-owned DB/Redis, no scheduler, no external credentials")


if __name__ == "__main__":
    main()
