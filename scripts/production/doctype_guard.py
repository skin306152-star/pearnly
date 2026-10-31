#!/usr/bin/env python3
"""逐站迁移前后各数一次 DocType，变少就停（10-31）。

起因：10-31 本机实验栈迁移时，一个 DocType 控制器导入失败（ImportError），Frappe 把它当成孤儿删掉了 75 个 DocType——迁移本身照样
「成功」退出。生产 `activate-product.sh` 与实验室 `scripts/flows/run.py` 都在每个站点迁移前后调这个脚本：

    doctype_guard.py snapshot <站点>            在 bench 目录里跑（容器内）：打印一行 ``DOCTYPES <JSON 名单>``
    doctype_guard.py compare <前> <后>          前后两份 snapshot 输出（文件）：迁移后少了的打印出来、退出码 86；没少退出 0

有意删掉的 DocType（随发布删的）逐个登记在同目录的 ``doctype-removals.txt``，比较时不算「少了」；没有别的绕过开关。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MARK = "DOCTYPES "
LOST = 86  # 迁移后 DocType 变少：调用方据此停下（与迁移失败的退出码分开）
REMOVALS = Path(__file__).with_name("doctype-removals.txt")


def snapshot(site: str) -> list[str]:
    import frappe

    frappe.init(site=site, sites_path="sites")
    frappe.connect()
    try:
        # 名单就是全部 DocType 的名字（每站几百个），要整份比对，不分页
        return sorted(frappe.db.sql_list("select name from `tabDocType`"))
    finally:
        frappe.destroy()


def parse(text: str, source: str = "snapshot") -> list[str]:
    """snapshot 的输出：容器启动时可能先打印别的行，只认带标记的那一行。"""
    for line in text.splitlines():
        if line.startswith(MARK):
            return json.loads(line[len(MARK):])
    raise SystemExit(f"{source}: no {MARK.strip()} line")


def read(path: str) -> list[str]:
    return parse(Path(path).read_text(encoding="utf-8"), path)


def allowed_removals(path: Path = REMOVALS) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}


def lost(before: list[str], after: list[str], removals: set[str]) -> list[str]:
    return sorted(set(before) - set(after) - removals)


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "snapshot":
        print(MARK + json.dumps(snapshot(argv[1])))
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        before, after = read(argv[1]), read(argv[2])
        missing = lost(before, after, allowed_removals())
        if missing:
            print(f"{len(missing)} DocTypes disappeared during migration ({len(before)} before, {len(after)} after): "
                  + ", ".join(missing), file=sys.stderr)
            return LOST
        print(f"DocTypes kept: {len(before)} before, {len(after)} after")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
