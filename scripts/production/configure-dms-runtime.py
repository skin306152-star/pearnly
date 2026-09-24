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
    # Manual reads enqueue directly. Do not change a bench's scheduler cadence.
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
