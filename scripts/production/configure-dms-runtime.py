"""DMS-only native manual-read queue configuration; no Site or external IO.

Run explicitly against the DMS bench common_site_config.json before restarting
its scheduler/workers. Other product benches must not invoke this operation.
"""
import argparse
import copy
import json
from pathlib import Path


def configured(values):
    result = copy.deepcopy(values)
    result.setdefault("workers", {}).setdefault("dms_sync", {})["timeout"] = 420
    # ADR-013: daytime change sync runs every minute, so this DMS bench's
    # scheduler must look every 60 s (Frappe's default is 240 s). Other benches
    # keep their own cadence; this script only ever runs against the DMS bench.
    result["scheduler_tick_interval"] = 60
    return result


def install(path=None):
    target = Path(path or "/home/frappe/frappe-bench/sites/common_site_config.json")
    data = configured(json.loads(target.read_text()))
    temporary = target.with_suffix(".dms-runtime.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.chmod(target.stat().st_mode & 0o777)
    temporary.replace(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path")
    args = parser.parse_args()
    install(args.path)
