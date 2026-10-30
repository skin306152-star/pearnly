#!/usr/bin/env python3
"""部署闸「沿用全套」（主会话 10-30，用户问「每次小改动都要跑全套？」）。

本提交没有全套流程状态（GitHub `pearnly/flows` 说明以 ``all flows`` 开头的 success）时，沿 main 首父链往回找最近一个有全套的提交；
那个提交到本提交之间改到的文件全部不在产品路径里，就沿用那次全套（构建里的候选车道仍会把这些改动跑一遍）。命中任一产品路径、
中间夹着改产品的提交、或往回找不到全套，都照旧拦下、要跑全套。没有绕过开关。

  python3 scripts/production/flows_inherit.py <提交>   退出码 0 = 可沿用（打印沿用哪个提交、之后改了哪些文件），1 = 不能沿用（打印原因）
"""
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 命中任一就要全套：产品代码、前端、CC 桌面版、运行定义、流程用例本身、依赖与锁文件
PRODUCT_PREFIXES = ("apps/", "frontend/", "companion/", "infrastructure/", "tests/flows/", "scripts/flows/")
DEPENDENCY_NAMES = ("requirements*", "*.lock", "package*.json", "pyproject.toml", "Dockerfile*")
LOOKBACK = 50   # 往回最多看这么多个首父提交


def is_product(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return path.startswith(PRODUCT_PREFIXES) or any(fnmatch.fnmatch(name, pattern) for pattern in DEPENDENCY_NAMES)


def is_full_suite(status) -> bool:
    return bool(status) and status[0] == "success" and str(status[1]).startswith("all flows")


def decide(sha, ancestors, status_of, changed_since):
    """``ancestors``：本提交之前的首父提交（新到旧）；``status_of(提交)`` → (state, description) 或 None；
    ``changed_since(基准)`` → 基准..本提交改到的文件。回 (能否沿用, 说明)。"""
    for base in ancestors:
        if is_full_suite(status_of(base)):
            changed = changed_since(base)
            product = [path for path in changed if is_product(path)]
            if product:
                return False, f"{base[:8]} 之后改到了产品路径（要跑全套）：" + "、".join(product[:20])
            return True, f"沿用 {base[:8]} 的全套，之后只改了：" + ("、".join(changed) or "（无文件改动）")
    return False, f"往回 {len(ancestors)} 个提交里找不到全套流程结果"


def _git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout


def _status(sha):
    out = subprocess.run(["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}/status", "--jq",
                          '[.statuses[] | select(.context == "pearnly/flows")][0] | select(. != null) | [.state, .description]'],
                         cwd=REPO, capture_output=True, text=True)
    return tuple(json.loads(out.stdout)) if out.returncode == 0 and out.stdout.strip() else None


def main(argv) -> int:
    sha = _git("rev-parse", argv[1]).strip()
    ancestors = _git("rev-list", "--first-parent", f"--max-count={LOOKBACK}", f"{sha}~1").split()
    ok, note = decide(sha, ancestors, _status, lambda base: _git("diff", "--name-only", f"{base}..{sha}").split())
    print(note)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
