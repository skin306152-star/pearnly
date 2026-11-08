#!/usr/bin/env python3
"""CC 工具一致检查（ADR-027 标准二，2026-11-08）：同类工具在 Cowork 与 ERP 两个产品里，去掉产品前缀（``cowork_`` / ``erp_``）后
逐项一致——步骤名（做完 / 进行中，英文原文与四语译文）、参数（名字、类型、必填）、要不要「允许」卡片、限哪种会话（modes）；
代录（CC 独有）两产品的接法声明（``pearnly_express_operator``、``pearnly_write_precheck`` 的键）也要一样。
确属产品差异的逐条登记在 ``scripts/impact/cc-tools-baseline.txt``（只许减少：已经一致或工具不在了的条目要删）。

    python3 scripts/check-cc-tools.py            # 在生产镜像里跑（要导入两个产品的工具声明，见 ci-candidate-checks.sh）

纯比对逻辑在 ``compare()``，不导入 Frappe，单测 ``tests/test_check_cc_tools.py``。
"""
from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = {"cowork": "pearnly_cowork", "erp": "pearnly_erp_portal"}
BASELINE = ROOT / "scripts" / "impact" / "cc-tools-baseline.txt"


def _hook(app: str, name: str) -> list:
    tree = ast.parse((ROOT / "apps" / app / app / "hooks.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return list(ast.literal_eval(node.value))
    return []


def _attr(path: str):
    module, _sep, attr = path.rpartition(".")
    return getattr(importlib.import_module(module), attr)


def declared(product: str) -> tuple[dict, dict]:
    """这个产品声明的工具 {名字: 声明} 与进行中的说法 {名字: 英文}（不含共用层的工具：两个产品是同一份）。"""
    app = PRODUCTS[product]
    tools = {}
    for path in _hook(app, "pearnly_assistant_tools"):
        if path.startswith(app + "."):
            for tool in _attr(path) or []:
                tools[tool["name"]] = tool
    steps = {}
    for path in _hook(app, "pearnly_assistant_step_names"):
        if path.startswith(app + "."):
            steps.update(_attr(path) or {})
    return tools, steps


def glossary(product: str) -> dict:
    path = ROOT / "apps" / PRODUCTS[product] / PRODUCTS[product] / "i18n" / "messages.tsv"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        cells = line.split("\t")
        out.setdefault(cells[0], cells[1:4])
    return out


def _schema(tool: dict) -> dict:
    """参数的形状：{名字: 类型}、必填集合。说明文字不比（两个产品的业务说法可以不同）。"""
    schema = tool.get("input_schema") or {}
    props = {name: str((spec or {}).get("type") or "") for name, spec in (schema.get("properties") or {}).items()}
    return {"props": props, "required": sorted(schema.get("required") or [])}


def _approval(tool: dict):
    gate = tool.get("approval")
    return sorted(gate) if isinstance(gate, (list, tuple)) else bool(gate)


def compare(products: dict, words: dict) -> list[str]:
    """``products``：{产品: (工具 {名字: 声明}, 进行中说法 {名字: 英文})}；``words``：{产品: 术语表}。回差异条目
    （``<去前缀的名字>:<项>``，项为 label / step / words / params:<参数> / required / approval / modes）。"""
    (c_tools, c_steps), (e_tools, e_steps) = products["cowork"], products["erp"]
    base = lambda name, prefix: name[len(prefix):] if name.startswith(prefix) else None  # noqa: E731
    cowork = {base(n, "cowork_"): n for n in c_tools if base(n, "cowork_")}
    erp = {base(n, "erp_"): n for n in e_tools if base(n, "erp_")}
    diffs = []
    for key in sorted(set(cowork) & set(erp)):
        c, e = c_tools[cowork[key]], e_tools[erp[key]]
        if c.get("label") != e.get("label"):
            diffs.append(f"{key}:label")
        if c_steps.get(cowork[key]) != e_steps.get(erp[key]):
            diffs.append(f"{key}:step")
        for text in {c.get("label"), c_steps.get(cowork[key])} - {None}:
            if text in words["cowork"] and words["cowork"].get(text) != words["erp"].get(text):
                diffs.append(f"{key}:words")
                break
        cs, es = _schema(c), _schema(e)
        for name in sorted(set(cs["props"]) | set(es["props"])):
            if cs["props"].get(name) != es["props"].get(name):
                diffs.append(f"{key}:params:{name}")
        if cs["required"] != es["required"]:
            diffs.append(f"{key}:required")
        if _approval(c) != _approval(e):
            diffs.append(f"{key}:approval")
        if sorted(c.get("modes") or []) != sorted(e.get("modes") or []):
            diffs.append(f"{key}:modes")
    return diffs


OPERATOR_HOOKS = ("pearnly_express_operator", "pearnly_write_precheck")


def _hook_dict(app: str, name: str) -> dict:
    tree = ast.parse((ROOT / "apps" / app / app / "hooks.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            value = ast.literal_eval(node.value)
            return value if isinstance(value, dict) else {}
    return {}


def operator_hooks() -> dict:
    """两产品代录与开始前卡片的接法声明（代录是 CC 独有，两产品要一样，ADR-027 A）：{产品: {钩子: [键]}}。只读 hooks.py，不导入。"""
    return {product: {hook: sorted(_hook_dict(app, hook)) for hook in OPERATOR_HOOKS} for product, app in PRODUCTS.items()}


def compare_operator(hooks: dict) -> list[str]:
    """两产品代录接法：一边声明了、另一边没有的键 → ``operator:<钩子>:<键>``。"""
    diffs = []
    for hook in OPERATOR_HOOKS:
        cowork, erp = set(hooks["cowork"].get(hook) or ()), set(hooks["erp"].get(hook) or ())
        diffs += [f"operator:{hook}:{key}" for key in sorted(cowork ^ erp)]
    return diffs


def baseline(path=BASELINE) -> list:
    if not Path(path).exists():
        return []
    return [line.split("#", 1)[0].strip() for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()]


def check(diffs: list, known: list) -> list[str]:
    errors = []
    if len(set(known)) != len(known):
        errors.append("cc-tools-baseline.txt 有重复条目")
    for item in sorted(set(diffs) - set(known)):
        errors.append(f"两产品同类工具不一致：{item} —— 改成一致（ADR-027 工具模板），确属产品差异的登记进 cc-tools-baseline.txt 并写原因")
    for item in sorted(set(known) - set(diffs)):
        errors.append(f"基线该删：cc-tools-baseline.txt 里的 {item} 已经一致或工具不在了，删掉这一条（基线只许减少）")
    return errors


def main() -> int:
    for app in ("pearnly_core", *PRODUCTS.values()):
        sys.path.insert(0, str(ROOT / "apps" / app))
    products = {product: declared(product) for product in PRODUCTS}
    words = {product: glossary(product) for product in PRODUCTS}
    errors = check(compare(products, words) + compare_operator(operator_hooks()), baseline())
    if errors:
        print(f"CC 工具一致检查：不通过（{len(errors)} 处）", file=sys.stderr)
        for error in errors:
            print("  - " + error, file=sys.stderr)
        return 1
    pairs = len({n[7:] for n in products["cowork"][0] if n.startswith("cowork_")} & {n[4:] for n in products["erp"][0] if n.startswith("erp_")})
    print(f"CC 工具一致检查：通过（两产品同类工具 {pairs} 对，登记的产品差异 {len(baseline())} 条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
