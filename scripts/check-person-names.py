#!/usr/bin/env python3
"""人名显示闸门：拦新代码绕过共用显示名、自己取人名（用户 10-30：个人信息加姓名，显示用户名的地方改完必须全部覆盖）。

一个人显示成什么只由 `pearnly_core.account.names` 决定（`display_name` / `display_names`：有姓名用姓名，没填过的老账号是用户名）。
各处自己读 `full_name`、调 `get_fullname`、在页面里查 `frappe.user_info(...).fullname`，就会和统一口径走散（缓存里没有的人显示成
邮箱、改名后旧快照不变）。服务端统一给好显示名，页面只显示接口给的那一格。

算作「自己取人名」：
- Python（apps/**/*.py，测试与迁移补丁除外）：`get_fullname(`、`user_info(`、读 `full_name` 字段（`"full_name"` / `.full_name`）。
- 前端（apps/**/public/**/*.js，构建产物除外）：`user_info(`、`.fullname`、`user_fullname`、`full_name()`。

确实不是「显示一个人」的（建号时写 `full_name`、框架自己的会话快照兜底）在那一行写 `# person-name: 原因` / `// person-name: 原因`。
存量登记在 scripts/person-names-baseline.json（按文件计数）：**只许减少不许增加**；改掉一处后用 `--update` 把基线降下来。

    python3 scripts/check-person-names.py            # 检查；有文件超出基线就失败
    python3 scripts/check-person-names.py --list     # 逐处列出（文件:行）
    python3 scripts/check-person-names.py --update   # 按当前代码重写基线（只能在减少时用；增加时拒绝）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = Path("apps/pearnly_core/pearnly_core/account/names.py")
PY_PATTERNS = (re.compile(r"\bget_fullname\("), re.compile(r"\buser_info\("), re.compile(r"[\"']full_name[\"']"),
               re.compile(r"\.full_name\b"))
JS_PATTERNS = (re.compile(r"\buser_info\("), re.compile(r"\.fullname\b"), re.compile(r"\buser_fullname\b"),
               re.compile(r"\bfull_name\(\)"))
SKIP_PARTS = {"node_modules", "tests", "patches", "__pycache__", "vendor"}


def _hits(path: Path, patterns, marker: str, comment: str) -> list[int]:
    hits = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if marker in line or line.lstrip().startswith(comment):
            continue
        if any(pattern.search(line) for pattern in patterns):
            hits.append(lineno)
    return hits


def scan(root: Path = ROOT) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted((root / "apps").rglob("*.py")):
        rel = path.relative_to(root)
        if SKIP_PARTS & set(rel.parts) or path.name.startswith("test_") or rel == SHARED:
            continue
        if hits := _hits(path, PY_PATTERNS, "# person-name:", "#"):
            found[str(rel)] = hits
    for path in sorted((root / "apps").rglob("*.js")):
        rel = path.relative_to(root)
        if "public" not in rel.parts or SKIP_PARTS & set(rel.parts) or ".bundle." in path.name:
            continue
        if path.name.endswith(".min.js") or "assistant" in rel.parts or "pearnly-app" in rel.parts:
            continue  # 构建产物
        if hits := _hits(path, JS_PATTERNS, "// person-name:", "//"):
            found[str(rel)] = hits
    return found


def compare(found: dict[str, list[int]], baseline: dict[str, int]) -> list[str]:
    problems = []
    for file, hits in sorted(found.items()):
        allowed = int(baseline.get(file, 0))
        if len(hits) > allowed:
            problems.append(f"{file}: {len(hits)} 处自己取人名（基线 {allowed}），行 {', '.join(map(str, hits))}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="逐处列出")
    parser.add_argument("--update", action="store_true", help="按当前代码降低基线（不许增加）")
    parser.add_argument("--repo", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    found = scan(args.repo)
    baseline_path = args.repo / "scripts" / "person-names-baseline.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path.exists() else {}
    problems = compare(found, baseline)
    if args.list:
        for file, hits in sorted(found.items()):
            print(f"{file}: {', '.join(map(str, hits))}")
    if args.update:
        if problems and baseline_path.exists():
            print("基线只许减少：下面这些超出了，先改走 pearnly_core.account.names，或写 person-name 原因", file=sys.stderr)
            print("\n".join(problems), file=sys.stderr)
            return 1
        baseline_path.write_text(json.dumps({file: len(hits) for file, hits in sorted(found.items())},
                                            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"基线已更新：{len(found)} 个文件、{sum(map(len, found.values()))} 处")
        return 0
    if problems:
        print("人名显示闸门：有新增的自己取人名（显示一个人一律用 pearnly_core.account.names 的 display_name / display_names，"
              "页面显示接口给的名字；确实不是显示人名的写 person-name 原因）")
        print("\n".join(problems))
        return 1
    total = sum(map(len, found.values()))
    print(f"人名显示闸门：通过（存量 {total} 处、{len(found)} 个文件，未超出基线）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
