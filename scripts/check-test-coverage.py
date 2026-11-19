#!/usr/bin/env python3
"""测试必须有人跑（主会话 11-08）：`tests/` 下每个 `test_*.py` 要么被 CI 真正执行，要么登记在豁免表并写明原因。

起因：11-08 查出期初报表的测试（test_opening_report）在 main 上 20 个错误却没人知道——它不在任何 CI 步骤里，接口改了没跟。
一清点，431 个测试里有 154 个从来没被 CI 跑过。

「被 CI 执行」的口径（与 `docs/work/2026-11-08-orphan-tests.md` 的清点同一套）：
- `.github/workflows/*.yml` 的 run 行、`scripts/ci-candidate-checks.sh` 里点名的（文件名或模块名）；
- 上面这些 run 行执行的脚本（`scripts/...`）里按路径 `tests/<文件>` 列出的（如 `check-dms-offline.py` 的清单）。
只被别的测试 `import`（借样例数据）不算：它自己的用例没有跑。

两张表（`scripts/tests/`，制表符分隔，`#` 开头为注释）：
- `not-in-ci.tsv`：长期不进 CI 的，每行「文件名<TAB>原因」，原因不能空（例：要传入旧仓固定提交的源码才能跑的迁移对照工具）。
- `orphan-baseline.txt`：清点中、还没处理完的存量，**只许减少**：相对 merge-base 新加的条目判失败；已经被 CI 跑到或文件已删的条目也判失败（要同一提交删掉）。

    python3 scripts/check-test-coverage.py          # 检查（CI 跑）
    python3 scripts/check-test-coverage.py --list   # 列出每个测试由谁执行 / 为何豁免

退出码：0 = 通过；1 = 有没人跑又没登记的测试，或登记过期。只读，不连网络。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXEMPT = ROOT / "scripts" / "tests" / "not-in-ci.tsv"
BASELINE = ROOT / "scripts" / "tests" / "orphan-baseline.txt"
CANDIDATE = ROOT / "scripts" / "ci-candidate-checks.sh"


def run_blocks(path: Path) -> list[str]:
    """工作流里每个 `run:`（单行或 `|` 块）的命令文本。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks, i = [], 0
    while i < len(lines):
        match = re.match(r"\s+run:\s*(\|)?\s*(.*)$", lines[i])
        if not match:
            i += 1
            continue
        if not match.group(1):
            blocks.append(match.group(2))
            i += 1
            continue
        indent, body = len(lines[i]) - len(lines[i].lstrip()), []
        i += 1
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent):
            body.append(lines[i])
            i += 1
        blocks.append("\n".join(body))
    return blocks


TOKEN = re.compile(r"(?<![\w.])(test_\w+)\b")


def names_in(text: str, tests: list[str]) -> set[str]:
    """文本里点名的测试：文件名或模块名，前面不能紧跟字母数字、下划线或 `.`（`pkg.test_x` 是别的包里的同名模块）。
    一遍扫出全部 `test_…` 词再对清单（逐个测试各编一个正则时，四百多个超出 re 的缓存，CI 上要 20 多秒）。"""
    found = {token + ".py" for token in TOKEN.findall(text)}
    return found & set(tests)


def executed(root: Path, tests: list[str]) -> dict[str, str]:
    """{测试文件名: 由谁执行}。"""
    sources = [(wf.name, block) for wf in sorted((root / ".github" / "workflows").glob("*.yml")) for block in run_blocks(wf)]
    candidate = root / "scripts" / "ci-candidate-checks.sh"
    if candidate.is_file():
        sources.append((candidate.name, candidate.read_text(encoding="utf-8")))
    out: dict[str, str] = {}
    scripts: set[str] = set()
    for where, text in sources:
        for name in names_in(text, tests):
            out.setdefault(name, where)
        scripts |= set(re.findall(r"(scripts/[\w./-]+\.(?:py|sh|mjs|cjs))", text))
    for script in sorted(scripts):
        path = root / script
        if not path.is_file() or path.name == candidate.name:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in tests:
            if f"tests/{name}" in text:
                out.setdefault(name, script)
    return out


