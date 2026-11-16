#!/usr/bin/env python3
"""CC 引导声明闸：每个产品 hooks ``pearnly_guides`` 列出的声明都合形状、指向的函数与工具都在（总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2、第 3 节）。

一项业务的引导是产品写的一份纯字面量声明（``<模块>.GUIDES``），共用引擎据此出菜单与卡。这里不导入产品模块（它们连着 Frappe），
按源码读：

1. hooks 里 ``pearnly_guides`` 的每个路径指向的模块有 ``GUIDES``，且是纯字面量；
2. 每份声明过 ``pearnly_core.assistant.guide.declaration.problems``（键、取值、步骤、依赖）；
3. 声明里的每个点路径（权限、选项来源、默认值、草稿、复核、确定、结果）指向的函数在那个模块的源码里；
4. ``tool``（打字路径上办同一件事的工具）在这个产品的工具声明里；
5. 一个产品里同一个 id 只声明一次；两个产品都有的业务（采购、销售）菜单上的字、图标、顺序、出题方式一致。

    python3 scripts/check-cc-guides.py            # 检查
    python3 scripts/check-cc-guides.py --list     # 列出每个产品声明了哪些业务

只读；不连网络。
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.assistant.guide import declaration  # noqa: E402

HOOK = "pearnly_guides"
SAME_ACROSS_PRODUCTS = ("label_key", "icon", "order", "source", "opening_key", "writes")


def _module_file(root: Path, dotted: str) -> Path | None:
    """``app.pkg.mod`` → ``apps/app/app/pkg/mod.py``（或包的 ``__init__.py``）；找不到回 None。"""
    parts = dotted.split(".")
    base = root / "apps" / parts[0] / Path(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _assigned(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return node.value
    return None


def _defines(root: Path, dotted: str) -> bool:
    """点路径最后一段是那个模块顶层的函数、类或赋值。"""
    module, _, attr = dotted.rpartition(".")
    path = _module_file(root, module)
    if path is None:
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any((isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == attr)
               for node in tree.body) or _assigned(tree, attr) is not None


def _tool_names(root: Path, app: str) -> set:
    """这个产品源码里声明过的工具名（``{"name": "<产品>_...", ...}`` 字面量的 name）。"""
    names = set()
    for path in (root / "apps" / app / app).rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"name"' not in text:
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "name" and isinstance(value, ast.Constant) \
                            and isinstance(value.value, str):
                        names.add(value.value)
    return names


def products(root: Path = ROOT) -> Dict[str, List[str]]:
    """``{产品 App: [声明路径]}``：各 App 的 hooks.py 里 ``pearnly_guides`` 的字面量。"""
    found: Dict[str, List[str]] = {}
    for hooks in sorted((root / "apps").glob("*/*/hooks.py")):
        value = _assigned(ast.parse(hooks.read_text(encoding="utf-8")), HOOK)
        if value is not None:
            found[hooks.parent.name] = list(ast.literal_eval(value))
    return found


def load(root: Path = ROOT) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
    """(``{产品: [声明]}``, 读不出来的问题)。"""
    guides: Dict[str, List[Dict[str, Any]]] = {}
    errors: List[str] = []
    for app, paths in products(root).items():
        guides[app] = []
        for dotted in paths:
            module, _, attr = dotted.rpartition(".")
            path = _module_file(root, module)
            value = _assigned(ast.parse(path.read_text(encoding="utf-8")), attr) if path else None
            if value is None:
                errors.append(f"{app}: {dotted} not found")
                continue
            try:
                literal = ast.literal_eval(value)
            except ValueError:
                errors.append(f"{app}: {dotted} is not a plain literal")
                continue
            guides[app].extend(literal if isinstance(literal, list) else [literal])
    return guides, errors


def check(root: Path = ROOT) -> List[str]:
    guides, errors = load(root)
    by_id: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for app, items in guides.items():
        tools = None
        seen = set()
        for guide in items:
            found = declaration.problems(guide)
            errors.extend(f"{app}: {text}" for text in found)
            if found:
                continue
            name = guide["id"]
            if name in seen:
                errors.append(f"{app}: {name}: declared twice")
            seen.add(name)
            errors.extend(f"{app}: {name}: {dotted} does not exist" for dotted in declaration.paths(guide)
                          if not _defines(root, dotted))
            if guide.get("tool"):
                tools = _tool_names(root, app) if tools is None else tools
                if guide["tool"] not in tools:
                    errors.append(f"{app}: {name}: tool {guide['tool']} is not declared in this product")
            by_id.setdefault(name, []).append((app, guide))
    for name, owners in by_id.items():
        first_app, first = owners[0]
        for app, guide in owners[1:]:
            errors.extend(f"{name}: {key} differs between {first_app} and {app}"
                          for key in SAME_ACROSS_PRODUCTS if guide.get(key) != first.get(key))
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        for app, items in load()[0].items():
            for guide in items:
                print(f"{app}\t{guide.get('id')}\t{guide.get('source')}\t{','.join(guide.get('modes') or ())}")
        return 0
    errors = check()
    for text in errors:
        print(text, file=sys.stderr)
    if errors:
        print(f"check-cc-guides: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    total = sum(len(items) for items in load()[0].values())
    print(f"check-cc-guides: {total} guide declaration(s) ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
