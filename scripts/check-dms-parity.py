#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DMS 与老产品的**语义对账**：把"新旧代码的差异"冻住，动了就报警。

## 为什么要有它

2026-09-21 连续发现两处"搬家时漏掉一个开关"的回归（查重读取漏了
`authoritative_read=True`、编辑页首屏漏拉"府"列表）—— 两处都不会让任何测试变红，
只能靠人眼或用户撞上。人工对账做一次可以，做完就过期了。

这个脚本的做法：以**当时线上那版**为基准，逐个模块做 AST 级对比（去掉注释、
文档字符串、import 顺序这些噪音），把每个模块的"差异指纹"记进
`scripts/dms-parity-expectations.json`。之后任何人改动这块代码：

· 差异**没有变化** → 通过（说明和老产品的偏离度没动）；
· 差异**变了** → 失败并指出是哪个模块（说明要么修好了差异、要么又漏了/多了一处），
  必须由人确认后跑 `--update` 重新登记。

它不是"证明新代码正确"，而是"把已复核过的偏离冻结，防止悄悄漂移"。

## 基准从哪来

必须用**当时真正在服务线上流量的那版**。本地老仓库的 `master` 落后线上 12 天、
少 15 个文件（2026-09-21 实测），拿它当基准会误报一大堆差异。

    --baseline-repo /Users/skin/Developer/Pearnly/pearnly-app
    --baseline-sha  501589f0        # 线上基准（origin/master，2026-09-18）

脚本用 `git show <sha>:<path>` 读老文件，不切分支、不改老仓库工作树。

## 用法

    python3 scripts/check-dms-parity.py              # 对账（CI/本地都可用，失败即退出码 1）
    python3 scripts/check-dms-parity.py --report     # 只打印差异行数，不判失败
    python3 scripts/check-dms-parity.py --update     # 复核后重新登记指纹（会改 JSON）

老仓库不在本机时（例如 CI）脚本会**跳过并退出 0**，只在真正拿得到基准时判失败 ——
这条对账是维护者工具，不是运行时依赖。
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTATIONS = os.path.join(REPO_ROOT, "scripts", "dms-parity-expectations.json")
DEFAULT_BASELINE_REPO = os.path.expanduser("~/Developer/Pearnly/pearnly-app")
DEFAULT_BASELINE_SHA = "501589f0"

# 老 → 新 的重命名/换落点。没登记且同名文件也找不到的，按"老文件缺失"报出来，
# 而不是静默跳过（静默跳过正是漏搬的温床）。
RENAMES: Dict[str, str] = {
    "_out.py": "apps/pearnly_dms/pearnly_dms/line/out.py",
    "store.py": "apps/pearnly_core/pearnly_core/line_session.py",
    "session_store.py": "apps/pearnly_core/pearnly_core/line_session.py",
    "login_tickets.py": "apps/pearnly_core/pearnly_core/line_session.py",
    "binding_guard.py": "apps/pearnly_dms/pearnly_dms/line/identity.py",
    "binding_state.py": "apps/pearnly_dms/pearnly_dms/line/identity.py",
    "customer_edit_entry.py": "apps/pearnly_dms/pearnly_dms/intake/flow.py",
    "mrerp_portal.py": "apps/pearnly_dms/pearnly_dms/account/portal.py",
    "menu_sync.py": "apps/pearnly_dms/pearnly_dms/menu/rich_menu.py",
    "account_channel.py": "apps/pearnly_control/pearnly_control/line_admin.py",
    "schema.py": "apps/pearnly_core/pearnly_core/pearnly_core/doctype/pearnly_line_session/pearnly_line_session.json",
}


