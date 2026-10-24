#!/usr/bin/env python3
"""共用件连带回归闸门：改到登记的共用件时列出要实测的全部入口；有调用方没登记就失败。

见 docs/development/verification.md「共用件连带回归闸门」。登记表 scripts/impact/shared-parts.toml。

    python3 scripts/check-impact.py                    # 相对 merge-base(origin/main, HEAD) 的改动（含未提交），出清单并核登记表
    python3 scripts/check-impact.py --markdown         # 清单按 Markdown 表输出，贴进 docs/work/ 任务记录
    python3 scripts/check-impact.py --part desk-scan   # 不看 diff，直接列某个共用件的入口
    python3 scripts/check-impact.py --files a.js b.py  # 按给定文件算（演练、审查用）

退出码：0 = 登记表完整；1 = 有调用方没登记 / 登记表错误。命中共用件本身不算失败——它要求的是按清单实测，结果记在任务记录里。
只读：不改文件、不连网络。
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "scripts" / "impact" / "shared-parts.toml"
CHECKS = {"auto", "manual", "offline"}
PRODUCTS = {"ERP", "Cowork", "DMS", "控制面", "CC", "共用"}
# 反查调用方时扫这些文件；测试、文档、构建产物不算调用方
SEARCH = ("apps/**", "frontend/**", "companion/src/**", "infrastructure/**", "scripts/**")
SKIP = ("**/tests/**", "**/test_*.py", "tests/**", "docs/**", "**/node_modules/**", "**/public/assistant/**",
        "**/public/pearnly-app/**", "**/*.min.js", "**/vendor/**", "scripts/impact/**", "scripts/check-impact.py",
        "**/*.wasm", "**/*.png", "**/*.jpg", "**/*.svg", "**/*.mp3", "**/*.ico", "**/*.woff*", "**/*.tsv", "**/*.po",
        "**/*.mo", "**/*.json", "**/*.lock", "**/package-lock.json")


@dataclass
class Part:
    id: str
    name: str
    paths: list
    markers: list
    entries: list
    search: list = field(default_factory=list)
    skip: list = field(default_factory=list)
    not_entries: dict = field(default_factory=dict)  # 调用方文件 → 为什么不算入口（如只转发）


@dataclass
class Entry:
    id: str
    product: str
    where: str
    steps: str
    expect: str
    check: str
    files: list
    smoke: str = ""   # 自动冒烟的探针名（scripts/smoke/probes/<名>.js）
    url: str = ""     # 冒烟打开的地址（相对站点根）
    args: dict = field(default_factory=dict)  # 传给探针的参数
    site: str = ""    # 冒烟用哪个测试租户：erp / cowork / dms（默认按 product）


def _match(path: str, patterns) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or (pattern.endswith("/**") and path.startswith(pattern[:-2]))
               for pattern in patterns)


def _git(*args, cwd=ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def load(path=REGISTRY):
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    errors = []
    entries = {}
    for raw in data.get("entry", []):
        try:
            entry = Entry(**raw)
        except TypeError as error:
            errors.append(f"入口格式不对：{raw.get('id')!r}：{error}")
            continue
        if entry.id in entries:
            errors.append(f"入口重复：{entry.id}")
        if entry.check not in CHECKS:
            errors.append(f"入口 {entry.id} 的 check 只能是 {sorted(CHECKS)}")
        if entry.check == "auto" and not entry.smoke:
            errors.append(f"入口 {entry.id} 标了 auto 但没写 smoke（自动冒烟的探针）")
        if entry.product not in PRODUCTS:
            errors.append(f"入口 {entry.id} 的 product 只能是 {sorted(PRODUCTS)}")
        entries[entry.id] = entry
    parts = {}
    for raw in data.get("part", []):
        try:
            part = Part(**raw)
        except TypeError as error:
            errors.append(f"共用件格式不对：{raw.get('id')!r}：{error}")
            continue
        if part.id in parts:
            errors.append(f"共用件重复：{part.id}")
        for entry_id in part.entries:
            if entry_id not in entries:
                errors.append(f"共用件 {part.id} 引用了不存在的入口 {entry_id}")
        parts[part.id] = part
    used = {entry_id for part in parts.values() for entry_id in part.entries}
    for entry_id in sorted(set(entries) - used):
        errors.append(f"入口 {entry_id} 没有任何共用件引用")
    return parts, entries, errors


def tracked(root=ROOT) -> list:
    return [line for line in _git("ls-files", cwd=root).splitlines() if line]


def callers(part: Part, files: list, root=ROOT) -> set:
    """全仓里引用这个共用件的文件（按特征串子串匹配；共用件自己的文件不算）。"""
    found = set()
    search, skip = part.search or SEARCH, (*SKIP, *part.skip)
    for path in files:
        if not _match(path, search) or _match(path, skip) or _match(path, part.paths):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(marker in text for marker in part.markers):
            found.add(path)
    return found


def validate(parts, entries, files, root=ROOT) -> list:
    """登记表与代码对不上的地方：路径不存在、入口文件不存在、调用方没登记。"""
    errors = []
    present = set(files)
    for part in parts.values():
        if not any(_match(path, part.paths) for path in files):
            errors.append(f"共用件 {part.id} 的 paths 一个文件都没匹配到：{part.paths}")
        registered = {path for entry_id in part.entries if entry_id in entries for path in entries[entry_id].files}
        for path in sorted(callers(part, files, root) - registered - set(part.not_entries)):
            errors.append(f"共用件 {part.id}（{part.name}）的调用方没登记：{path} —— 在 scripts/impact/shared-parts.toml 加入口，"
                          "或写进这个共用件的 not_entries 并说明原因")
        for path in part.not_entries:
            if path not in present:
                errors.append(f"共用件 {part.id} 的 not_entries 里的文件不存在：{path}")
    for entry in entries.values():
        for path in entry.files:
            if path not in present:
                errors.append(f"入口 {entry.id} 的文件不存在：{path}")
    return errors


def changed(base: str, root=ROOT) -> list:
    if base == "auto":
        base = os.environ.get("IMPACT_BASE") or "origin/main"
    try:
        merge_base = _git("merge-base", base, "HEAD", cwd=root).strip()
    except subprocess.CalledProcessError:
        print(f"（取不到 {base} 与 HEAD 的 merge-base，只核登记表）", file=sys.stderr)
        return []
    names = set(_git("diff", "--name-only", merge_base, cwd=root).splitlines())
    names |= set(_git("ls-files", "--others", "--exclude-standard", cwd=root).splitlines())
    return sorted(name for name in names if name)


def impact(parts, entries, changes, files, root=ROOT):
    """改动 → 命中的共用件（改到它本身）与只改到某个调用方的入口。"""
    hit = [part for part in parts.values() if any(_match(path, part.paths) for path in changes)]
    touched = {}
    for part in parts.values():
        for entry_id in part.entries:
            entry = entries.get(entry_id)
            if entry and any(path in changes for path in entry.files):
                touched.setdefault(entry_id, set()).add(part.id)
    return hit, touched


def render(hit, touched, parts, entries, markdown: bool) -> str:
    lines = []
    if not hit and not touched:
        return "没有改到登记的共用件或其入口。"
    wanted = []
    for part in hit:
        wanted.extend(part.entries)
    wanted.extend(entry_id for entry_id in touched if entry_id not in wanted)
    wanted = list(dict.fromkeys(wanted))
    if markdown:
        lines.append("命中的共用件：" + "、".join(f"`{part.id}`（{part.name}）" for part in hit) if hit else "命中的共用件：无")
        if touched:
            lines.append("只改到调用方的入口：" + "、".join(f"`{entry_id}`" for entry_id in touched))
        lines.append("")
        lines.append(f"要实测的入口 {len(wanted)} 个（每行填「通过 / 不通过 / 没测到（原因）」，不能用推断代替）：")
        lines.append("")
        lines.append("| 入口 | 产品 | 位置 | 怎么点 | 预期 | 方式 | 结果 |")
        lines.append("|---|---|---|---|---|---|---|")
        for entry_id in wanted:
            entry = entries[entry_id]
            lines.append(f"| `{entry.id}` | {entry.product} | {entry.where} | {entry.steps} | {entry.expect} | "
                         f"{entry.check}{('：' + entry.smoke) if entry.smoke else ''} |  |")
    else:
        for part in hit:
            lines.append(f"命中共用件 {part.id}（{part.name}）：{len(part.entries)} 个入口")
        for entry_id in wanted:
            entry = entries[entry_id]
            lines.append(f"  [{entry.check}] {entry.id}  {entry.product} · {entry.where} · {entry.steps} → {entry.expect}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=str(ROOT), help="要看的工作树（默认本仓）")
    parser.add_argument("--registry", help="登记表路径（默认 scripts/impact/shared-parts.toml）")
    parser.add_argument("--base", default="auto", help="基线（默认 IMPACT_BASE 或 origin/main 的 merge-base）")
    parser.add_argument("--files", nargs="*", help="不看 diff，按这些文件算")
    parser.add_argument("--part", action="append", help="不看 diff，直接列这个共用件的入口")
    parser.add_argument("--markdown", action="store_true", help="清单按 Markdown 表输出")
    parser.add_argument("--out", help="清单写到这个文件（GITHUB_STEP_SUMMARY 追加）")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    parts, entries, errors = load(args.registry or root / "scripts" / "impact" / "shared-parts.toml")
    files = tracked(root)
    errors += validate(parts, entries, files, root)
    if args.part:
        unknown = [part_id for part_id in args.part if part_id not in parts]
        if unknown:
            errors.append(f"没有这个共用件：{unknown}")
        hit, touched = [parts[part_id] for part_id in args.part if part_id in parts], {}
    else:
        changes = args.files if args.files is not None else changed(args.base, root)
        hit, touched = impact(parts, entries, changes, files, root)
    report = render(hit, touched, parts, entries, args.markdown)
    print(report)
    if args.out:
        mode = "a" if args.out == os.environ.get("GITHUB_STEP_SUMMARY") else "w"
        with open(args.out, mode, encoding="utf-8") as handle:
            handle.write("## 共用件连带回归清单\n\n" + report + "\n")
    if errors:
        print("\n登记表与代码对不上（共 %d 处）：" % len(errors), file=sys.stderr)
        for error in errors:
            print("  - " + error, file=sys.stderr)
        return 1
    print(f"\n登记表完整：{len(parts)} 个共用件、{len(entries)} 个入口。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
