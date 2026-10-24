"""ERP-only bench configuration (scheduler tick); no Site or external IO.

Run explicitly against the ERP bench common_site_config.json before restarting
its scheduler/workers. Other product benches must not invoke this operation.
"""
import argparse
import copy
import json
from pathlib import Path


def configured(values):
    result = copy.deepcopy(values)
    # ERP 的 Express 主档同步推进（`masters.advance`）和写账回读（`push.poll`）按每分钟设计；Frappe 默认节拍 240 秒，
    # 不设时「每分钟」实际 4 分钟一次（10-24 实验栈：写单后自动同步要等 4 分钟）。同 DMS（ADR-013 / ADR-014）只改 ERP 这台 bench。
    result["scheduler_tick_interval"] = 60
    return result


def install(path=None):
    target = Path(path or "/home/frappe/frappe-bench/sites/common_site_config.json")
    data = configured(json.loads(target.read_text()))
    temporary = target.with_suffix(".erp-runtime.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.chmod(target.stat().st_mode & 0o777)
    temporary.replace(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path")
    args = parser.parse_args()
    install(args.path)