def _normalize(source: str) -> List[str]:
    """AST 归一：去掉注释/文档字符串/import，只留语义语句。

    比"逐行 diff"更接近"逻辑是否一样"：`async` → 同步、格式化、换行都不算差异。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    return [line.strip() for line in ast.unparse(tree).splitlines() if line.strip()]


def _git_show(repo: str, sha: str, path: str) -> Optional[str]:
    result = subprocess.run(
        ["git", "-C", repo, "show", f"{sha}:{path}"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _git_list(repo: str, sha: str, prefix: str) -> List[str]:
    result = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", sha, "--", prefix],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _find_new_file(basename: str) -> Optional[str]:
    if basename in RENAMES:
        candidate = os.path.join(REPO_ROOT, RENAMES[basename])
        return candidate if os.path.isfile(candidate) else None
    for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, "apps")):
        if basename in files and "pearnly_dms" in root:
            return os.path.join(root, basename)
    return None


def _pair_report(baseline_repo: str, sha: str) -> Tuple[Dict[str, dict], List[str]]:
    entries: Dict[str, dict] = {}
    problems: List[str] = []
    for old_path in sorted(_git_list(baseline_repo, sha, "services/line_dms")):
        basename = os.path.basename(old_path)
        if basename == "__init__.py":
            continue
        source = _git_show(baseline_repo, sha, old_path)
        if source is None:
            problems.append(f"{basename}: 基准里读不到")
            continue
        new_path = _find_new_file(basename)
        if not new_path:
            entries[basename] = {"new": None, "note": "新仓没有对应落点（需人工复核）"}
            continue
        try:
            old_lines = _normalize(source)
            with open(new_path, encoding="utf-8") as handle:
                new_lines = _normalize(handle.read())
        except SyntaxError as exc:
            problems.append(f"{basename}: 解析失败 {exc}")
            continue
        diff = [
            line for line in difflib.unified_diff(old_lines, new_lines, lineterm="", n=0)
            if line[:1] in ("+", "-") and not line.startswith(("+++", "---"))
        ]
        entries[basename] = {
            "new": os.path.relpath(new_path, REPO_ROOT),
            "diff_lines": len(diff),
            "digest": hashlib.sha256("\n".join(diff).encode("utf-8")).hexdigest(),
        }
    return entries, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="DMS 与老产品的语义对账")
    parser.add_argument("--baseline-repo", default=DEFAULT_BASELINE_REPO)
    parser.add_argument("--baseline-sha", default=DEFAULT_BASELINE_SHA)
    parser.add_argument("--update", action="store_true", help="复核后重新登记指纹")
    parser.add_argument("--report", action="store_true", help="只打印，不判失败")
    args = parser.parse_args()

    if not os.path.isdir(os.path.join(args.baseline_repo, ".git")):
        print(f"[parity] 基准仓库不存在，跳过：{args.baseline_repo}")
        return 0
    if subprocess.run(["git", "-C", args.baseline_repo, "cat-file", "-t", args.baseline_sha],
                      capture_output=True, text=True).returncode != 0:
        print(f"[parity] 基准 SHA 不存在，跳过：{args.baseline_sha}")
        return 0

    entries, problems = _pair_report(args.baseline_repo, args.baseline_sha)

    if args.update:
        payload = {"baseline_repo": os.path.basename(args.baseline_repo),
                   "baseline_sha": args.baseline_sha, "modules": entries}
        with open(EXPECTATIONS, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        print(f"[parity] 已登记 {len(entries)} 个模块的差异指纹 → {os.path.relpath(EXPECTATIONS, REPO_ROOT)}")
        for name in problems:
            print(f"[parity] 需要注意：{name}")
        return 0

    if not os.path.exists(EXPECTATIONS):
        print(f"[parity] 还没有指纹文件，先跑 --update：{os.path.relpath(EXPECTATIONS, REPO_ROOT)}")
        return 1 if not args.report else 0
    with open(EXPECTATIONS, encoding="utf-8") as handle:
        expected = json.load(handle)
    expected_modules = expected.get("modules") or {}

    drifted: List[str] = []
    for name, entry in sorted(entries.items()):
        want = expected_modules.get(name)
        if want is None:
            drifted.append(f"{name}: 未登记（新出现的模块？）")
            continue
        if want.get("digest") != entry.get("digest"):
            drifted.append(
                f"{name}: 与老产品的差异变了"
                f"（登记 {want.get('diff_lines')} 行 → 现在 {entry.get('diff_lines')} 行）"
            )
    for name in sorted(set(expected_modules) - set(entries)):
        drifted.append(f"{name}: 基准里有、这次没对上")

    print(f"[parity] 基准 {expected.get('baseline_sha')}（{expected.get('baseline_repo')}）"
          f"｜对齐 {len(entries)} 个模块")
    if args.report:
        for name, entry in sorted(entries.items(), key=lambda kv: -(kv[1].get("diff_lines") or 0)):
            print(f"    {entry.get('diff_lines') or 0:>4} 行  {name} → {entry.get('new') or '（无落点）'}")
        for name in problems:
            print(f"    !! {name}")
        for line in drifted:
            print(f"    ?? {line}")
        return 0
    if drifted or problems:
        print("[parity] 需要人工复核：")
        for line in problems + drifted:
            print(f"  · {line}")
        print("[parity] 确认无误后跑：python3 scripts/check-dms-parity.py --update")
        return 1
    print("[parity] 与老产品的差异没有漂移")
    return 0


if __name__ == "__main__":
    sys.exit(main())
