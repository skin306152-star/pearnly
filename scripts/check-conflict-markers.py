#!/usr/bin/env python3
"""拦没解完的合并冲突：被跟踪的文本文件里成组出现冲突标记就失败。

    python3 scripts/check-conflict-markers.py            仓库里全部被跟踪的文件（工作树里的内容）——CI
    python3 scripts/check-conflict-markers.py --staged   本次暂存的文件（暂存快照里的内容）——提交钩子

判定（避开文档里合法的分隔线）：同一个文件里按顺序出现三种标记才算——行首七个小于号加空格加分支 / 提交名、
之后一行恰好七个等号、再之后行首七个大于号加空格加分支 / 提交名。单独一行等号、没带名字的尖括号行都不算。
二进制文件（前 8K 里有 NUL）跳过。起因：11-14 一次合并漏解一处冲突标记就提交了，钩子不拦。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OURS = re.compile(rb"^<{7} \S")
MIDDLE = re.compile(rb"^={7}\r?$")
THEIRS = re.compile(rb"^>{7} \S")


def conflicts(data: bytes) -> list[int]:
    """文件内容 → 每组冲突标记开头那一行的行号（从 1 起）。二进制回空。"""
    if b"\0" in data[:8192]:
        return []
    found, start, middle = [], None, False
    for number, line in enumerate(data.split(b"\n"), start=1):
        if OURS.match(line):
            start, middle = number, False
        elif start is not None and MIDDLE.match(line):
            middle = True
        elif start is not None and middle and THEIRS.match(line):
            found.append(start)
            start, middle = None, False
    return found


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, check=True).stdout


def tracked(root: Path) -> list[tuple[str, bytes]]:
    out = []
    for name in git(root, "ls-files", "-z").decode("utf-8", "surrogateescape").split("\0"):
        path = root / name
        if name and path.is_file() and not path.is_symlink():
            out.append((name, path.read_bytes()))
    return out


def staged(root: Path) -> list[tuple[str, bytes]]:
    names = git(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8", "surrogateescape").split("\0")
    return [(name, git(root, "show", f":{name}")) for name in names if name]


def check(files: list[tuple[str, bytes]]) -> list[str]:
    return [f"{name}:{line}" for name, data in files for line in conflicts(data)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="拦没解完的合并冲突标记")
    parser.add_argument("--staged", action="store_true", help="只看本次暂存的文件（暂存快照）")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root)
    hits = check(staged(root) if args.staged else tracked(root))
    if hits:
        print("没解完的合并冲突（成组的冲突标记）：", file=sys.stderr)
        for hit in hits:
            print("  - " + hit, file=sys.stderr)
        print("解完冲突、删掉三种标记再提交。", file=sys.stderr)
        return 1
    print("冲突标记检查：通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
