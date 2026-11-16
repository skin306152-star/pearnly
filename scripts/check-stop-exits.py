#!/usr/bin/env python3
"""停下出口闸：每个让流程停下的原因码都归了类——问用户 / 等 / 说清要用户做哪一件事（总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 第 9 节；归类表 ``pearnly_core/assistant/stop_exits.py``）。

用户 11-14：「要点的要做的可以引导式啊，cc 为什么拒绝，决定交给用户」——不许只有原因码、没有下一步。原因码散在各自的登记处，
这里按源码把它们收齐（不导入产品模块）：

- 代录原因（``express_operator/contracts.py`` 的 ``REASONS``）；
- 直写回执里转人工的码（``cc_desktop/wire/ack_rules.py`` 的 ``NEEDS_PERSON_CODES``、``express_rules/push/common.py`` 的两组引导码）；
- ERP 单据停下的原因（``express/contracts.py`` 的 ``express.*`` 与 ``choice_needed``）；
- 收款的拒绝码与回执码（``collection/contracts.py``）；
- 写入计划跳过的原因（``assistant/write_plan.py`` 的 ``SKIP_*``）；
- MR.ERP 的用户数据错与要人选的码（``mrerp/push/user_data.py``、ERP ``mrerp/contracts.py``）。

规则：收到的每个码要么在归类表里，要么在基线 ``scripts/stop-exits-baseline.json``（存量还没归类的，**只许减少**）。
新加一个原因码而没归类 = 失败；归了类还留在基线里 = 失败（把它从基线删掉）；归类表里有、源码里已经没有的码 = 失败。

    python3 scripts/check-stop-exits.py            # 检查
    python3 scripts/check-stop-exits.py --list     # 逐码列出：归类 / 还没归类
    python3 scripts/check-stop-exits.py --update   # 按当前代码重写基线（只能在减少时用）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "stop-exits-baseline.json"
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.assistant import stop_exits  # noqa: E402

K = "apps/pearnly_core/pearnly_core"
E = "apps/pearnly_erp_portal/pearnly_erp_portal"


def _assignments(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value
                elif isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    for name, value in zip(target.elts, node.value.elts):
                        if isinstance(name, ast.Name):
                            yield name.id, value


def _strings(node: ast.AST) -> List[str]:
    """一个字面量（元组、集合、``frozenset({...})``、字典的键）里直接写着的字符串；``*别处`` 展开的不算（在它自己的登记处收）。"""
    if isinstance(node, ast.Call) and node.args:
        node = node.args[0]
    items: Iterable[ast.AST] = node.keys if isinstance(node, ast.Dict) else getattr(node, "elts", ())
    return [item.value for item in items if isinstance(item, ast.Constant) and isinstance(item.value, str)]


def keys_of(*names: str):
    return lambda tree: [text for name, value in _assignments(tree) if name in names for text in _strings(value)]


def constants(pattern: str):
    rule = re.compile(pattern)
    return lambda tree: [value.value for _, value in _assignments(tree)
                         if isinstance(value, ast.Constant) and isinstance(value.value, str) and rule.match(value.value)]


def named(pattern: str):
    rule = re.compile(pattern)
    return lambda tree: [value.value for name, value in _assignments(tree)
                         if rule.match(name) and isinstance(value, ast.Constant) and isinstance(value.value, str)]


SOURCES = (
    (f"{K}/express_operator/contracts.py", keys_of("REASONS")),
    (f"{K}/cc_desktop/wire/ack_rules.py", keys_of("NEEDS_PERSON_CODES")),
    (f"{K}/express_rules/push/common.py", keys_of("GUIDED_CHOICE_CODES", "GUIDED_REFUSAL_CODES")),
    (f"{E}/express/contracts.py", constants(r"^(express\.[a-z_]+|choice_needed)$")),
    (f"{E}/collection/contracts.py", constants(r"^collection\.[a-z_]+$")),
    (f"{E}/collection/contracts.py", keys_of("UNCOLLECTABLE")),
    (f"{K}/assistant/write_plan.py", named(r"^SKIP_[A-Z_]+$")),
    (f"{K}/mrerp/push/user_data.py", keys_of("USER_DATA_ERROR_CODES")),
    (f"{E}/mrerp/contracts.py", keys_of("CHOICE_CODES")),
)


def harvest(root: Path = ROOT) -> Dict[str, str]:
    """``{原因码: 它登记在哪个文件}``（同一个码在几处出现记第一处）。"""
    found: Dict[str, str] = {}
    for rel, extract in SOURCES:
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"check-stop-exits: {rel} is missing (the reason registry moved: update SOURCES)")
        for code in extract(ast.parse(path.read_text(encoding="utf-8"))):
            found.setdefault(code, rel)
    return found


def read_baseline(path: Path = BASELINE) -> Set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))["unclassified"]) if path.is_file() else set()


def check(found: Dict[str, str], exits, baseline: Set[str]) -> List[str]:
    errors = list(stop_exits.problems(exits))
    unclassified = set(found) - set(exits)
    errors += [f"{code} ({found[code]}): a reason that stops the flow has no next step: classify it in "
               "pearnly_core/assistant/stop_exits.py (ask / wait / act)" for code in sorted(unclassified - baseline)]
    errors += [f"{code}: classified now: remove it from scripts/stop-exits-baseline.json (run --update)"
               for code in sorted(baseline & set(exits))]
    errors += [f"{code}: in the baseline but no longer in the code: run --update" for code in sorted(baseline - set(found))]
    errors += [f"{code}: classified but not found in any reason registry" for code in sorted(set(exits) - set(found))]
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)
    found = harvest()
    baseline = read_baseline()
    unclassified = sorted(set(found) - set(stop_exits.EXITS))
    if args.list:
        for code in sorted(found):
            item = stop_exits.EXITS.get(code)
            print(f"{code}\t{item['next'] if item else '-'}\t{'live' if item and item.get('live') else (item or {}).get('phase', '')}\t{found[code]}")
        return 0
    if args.update:
        if BASELINE.is_file() and set(unclassified) - baseline:
            print("check-stop-exits: the baseline only goes down; classify the new reasons instead: "
                  + ", ".join(sorted(set(unclassified) - baseline)), file=sys.stderr)
            return 1
        BASELINE.write_text(json.dumps({"unclassified": unclassified}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"check-stop-exits: baseline written ({len(unclassified)} unclassified)")
        return 0
    errors = check(found, stop_exits.EXITS, baseline)
    for text in errors:
        print(text, file=sys.stderr)
    if errors:
        print(f"check-stop-exits: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    live = sum(1 for item in stop_exits.EXITS.values() if item.get("live"))
    print(f"check-stop-exits: {len(found)} reasons; {len(stop_exits.EXITS)} classified ({live} live), {len(baseline)} in the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
