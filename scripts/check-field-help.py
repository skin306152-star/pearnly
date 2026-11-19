#!/usr/bin/env python3
"""字段说明闸门：ERP / Cowork 的字段说明收在标题旁的说明图标里，不写在字段下方（2026-11-02，用户：「cowork 和 erp 全部有这样的地方，
全部按这个做。dms 和后台管理不用」）。

做法在共用层一处（`pearnly_core/public/desk/field-help.js`，扩展框架 `ControlInput.set_description`），所以新写的字段带 `description`
自动就是图标，不用逐个登记。这里拦的是：
- 共用组件没挂上（`pearnly_core/hooks.py` 的 `app_include_js`，要在 `product.js` 之后）、或 ERP / Cowork 没声明 `field_help: "icon"`、
  或 DMS 声明了（DMS 照原生；控制面不装 pearnly_core）；
- 产品前端绕开它写到字段下方：`set_new_description(`、直接操作 `help-box`、`toggle_description(`、框架的 `show_description_on_click`；
- 例外 `description_inline`（运行时写的状态与数值，照原生留在下方）每个文件几处要登记在 `scripts/field-help-inline.json`（写原因），
  只许减少不许增加：比对 merge-base(origin/main) 那一版，新文件或条数变多都拦；条数变少了也报出来，要把登记改小。

页面上实际有没有漏（框架自己画的表单、弹窗）由流程用例每一步后检查（`scripts/regression/ui_steps.mjs`，`scripts/flows/run.py`
的「field descriptions below fields」）。

    python3 scripts/check-field-help.py

只读、不连网络。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "scripts" / "field-help-inline.json"
MODULE = ROOT / "apps" / "pearnly_core" / "pearnly_core" / "public" / "desk" / "field-help.js"
CORE_HOOKS = ROOT / "apps" / "pearnly_core" / "pearnly_core" / "hooks.py"
DECLARE = {"pearnly_erp_portal": True, "pearnly_cowork": True, "pearnly_dms": False, "pearnly_control": False}
FRONTENDS = [ROOT / "apps" / app / app / "public" for app in ("pearnly_core", "pearnly_erp_portal", "pearnly_cowork")]
BYPASS = re.compile(r"set_new_description\(|help-box|toggle_description\(|show_description_on_click")
INLINE = re.compile(r"\bdescription_inline\b")
DECLARED = re.compile(r"""['"]field_help['"]\s*:\s*['"]icon['"]""")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def sources():
    for base in FRONTENDS:
        for path in sorted(base.rglob("*.js")):
            if "node_modules" in path.parts or "dist" in path.parts or path == MODULE:
                continue
            yield path


def wiring() -> list[str]:
    problems = []
    if not MODULE.exists():
        return [f"{rel(MODULE)} 不在了：ERP / Cowork 的字段说明会回到字段下方"]
    line = next((row for row in CORE_HOOKS.read_text(encoding="utf-8").splitlines() if row.startswith("app_include_js =")), "")
    product, module = line.find('"desk/product.js"'), line.find('"desk/field-help.js"')
    if module < 0:
        problems.append(f"{rel(CORE_HOOKS)}：app_include_js 没挂 desk/field-help.js")
    elif product < 0 or module < product:
        problems.append(f"{rel(CORE_HOOKS)}：desk/field-help.js 要挂在 desk/product.js 之后")
    for app, wanted in DECLARE.items():
        hooks = ROOT / "apps" / app / app / "hooks.py"
        if not hooks.exists():
            continue
        found = bool(DECLARED.search(hooks.read_text(encoding="utf-8")))
        if found != wanted:
            problems.append(f"{rel(hooks)}：{'要' if wanted else '不要'}在 pearnly_navigation 里声明 field_help: 'icon'")
    return problems


def counts() -> tuple[dict[str, int], list[str]]:
    found, problems = {}, []
    for path in sources():
        for number, row in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BYPASS.search(row):
                problems.append(f"{rel(path)}:{number}：绕开说明图标写到字段下方（{BYPASS.search(row).group(0)}）；"
                                "说明写在字段的 description 上，运行时状态用 description_inline 并登记")
            hits = len(INLINE.findall(row))
            if hits:
                found[rel(path)] = found.get(rel(path), 0) + hits
    return found, problems


def base_registry() -> dict | None:
    base = subprocess.run(["git", "-c", "core.quotepath=off", "merge-base", "origin/main", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not base:
        return None
    shown = subprocess.run(["git", "-c", "core.quotepath=off", "show", f"{base}:{rel(REGISTRY)}"], cwd=ROOT, capture_output=True, text=True)
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def main() -> int:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    problems = wiring()
    found, bypass = counts()
    problems += bypass
    for path, count in sorted(found.items()):
        entry = registry.get(path)
        if not entry:
            problems.append(f"{path}：{count} 处 description_inline 没登记（{rel(REGISTRY)}，写清是哪种运行时状态）")
        elif count > entry["count"]:
            problems.append(f"{path}：description_inline {count} 处，登记的是 {entry['count']} 处")
        elif count < entry["count"]:
            problems.append(f"{path}：description_inline 只剩 {count} 处，把登记从 {entry['count']} 改小")
        if entry and not str(entry.get("reason") or "").strip():
            problems.append(f"{path}：登记没写原因")
    for path in sorted(set(registry) - set(found)):
        problems.append(f"{path}：登记了 description_inline，文件里已经没有了，删掉这条")
    base = base_registry()
    if base is not None:
        for path, entry in sorted(registry.items()):
            before = base.get(path)
            if before is None:
                problems.append(f"{path}：新登记的 description_inline 例外（只许减少；确需例外先问主会话）")
            elif entry["count"] > before["count"]:
                problems.append(f"{path}：description_inline 登记从 {before['count']} 加到 {entry['count']}（只许减少）")
    for problem in problems:
        print(problem)
    total = sum(found.values())
    print(f"字段说明：共用组件{'已' if not wiring() else '未'}接上；留在下方的例外 {total} 处（{len(found)} 个文件）"
          + ("" if not problems else f"，{len(problems)} 个问题"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
