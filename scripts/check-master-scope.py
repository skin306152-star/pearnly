#!/usr/bin/env python3
"""主档按公司可见的口径闸门：拦新写的「这家公司看得到哪些客户 / 供应商 / 物料」（公司接入重做第 2 批，2026-11-14）。

起因：11-13 用户贴图，同步上来的物料在物料调整的物料框里看不到；查下来「本公司能看哪些主档」散成 1 个权限钩子 + 5 个函数 +
17 处手写，口径各不相同（会话当前公司 / 单据公司、收款只认账本档、盘点只列有结存的），新入口照哪处抄就是哪种口径。现在口径
只在 `apps/pearnly_erp_portal/pearnly_erp_portal/masters/access.py`（`usable` / `scope_sql` / `scope_or_filters` / `ledger_filters` /
权限钩子 `master_query`），选择框搜索在 `masters/link_search.py`。

算作「自己写口径」：ERP App 的 Python 代码里出现归属列名 ``express_company`` 字样（条件、字段、比较都算）。同步自己写归属
（`express/masters.py` 等）、单据随单建档记归属、编码判重的这些存量登记在 scripts/master-scope-baseline.json（按文件计数）：
**只许减少不许增加**，新文件出现就拦。要判「能不能用」「列哪些」请调 `masters.access` 的函数（字段名用 `access.OWNER`）。

    python3 scripts/check-master-scope.py            # 检查
    python3 scripts/check-master-scope.py --list     # 逐处列出
    python3 scripts/check-master-scope.py --update   # 按当前代码重写基线（只能减少）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "apps" / "pearnly_erp_portal" / "pearnly_erp_portal"
BASELINE = ROOT / "scripts" / "master-scope-baseline.json"
HOME = "apps/pearnly_erp_portal/pearnly_erp_portal/masters/access.py"  # 口径唯一的家，不计
PATTERN = re.compile(r"\bexpress_company\b")
SKIP_PARTS = {"tests", "patches", "__pycache__", "doctype"}


def scan() -> dict:
    found = {}
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == HOME or SKIP_PARTS & set(path.relative_to(APP).parts) or path.name.startswith("test_"):
            continue
        lines = [number for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if PATTERN.search(line)]
        if lines:
            found[rel] = lines
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    found = scan()
    counts = {path: len(lines) for path, lines in found.items()}
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    if args.list:
        for path, lines in found.items():
            for number in lines:
                print(f"{path}:{number}")
    over = {path: (count, baseline.get(path, 0)) for path, count in counts.items() if count > baseline.get(path, 0)}
    if args.update:
        if over and BASELINE.exists():
            for path, (count, allowed) in over.items():
                print(f"拒绝：{path} 从 {allowed} 增加到 {count}", file=sys.stderr)
            return 1
        BASELINE.write_text(json.dumps(counts, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"基线已写：{sum(counts.values())} 处 / {len(counts)} 个文件")
        return 0
    if over:
        for path, (count, allowed) in over.items():
            print(f"FAIL {path}: express_company 出现 {count} 处（基线 {allowed}）——判可见 / 列范围请调 masters.access", file=sys.stderr)
        return 1
    stale = {path for path in baseline if counts.get(path, 0) < baseline[path]}
    print(f"RESULT: PASS（{sum(counts.values())} 处 / {len(counts)} 个文件；可下调基线 {len(stale)} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
