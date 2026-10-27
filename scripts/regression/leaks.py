#!/usr/bin/env python3
"""从页面快照（scripts/smoke/inventory.py 的 JSONL）列出「漏出的框架默认入口」：产品声明（hooks.py 的 pearnly_navigation，
ADR-009）里登记的页面算承接；其余能打开（不是无权限页、没被跳走）的地址、它们上面的按钮 / 菜单，以及各页都在的全局入口
（应用切换菜单、快捷键、顶栏搜索框）逐岗位列出。输出 Markdown。

    python3 scripts/regression/leaks.py <快照.jsonl>…
"""
from __future__ import annotations

import ast
import json
import os
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

# 声明取被测的那份源码（回归实验室挂的 PEARNLY_REGRESSION_SRC），没给就取本仓
ROOT = Path(os.environ.get("PEARNLY_REGRESSION_SRC") or Path(__file__).resolve().parents[2])
APPS = {"erp": "pearnly_erp_portal", "cowork": "pearnly_cowork", "dms": "pearnly_dms"}


def declared(product: str) -> set[str]:
    app = APPS[product]
    source = (ROOT / f"apps/{app}/{app}/hooks.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "pearnly_navigation" for t in n.targets))
    entry = ast.literal_eval(node.value)[0]
    routes = {page[1] for page in entry["pages"]} | set(entry.get("available") or [])
    # 产品声明里放行的框架路由（`routes`，前两段，如 `query-report/ERP Stock Count Variance`）
    return routes | set(entry.get("routes") or [])


def allowed(path: str, ours: set[str], account: str) -> bool:
    """这个地址算不算产品承接：声明的页面、声明放行的框架路由（前两段）、本人的个人页。"""
    parts = [urllib.parse.unquote(p) for p in path.split("?")[0].split("/") if p][1:]
    if not parts or parts[0] in ours or "/".join(parts[:2]) in ours:
        return True
    return parts[:2] == ["user", account] and len(parts) == 2


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
            if inv.get("not_permitted") or allowed(final, ours, row.get("account", "")):
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


def matrix(paths: list[str]) -> None:
    """按产品汇成一张「声明外能打开的地址 × 岗位」表（✓ = 这个岗位能打开），外加各岗位在我们自己页面上看到的页头按钮 / 菜单。"""
    rows = [json.loads(line) for path in paths for line in open(path, encoding="utf-8") if line.strip()]
    by_product = defaultdict(list)
    for row in rows:
        by_product[row["product"]].append(row)
    for product, items in by_product.items():
        ours = declared(product)
        roles = list(dict.fromkeys(row["role"] for row in items))
        opened = defaultdict(set)
        for row in items:
            inv = row.get("inventory") or {}
            if not inv or inv.get("not_permitted"):
                continue
            final = (row.get("final") or row["path"]).split("?")[0]
            if allowed(final, ours, row.get("account", "")):
                continue
            parts = final.strip("/").split("/")
            # 具体记录（某个人、某张单）归成「<记录>」：同一类表单只算一个入口
            key = "/".join(parts[:2]) + ("/<记录>" if len(parts) > 2 else "")
            opened[key].add(row["role"])
        print(f"\n## {product}：声明外能打开的地址 × 岗位（共 {len(opened)} 个地址）\n")
        print("| 地址 | " + " | ".join(roles) + " |\n|---|" + "---|" * len(roles))
        for key in sorted(opened, key=lambda k: (-len(opened[k]), k)):
            print(f"| `/{key}` | " + " | ".join("✓" if role in opened[key] else "" for role in roles) + " |")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--matrix"]:
        matrix(sys.argv[2:])
    else:
        main(sys.argv[1:])
