#!/usr/bin/env python3
"""流程用例覆盖闸（主会话 10-28 第 3 道闸）：改到产品入口文件却没有流程用例登记它 → 失败。

    python3 scripts/flows/coverage.py                     # 与 origin/main 的分叉点比
    python3 scripts/flows/coverage.py --base <提交>        # 或环境变量 FLOWS_BASE（CI 里 PR 传 HEAD^1）
    python3 scripts/flows/coverage.py --files a.js b.py    # 直接给文件（自测用）
    python3 scripts/flows/coverage.py --list               # 列出全仓入口文件里还没有流程的（存量清单，不判失败）
    python3 scripts/flows/coverage.py --status [--days 30] # 现状清单（Markdown）：按产品分组、按近 N 天改动次数排序，标出有没有流程

产品入口文件：产品 App 的前端脚本与页面（public / page / doctype 的 JS、www 页面）、前端单页源码（frontend/src、frontend/assistant/src），
以及带 `@frappe.whitelist` 的服务端模块。登记在 `tests/flows/registry.toml` 各流程的 `covers` 里（glob）。
确实没有业务流程可跑的（纯样式、只给框架加载的声明等）登记到 `tests/flows/not_flows.toml`，逐条写原因，只许按文件登记、不许整目录。
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "tests" / "flows" / "registry.toml"
NOT_FLOWS = ROOT / "tests" / "flows" / "not_flows.toml"
ENTRY_PATTERNS = [
    "apps/*/*/public/**/*.js", "apps/*/*/**/page/**/*.js", "apps/*/*/**/doctype/**/*.js", "apps/*/*/www/**/*.html",
    "apps/*/*/www/**/*.js", "frontend/src/**/*.ts", "frontend/src/**/*.vue", "frontend/assistant/src/**/*.ts",
    "frontend/assistant/src/**/*.tsx",
]
SKIP = ("/vendor/", "/dist/", "/node_modules/", "/public/assistant/", "/test", ".test.", ".spec.", ".d.ts", "/official-pos/")
WHITELIST = re.compile(r"^\s*@frappe\.whitelist\b", re.M)


def match(path, pattern) -> bool:
    # fnmatch 的 * 本来就跨目录；`**/` 也要能匹配零层目录
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("**/", ""))


def is_entry(path: str) -> bool:
    if any(s in path for s in SKIP) or not (ROOT / path).is_file():
        return False
    if any(match(path, p) for p in ENTRY_PATTERNS):
        return True
    if path.startswith("apps/") and path.endswith(".py") and "/tests/" not in path and "/patches/" not in path:
        return bool(WHITELIST.search((ROOT / path).read_text(encoding="utf-8", errors="replace")))
    return False


def registered() -> tuple[list[str], dict[str, str]]:
    covers = [c for flow in tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["flow"] for c in flow.get("covers", [])]
    exempt = {}
    if NOT_FLOWS.exists():
        exempt = {e["file"]: e.get("reason", "") for e in tomllib.loads(NOT_FLOWS.read_text(encoding="utf-8")).get("file", [])}
    return covers, exempt


PRODUCTS = [("apps/pearnly_erp_portal/", "ERP"), ("apps/pearnly_cowork/", "Cowork"), ("apps/pearnly_dms/", "DMS"),
            ("apps/pearnly_control/", "控制面"), ("apps/pearnly_core/", "共用层"), ("frontend/assistant/", "CC 对话页"),
            ("frontend/src/", "共用单页")]


def change_counts(days) -> tuple[dict, dict]:
    """每个文件近 ``days`` 天与全部历史的提交次数（按 main 历史，合并提交不计）。"""
    def counts(*extra):
        out = subprocess.run(["git", "log", "--no-merges", "--format=", "--name-only", *extra, "HEAD", "--", "apps", "frontend"],
                             cwd=ROOT, capture_output=True, text=True).stdout.split()
        found = {}
        for path in out:
            found[path] = found.get(path, 0) + 1
        return found
    return counts(f"--since={days}.days"), counts()


def status(covers, exempt, days) -> str:
    """现状清单：入口 → 有没有流程，按产品分组、按近期改动次数（再按全部历史）倒序。"""
    tracked = subprocess.run(["git", "ls-files", "apps", "frontend"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    recent, total = change_counts(days)
    flows = tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["flow"]
    rows = []
    for path in (p for p in tracked if is_entry(p)):
        product = next((name for prefix, name in PRODUCTS if path.startswith(prefix)), "其他")
        covered = [f["id"] for f in flows if any(match(path, c) for c in f.get("covers", []))]
        state = ", ".join(covered) if covered else ("不需要（已登记）" if path in exempt else "没有")
        rows.append((product, path, recent.get(path, 0), total.get(path, 0), state))
    lines = [f"| 产品 | 入口文件 | 有流程 | 不需要 | 没有流程 |", "|---|---|---|---|---|"]
    for _, name in PRODUCTS + [("", "其他")]:
        group = [r for r in rows if r[0] == name]
        if group:
            lines.append(f"| {name} | {len(group)} | {sum(r[4] not in ('没有', '不需要（已登记）') for r in group)} | "
                         f"{sum(r[4] == '不需要（已登记）' for r in group)} | {sum(r[4] == '没有' for r in group)} |")
    for _, name in PRODUCTS + [("", "其他")]:
        group = sorted((r for r in rows if r[0] == name), key=lambda r: (-r[2], -r[3], r[1]))
        if not group:
            continue
        lines += ["", f"#### {name}（{len(group)} 个，近 {days} 天改动次数倒序）", "", "| 入口 | 近 {0} 天 | 全部 | 流程 |".format(days),
                  "|---|---|---|---|"]
        lines += [f"| `{r[1]}` | {r[2]} | {r[3]} | {r[4]} |" for r in group]
    return "\n".join(lines)


def changed(base: str) -> list[str]:
    merge_base = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or base
    out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AMR", merge_base, "HEAD"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=os.environ.get("FLOWS_BASE") or "origin/main")
    parser.add_argument("--files", nargs="*")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    covers, exempt = registered()
    problems = []
    for flow in tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["flow"]:
        if not (ROOT / flow["module"]).is_file():
            problems.append(f"流程 {flow['id']}：模块 {flow['module']} 不存在")
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        problems += [f"流程 {flow['id']}：covers 里的 {c} 对不上任何文件（文件改名或删了要同步登记表）"
                     for c in flow.get("covers", []) if not any(match(t, c) for t in tracked)]
    problems += [f"{path}：not_flows.toml 这条没写原因" for path, reason in exempt.items() if not reason.strip()]
    problems += [f"{path}：not_flows.toml 登记的文件不存在" for path in exempt if not (ROOT / path).is_file()]
    if args.status:
        print(status(covers, exempt, args.days))
        return 0
    if args.list:
        tracked = subprocess.run(["git", "ls-files", "apps", "frontend"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        entries = [p for p in tracked if is_entry(p)]
        bare = [p for p in entries if p not in exempt and not any(match(p, c) for c in covers)]
        print("\n".join(bare))
        print(f"入口文件 {len(entries)} 个，有流程 {len(entries) - len(bare) - len([p for p in entries if p in exempt])} 个，"
              f"登记不需要流程 {len([p for p in entries if p in exempt])} 个，没有流程 {len(bare)} 个", file=sys.stderr)
        return 0
    files = args.files if args.files is not None else changed(args.base)
    entries = [p for p in files if is_entry(p)]
    bare = [p for p in entries if p not in exempt and not any(match(p, c) for c in covers)]
    problems += [f"{p}：改到的产品入口文件没有流程用例 —— 在 tests/flows/registry.toml 给覆盖它的流程加 covers（没有就新写一条流程），"
                 "或确实无流程可跑时登记进 tests/flows/not_flows.toml 并写原因" for p in bare]
    if problems:
        print(f"流程用例覆盖：不通过（改到入口文件 {len(entries)} 个）")
        print("\n".join("  - " + p for p in problems))
        return 1
    flows = sorted({flow["id"] for flow in tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["flow"]
                    for p in entries if any(match(p, c) for c in flow.get("covers", []))})
    print(f"流程用例覆盖：通过（改到入口文件 {len(entries)} 个，涉及流程 {', '.join(flows) or '无'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
