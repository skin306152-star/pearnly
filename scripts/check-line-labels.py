# -*- coding: utf-8 -*-
"""LINE 按钮文字闸门：用作 LINE 按钮标签的词条，中 / 英 / 泰 / 日任何一种超过 20 个字符就拦。

为什么：LINE 的按钮（快捷回复、Flex 按钮、富菜单格子）label 最多 20 个字符，代码里的 `str(label)[:20]`
会把超长的词静默截断，用户看到半截字（用户 2026-11-08，主会话转：「截断属于缺陷」）。所以长度在词条上拦，
不靠运行时截。由数据拼成的按钮（公司、供应商、仓库名）不归这里管，照旧在运行时截短。

怎么找：在 LINE 模块里找按钮标签的位置——字典里的 `"label"` 键、按钮辅助函数（`_button` / `_btn` /
`_qr_item` / `_item` …）的第一个参数或 `label=`——再从那个表达式里认出词条：
`_("英文原文")`、`_(表[...])`（模块里那张表的全部字符串）、DMS 的 `_t("键")`（按本模块 `_t` 的键前缀）、
`_t(键)` 而键来自遍历模块里的常量表、`cards.TXT_X` 这类老写法（= `dms.card.TXT_X`）。认出的词条去术语表
（`apps/*/*/i18n/messages.tsv`）按英文原文或键找四种语言。

用法：python3 scripts/check-line-labels.py [--list]   # --list 列出认出的全部按钮词条与各语言长度
退出码：有超长 = 1。
"""

from __future__ import annotations

import argparse
import ast
import csv
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIMIT = 20
LANGS = ("zh", "en", "th", "ja")
MODULES = (
    "apps/pearnly_core/pearnly_core/line*.py",
    "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/line_*.py",
    "apps/pearnly_erp_portal/pearnly_erp_portal/integrations/handlers.py",
    "apps/pearnly_cowork/pearnly_cowork/line/*.py",
    "apps/pearnly_dms/pearnly_dms/line/*.py",
    "apps/pearnly_dms/pearnly_dms/booking/conversation/*.py",
    "apps/pearnly_dms/pearnly_dms/query/*.py",
    "apps/pearnly_dms/pearnly_dms/menu/*.py",
    "apps/pearnly_dms/pearnly_dms/intake/*.py",
)
BUTTON_FUNCS = re.compile(r"^_?(button|btn|qr_item|item|qr|quick_item|choice|action)$")
TRANSLATE = {"_", "_lt"}
DMS_ATTR = re.compile(r"^(TXT|BTN|LBL|ST)_[A-Z0-9_]+$")


def glossary():
    by_en, by_key = {}, {}
    for path in glob.glob(os.path.join(ROOT, "apps/*/*/i18n/messages.tsv")):
        with open(path, encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
                texts = {lang: (row.get(lang) or "").replace("\\n", "\n") for lang in LANGS}
                if (row.get("key") or "").strip():
                    by_key[row["key"].strip()] = texts
                if (row.get("en") or "").strip():
                    by_en.setdefault(row["en"].strip(), texts)
    return by_en, by_key


def _name(func) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _strings(node) -> list:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


class Module:
    def __init__(self, path, tree):
        self.path = path
        self.tables = {}  # 模块级常量表：名字 → 节点
        self.prefix = ""  # 本模块 _t 的键前缀（dms.card.query. 这类）
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if isinstance(node.value, (ast.Dict, ast.Tuple, ast.List, ast.Set)):
                    self.tables[node.targets[0].id] = node.value
            if isinstance(node, ast.FunctionDef) and node.name == "_t":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.JoinedStr) and inner.values and isinstance(inner.values[0], ast.Constant):
                        self.prefix = inner.values[0].value
                if not self.prefix and "pearnly_dms" in path:
                    self.prefix = "dms.card."

    def refs(self, expr, loops, bound=None) -> list:
        """按钮标签表达式里认出的词条：[("en", 原文) | ("key", 键)]。bound：for 循环变量 → 字面量元组里那一列的表达式。"""
        out = []
        for node in ast.walk(expr):
            if isinstance(node, ast.Name) and bound and node.id in bound:
                for value in bound[node.id]:
                    out += self.refs(value, loops)
                continue
            if isinstance(node, ast.Call) and _name(node.func) in TRANSLATE and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append(("en", arg.value))
                elif isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name) and arg.value.id in self.tables:
                    table = self.tables[arg.value.id]
                    values = table.values if isinstance(table, ast.Dict) else [table]  # 取的是表的值，不是键
                    out += [("en", s) for value in values for s in _strings(value)]
            elif isinstance(node, ast.Call) and _name(node.func) == "_t" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.append(("key", self.prefix + arg.value))
                elif isinstance(arg, ast.Name) and arg.id in loops:
                    out += [("key", self.prefix + s) for s in loops[arg.id] if re.fullmatch(r"[A-Z][A-Z0-9_]*", s)]
            elif isinstance(node, ast.Attribute) and DMS_ATTR.match(node.attr) and "pearnly_dms" in self.path:
                out.append(("key", "dms.card." + node.attr))
        return out


