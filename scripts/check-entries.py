#!/usr/bin/env python3
"""页头入口声明静态检查（回归方案 A 节，主会话 10-26 审定）。

每个产品在自己的 `hooks.py` 里 `pearnly_navigation` 的 `actions` 声明「每一页页头允许出现的入口」（语义码），
外壳白名单（core `public/desk/actions.js`）、本检查、页面快照比对三处读同一份声明：

    'actions': {"pearnly-sales": ["Export", "New", ...],     # 我们自己的页面：代码里加的每个页头按钮都要在这里
                "Form/User": ["Save"],                       # 框架原生视图：只有这里列的才放行（运行时拦）
                "*": [...]}                                  # 全产品共用的页头入口（共用弹窗 / 助手函数加的）
    'shortcuts': ["ctrl+s"],                                 # 允许的全局快捷键（其余原生快捷键运行时注销）

本检查扫 `apps/**/public/desk/**/*.js`、`apps/**/page/**/*.js`、报表 JS 里加页头入口的调用
（add_custom_button / add_menu_item / set_primary_action / set_secondary_action / add_inner_button / add_action_item /
add_actions_menu_item / add_button），取第一个参数的语义码（`t("x.y")`、`__("Export")`、`w("..")`、`term("..")` 里的字面量）；
标签是变量、模板串的，要在同一行或上一行写 `// entry: 码1, 码2` 说明会出现哪些码。码不在所属产品声明里（core 的文件：在任一产品
声明里）就失败。

    python3 scripts/check-entries.py              # 检查（CI）
    python3 scripts/check-entries.py --generate   # 按存量调用生成各产品 actions（只增不减，打印 JSON，人工并进 hooks）
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = {"pearnly_erp_portal": "erp", "pearnly_cowork": "cowork", "pearnly_dms": "dms"}
CALLS = ("add_custom_button", "add_menu_item", "set_primary_action", "set_secondary_action", "add_inner_button",
         "add_action_item", "add_actions_menu_item", "add_button")
CALL = re.compile(r"([\w$.\]\[\)]+)\.(" + "|".join(CALLS) + r")\(")
# 只认页头：接收者是页面或表单（`page`、`this.page`、`frm`、`report.page`……）；弹窗（`d.` / `dialog.`）上的同名方法不在页头
PAGE_RECEIVER = re.compile(r"(^|\.)(page|frm|listview|list_view)$|^(cur_frm|cur_page)$")
# 标签的语义码：包装函数（`t`、`__`、`w`、`term`、`acct`、`ft`……）里的第一个字面量
LITERAL = re.compile(r"""^\s*[\w$.]+\(\s*(["'])(.+?)\1""")
NOTE = re.compile(r"//\s*entry:\s*(.+)$")
# 不扫的：上游原样搬来的官方 POS 组件（在我们注册的收银页里，按钮由上游组件自己加，收银页整体登记）、控制面
SKIP = ("official-pos", "/pearnly_control/", "node_modules")


def declarations() -> dict:
    """各产品 hooks 里的 `pearnly_navigation[0]` → ``{product: {"actions": {...}, "shortcuts": [...]}}``。"""
    found = {}
    for app, product in PRODUCTS.items():
        tree = ast.parse((ROOT / "apps" / app / app / "hooks.py").read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "pearnly_navigation" for t in node.targets):
                entry = ast.literal_eval(node.value)[0]
                found[product] = {"actions": entry.get("actions") or {}, "shortcuts": entry.get("shortcuts") or []}
    return found


def first_argument(text: str, start: int) -> str:
    """调用括号里第一个参数的源码（按括号 / 引号配对找到顶层逗号）。"""
    depth, quote, out = 0, "", []
    for char in text[start:]:
        if quote:
            out.append(char)
            if char == quote and (len(out) < 2 or out[-2] != "\\"):
                quote = ""
            continue
        if char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            break
        out.append(char)
    return "".join(out).strip()


def calls() -> list[dict]:
    """扫出的每个调用：文件、行号、产品（core 为 None）、码（列表；空 = 认不出又没注明）。"""
    found = []
    for path in sorted(ROOT.glob("apps/*/*/**/*.js")):
        rel = path.relative_to(ROOT).as_posix()
        if any(skip in rel for skip in SKIP) or "/public/dist/" in rel:
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for match in CALL.finditer(text):
            if not PAGE_RECEIVER.search(match.group(1)):
                continue
            line = text.count("\n", 0, match.start()) + 1
            argument = first_argument(text, match.end())
            literal = LITERAL.match(argument)
            notes = [NOTE.search(lines[index]) for index in (line - 1, line - 2) if 0 <= index < len(lines)]
            note = next((found_note for found_note in notes if found_note), None)
            if note:
                codes = [token.group() for code in note.group(1).split(",")
                         if (token := re.match(r"[\w.\-]+", code.strip()))]
            elif literal and literal.group(2) and "${" not in literal.group(2):
                codes = [literal.group(2)]
            else:
                codes = []
            app = rel.split("/")[1]
            found.append({"file": rel, "line": line, "product": PRODUCTS.get(app), "codes": codes,
                          "argument": argument[:60], "call": match.group(2)})
    return found


def page_routes() -> dict:
    """登记表里页面入口的「文件 → 页面路由」（`url = "/desk/<路由>"`）。"""
    data = tomllib.loads((ROOT / "scripts/impact/shared-parts.toml").read_text(encoding="utf-8"))
    routes = {}
    for entry in data.get("entry", []):
        url = str(entry.get("url") or "")
        if url.startswith("/desk/pearnly-"):
            product = str(entry.get("site") or entry.get("id", "").split(".")[0])
            for file in entry.get("files") or []:
                routes.setdefault(file, set()).add((product, url[len("/desk/"):].split("/")[0]))
    return routes


def declared_codes(declared: dict, product: str | None) -> set:
    products = [product] if product else list(declared)
    return {code for name in products for codes in (declared.get(name) or {}).get("actions", {}).values()
            for code in codes}


def check() -> int:
    declared = declarations()
    problems = []
    for call in calls():
        if not call["codes"]:
            problems.append(f"{call['file']}:{call['line']}: {call['call']}({call['argument']}…) 标签认不出语义码，"
                            f"在同一行或上一行写 `// entry: 码`")
            continue
        known = declared_codes(declared, call["product"])
        for code in call["codes"]:
            if code not in known:
                where = f"{call['product']} 的" if call["product"] else "任一产品的"
                problems.append(f"{call['file']}:{call['line']}: 「{code}」不在{where} pearnly_navigation actions 里")
    for problem in problems:
        print(problem)
    if problems:
        print(f"页头入口声明不完整：{len(problems)} 处（见 scripts/check-entries.py 文件头）")
        return 1
    total = sum(len(codes) for item in declared.values() for codes in item["actions"].values())
    print(f"页头入口声明完整：{len(calls())} 个调用点，{total} 个声明码")
    return 0


def generate() -> int:
    """按存量调用生成各产品 actions：能从登记表对上页面的放在那页，对不上的放 "*"（只增不减）。"""
    declared = declarations()
    routes = page_routes()
    out = {product: {route: list(codes) for route, codes in item["actions"].items()}
           for product, item in declared.items()}
    for call in calls():
        targets = routes.get(call["file"]) or set()
        products = [call["product"]] if call["product"] else list(PRODUCTS.values())
        for product in products:
            pages = sorted(route for owner, route in targets if owner == product) or ["*"]
            for page in pages:
                bucket = out.setdefault(product, {}).setdefault(page, [])
                bucket += [code for code in call["codes"] if code not in bucket]
    print(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true")
    args = parser.parse_args()
    return generate() if args.generate else check()


if __name__ == "__main__":
    sys.exit(main())
