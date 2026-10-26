#!/usr/bin/env python3
"""从页面快照（scripts/smoke/inventory.py 的 JSONL）列出「漏出的框架默认入口」：产品声明（hooks.py 的 pearnly_navigation，
ADR-009）里登记的页面算承接；其余能打开（不是无权限页、没被跳走）的地址、它们上面的按钮 / 菜单，以及各页都在的全局入口
（应用切换菜单、快捷键、顶栏搜索框）逐岗位列出。输出 Markdown。

    python3 scripts/regression/leaks.py <快照.jsonl>…
"""
from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPS = {"erp": "pearnly_erp_portal", "cowork": "pearnly_cowork", "dms": "pearnly_dms"}


def declared(product: str) -> set[str]:
    app = APPS[product]
    source = (ROOT / f"apps/{app}/{app}/hooks.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "pearnly_navigation" for t in n.targets))
    entry = ast.literal_eval(node.value)[0]
    routes = {page[1] for page in entry["pages"]} | set(entry.get("available") or [])
    return routes


def route_of(path: str) -> str:
    parts = [p for p in path.split("?")[0].split("/") if p]
    return parts[1] if len(parts) > 1 else ""


def main(paths: list[str]) -> None:
    rows = [json.loads(line) for path in paths for line in open(path, encoding="utf-8") if line.strip()]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["product"], row["role"])].append(row)
    for (product, role), items in groups.items():
        ours = declared(product)
        print(f"\n### {product} · {role}（{items[0].get('account')}）\n")
        globals_ = defaultdict(set)
        leaks = []
        for row in items:
            inv = row.get("inventory") or {}
            if not inv:
                continue
            globals_["应用切换菜单"].update(inv.get("app_menu") or [])
            globals_["快捷键"].update(inv.get("shortcuts") or [])
            if (inv.get("navbar") or {}).get("search"):
                globals_["顶栏搜索框"].add("出现")
            final = row.get("final") or row["path"]
            route = route_of(final)
            if inv.get("not_permitted") or route in ours or route == "":
                continue
            if route_of(row["path"]) != route and route in ours:
                continue  # 被跳回到我们的页
            menu = [m["text"] for m in inv.get("head_menu") or [] if m.get("visible")]
            buttons = [b["text"] for b in inv.get("head_buttons") or []]
            footer = [k for k, v in (inv.get("form_footer") or {}).items() if v]
            leaks.append((final, buttons, menu, inv.get("side_section") or [], footer))
        for key, values in globals_.items():
            print(f"- 全局 · {key}：{'、'.join(sorted(values)) or '（无）'}")
        if not leaks:
            print("- 声明外的页面：没有能打开的")
            continue
        print(f"- 声明外能打开的页面 {len(leaks)} 个：\n")
        print("| 地址 | 页头按钮 | 页头菜单 | 侧边区 | 表单底部 |\n|---|---|---|---|---|")
        for final, buttons, menu, side, footer in sorted(leaks):
            print(f"| `{final}` | {'、'.join(buttons)} | {'、'.join(menu)} | {'、'.join(side)} | {'、'.join(footer)} |")


if __name__ == "__main__":
    main(sys.argv[1:])