def label_params(texts) -> dict:
    """各模块里带 `label` 参数的函数：函数名 → 这个参数的位置（`account_action(kind, label)` → 1）。"""
    out = {}
    for text in texts:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                names = [a.arg for a in node.args.args]
                # 只认把这个参数放进动作 "label" 键的函数（按钮）；卡片字段名 `_kv(label, value)` 不算
                into_action = any(
                    isinstance(d, ast.Dict) and any(
                        isinstance(k, ast.Constant) and k.value == "label" and "label" in
                        {n.id for n in ast.walk(v) if isinstance(n, ast.Name)} for k, v in zip(d.keys, d.values))
                    for d in ast.walk(node))
                if "label" in names and into_action:
                    out[node.name] = names.index("label")
    return out


def scan(path, text, params=None) -> list:
    """一个文件里的按钮词条：[(行, 种类, 值)]。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    module = Module(path, tree)
    params = params or {}
    found = []

    def visit(node, loops, bound):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Tuple) and isinstance(node.iter, (ast.Tuple, ast.List)):
            bound = dict(bound)
            rows = [row.elts for row in node.iter.elts if isinstance(row, (ast.Tuple, ast.List))]
            for index, target in enumerate(node.target.elts):
                if isinstance(target, ast.Name):
                    bound[target.id] = [row[index] for row in rows if index < len(row)]
        if isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp)):
            loops = dict(loops)
            for gen in node.generators:
                source = gen.iter
                while isinstance(source, ast.Call) and isinstance(source.func, ast.Attribute):
                    source = source.func.value
                if isinstance(source, ast.Subscript):
                    source = source.value
                table = module.tables.get(source.id) if isinstance(source, ast.Name) else None
                names = [n.id for n in ast.walk(gen.target) if isinstance(n, ast.Name)]
                if table is not None:
                    for name in names:
                        loops[name] = _strings(table)
        labels = []
        if isinstance(node, ast.Dict):
            labels += [v for k, v in zip(node.keys, node.values)
                       if isinstance(k, ast.Constant) and k.value == "label"]
        if isinstance(node, ast.Call):
            name = _name(node.func)
            position = params.get(name, 0 if BUTTON_FUNCS.match(name) else None)
            if position is not None:
                if len(node.args) > position:
                    labels.append(node.args[position])
                labels += [kw.value for kw in node.keywords if kw.arg == "label"]
        for label in labels:
            for kind, value in module.refs(label, loops, bound):
                found.append((getattr(label, "lineno", 0), kind, value))
        for child in ast.iter_child_nodes(node):
            visit(child, loops, bound)

    visit(tree, {}, {})
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="LINE 按钮文字闸门")
    parser.add_argument("--list", action="store_true", help="列出认出的全部按钮词条")
    args = parser.parse_args(argv)
    by_en, by_key = glossary()
    seen, over, unknown = {}, [], []
    files = {}
    for pattern in MODULES:
        for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
            rel = os.path.relpath(path, ROOT)
            if "/test" in rel or rel.endswith("_test.py"):
                continue
            with open(path, encoding="utf-8") as handle:
                files[rel] = handle.read()
    params = label_params(files.values())
    for rel, text in files.items():
            for line, kind, value in scan(rel, text, params):
                texts = (by_en if kind == "en" else by_key).get(value)
                ident = "%s:%s" % (kind, value)
                if texts is None:
                    unknown.append("%s:%d %s" % (rel, line, ident))
                    continue
                if ident in seen:
                    continue
                seen[ident] = "%s:%d" % (rel, line)
                for lang in LANGS:
                    if len(texts.get(lang) or "") > LIMIT:
                        over.append((ident, lang, texts[lang], seen[ident]))
    if args.list:
        for ident, where in sorted(seen.items()):
            texts = (by_en if ident.startswith("en:") else by_key)[ident.split(":", 1)[1]]
            print("%s\t%s\t%s" % (ident, where, " ".join("%s=%d" % (l, len(texts.get(l) or "")) for l in LANGS)))
    print("LINE 按钮文字：认出 %d 条词条" % len(seen))
    if over:
        print("超过 %d 个字符（LINE 会截断，改短到正式说法）：" % LIMIT)
        for ident, lang, text, where in over:
            print("  %s %s「%s」%d 字  (%s)" % (ident, lang, text, len(text), where))
        return 1
    print("全部不超过 %d 个字符。" % LIMIT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
