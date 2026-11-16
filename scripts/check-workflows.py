#!/usr/bin/env python3
"""GitHub 工作流文件闸：`.github/workflows/*.yml` 必须是 GitHub 认得的 YAML。

为什么要有：11-15 在 development.yml 里加的一个步骤名写了「冒号 + 空格」（``- name: … engine: decided …``），整份文件 GitHub 不认，
分支上的 Development checks 一步都没跑（页面只说 workflow file issue，没有日志）；本地全闸门只按行摘 ``run:``，查不出来。

两层，都不用第三方库也能跑（本机与 runner 的 python 不一定装了 PyYAML）：

1. 逐行查 GitHub 会拒收的常见写法（不经解析器也能报出行号）：
   - 不加引号的值里出现「冒号 + 空格」（``name: a: b``）——YAML 把它当成又一层键值，整份文件作废；
   - 不加引号的值以保留字符开头（`` ` @ * & ! % ``）；
   - 用制表符缩进；
   ``|`` / ``>`` 块里的行（shell 命令）不查。
2. 装了 PyYAML 就再整份解析一遍，并核顶层有 ``on`` 与 ``jobs``、每个任务有 ``steps`` 或 ``uses``。

    python3 scripts/check-workflows.py            # 工作树里的全部工作流
    python3 scripts/check-workflows.py --staged   # 暂存快照里的（共享提交钩子用）
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ".github/workflows/"
_KEY = re.compile(r"^(?P<indent>[ ]*)(?:- )?(?P<key>[A-Za-z0-9_.\-]+):(?:[ ]+(?P<value>.*))?$")
_BLOCK = re.compile(r"^[|>][+\-0-9]*\s*(#.*)?$")
RESERVED = "`@*&!%"


def _plain(value: str) -> bool:
    """这个值是不是不加引号的普通写法（引号、块、流式、锚点引用以外的）。"""
    return bool(value) and value[0] not in "'\"[{|>"


def problems(name: str, text: str, parse: bool = True) -> list[str]:
    """逐行查（第一层）；``parse`` 为真且装了 PyYAML 时再整份解析（第二层）。"""
    out: list[str] = []
    block = None  # 正在 | / > 块里：块所属键的缩进
    for number, line in enumerate(text.splitlines(), 1):
        if "\t" in line[: len(line) - len(line.lstrip())]:
            out.append(f"{name}:{number}: indented with a tab")
            continue
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        if block is not None:
            if not stripped or indent > block:
                continue
            block = None
        if not stripped or stripped.startswith("#"):
            continue
        found = _KEY.match(line)
        if not found:
            continue
        value = (found.group("value") or "").strip()
        if _BLOCK.match(value):
            block = len(found.group("indent")) + (2 if line.lstrip().startswith("- ") else 0)
            continue
        if not _plain(value):
            continue
        body = value.split(" #", 1)[0]
        if ": " in body or body.endswith(":"):
            out.append(f"{name}:{number}: unquoted value of {found.group('key')} contains ': ' (quote it or rephrase)")
        elif body[0] in RESERVED:
            out.append(f"{name}:{number}: unquoted value of {found.group('key')} starts with {body[0]!r} (quote it)")
    if parse:
        out.extend(_parsed(name, text))
    return out


def _parsed(name: str, text: str) -> list[str]:
    try:
        import yaml  # noqa: PLC0415 — 可选：装了就整份解析
    except ImportError:
        return []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        return [f"{name}: not valid YAML: {str(error).splitlines()[0]}"]
    if not isinstance(data, dict):
        return [f"{name}: the top level is not a mapping"]
    # PyYAML 把不加引号的 on 读成 True
    out = [] if ("on" in data or True in data) else [f"{name}: no 'on' trigger"]
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return out + [f"{name}: no jobs"]
    for job, body in jobs.items():
        if not isinstance(body, dict) or not ({"steps", "uses"} & set(body)):
            out.append(f"{name}: job {job} has neither steps nor uses")
    return out


def _files(staged: bool) -> list[tuple[str, str]]:
    if staged:
        listed = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.split()
        names = [path for path in listed if path.startswith(FOLDER) and path.endswith((".yml", ".yaml"))]
        return [(path, subprocess.run(["git", "show", f":{path}"], cwd=ROOT, capture_output=True, text=True,
                                      check=True).stdout) for path in names]
    return [(str(path.relative_to(ROOT)), path.read_text(encoding="utf-8"))
            for path in sorted((ROOT / FOLDER).glob("*.y*ml"))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--staged", action="store_true", help="check the staged versions of the workflow files only")
    args = parser.parse_args(argv)
    found = [problem for name, text in _files(args.staged) for problem in problems(name, text)]
    for problem in found:
        print(problem, file=sys.stderr)
    if found:
        print(f"check-workflows: {len(found)} problem(s); GitHub would refuse the workflow file", file=sys.stderr)
        return 1
    print("check-workflows: workflow files ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