def read_exempt(text: str, label: str = "scripts/tests/not-in-ci.tsv") -> tuple[dict[str, str], list[str]]:
    rows, problems = {}, []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        name, _, reason = line.partition("\t")
        if not reason.strip():
            problems.append(f"{label}:{number} {name}：没写原因")
        rows[name.strip()] = reason.strip()
    return rows, problems


def read_baseline(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def base_text(root: Path, relative: str, base: str) -> str | None:
    """merge-base 上这张表的内容；取不到（浅克隆、没有远端、不是 git 仓库）回 None，只做本地核对。"""
    try:
        merge = subprocess.run(["git", "-c", "core.quotepath=off", "merge-base", base, "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        shown = subprocess.run(["git", "-c", "core.quotepath=off", "show", f"{merge.stdout.strip()}:{relative}"], cwd=root,
                               capture_output=True, text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    # merge-base 上还没有这张表（本表首次建立的那一批）：不比较
    return shown.stdout if shown.returncode == 0 else None


def check(root: Path = ROOT, base: str = "origin/main") -> tuple[list[str], dict]:
    tests = sorted(path.name for path in (root / "tests").glob("test_*.py"))
    ran = executed(root, tests)
    exempt_path, baseline_path = root / EXEMPT.relative_to(ROOT), root / BASELINE.relative_to(ROOT)
    exempt_label, baseline_label = str(EXEMPT.relative_to(ROOT)), str(BASELINE.relative_to(ROOT))
    exempt, problems = read_exempt(exempt_path.read_text(encoding="utf-8") if exempt_path.is_file() else "", exempt_label)
    baseline = read_baseline(baseline_path.read_text(encoding="utf-8") if baseline_path.is_file() else "")
    for name in tests:
        if name not in ran and name not in exempt and name not in baseline:
            problems.append(f"{name}：没有 CI 步骤执行它——接进 .github/workflows/development.yml（或候选检查），"
                            f"确实不进 CI 的写进 {exempt_label} 并写原因")
    for name in sorted(exempt):
        if name not in tests:
            problems.append(f"{exempt_label}：{name} 已不存在，删掉这一行")
        elif name in ran:
            problems.append(f"{exempt_label}：{name} 已由 {ran[name]} 执行，删掉这一行")
    for name in sorted(baseline):
        if name not in tests:
            problems.append(f"{baseline_label}：{name} 已不存在，删掉这一行")
        elif name in ran:
            problems.append(f"{baseline_label}：{name} 已由 {ran[name]} 执行，删掉这一行（基线只许减少）")
        elif name in exempt:
            problems.append(f"{baseline_label}：{name} 已在豁免表，删掉这一行")
    before = base_text(root, baseline_label, base)
    if before is not None:
        added = sorted(baseline - read_baseline(before))
        problems += [f"{baseline_label}：{name} 是新加的——基线只许减少，接进 CI 或写进豁免表" for name in added]
    return problems, {"tests": tests, "ran": ran, "exempt": exempt, "baseline": baseline}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    problems, state = check(base=args.base)
    if args.list:
        for name in state["tests"]:
            how = state["ran"].get(name) or (f"豁免：{state['exempt'][name]}" if name in state["exempt"] else
                                              "基线（待清点）" if name in state["baseline"] else "没人跑")
            print(f"{name}\t{how}")
    if problems:
        print(f"测试覆盖闸门：{len(problems)} 处问题", file=sys.stderr)
        for line in problems:
            print("  - " + line, file=sys.stderr)
        return 1
    print(f"测试覆盖闸门：通过（{len(state['tests'])} 个测试：CI 执行 {len(state['ran'])}，豁免 {len(state['exempt'])}，"
          f"待清点基线 {len(state['baseline'])}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
