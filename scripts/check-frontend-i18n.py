# -*- coding: utf-8 -*-
"""业务前端的"防漏键"检查：代码里用的文案键，语言包里到底有没有。

## 为什么必须有它

前端的取词规则是"**取不到就显示键名**"（故意的：空白最难查，键名一眼看得出）。
好处是缺译看得见，代价是**它不报错** —— 页面上出现 `home.ledger.usage`
这种字样，构建、类型检查、单元测试全都不会拦。

2026-09-19 实测到的两种漏法，都是靠肉眼在页面上看见的：

1. 命名空间没登记进取词接口的白名单（`pearnly_core/browser_text.py`）——
   接口静默返回空，整页显示键名；
2. setup 时求值一次的标签（页签、下拉）—— 文案还没取回来，永远显示键名。

第 2 种这条脚本管不了（那是响应式写法问题），但**第 1 种和"包缺键"它全管**。

## 判据

· 代码里出现 `t("x")` / `tf("x")` / `t('x')` → `x` 必须在四份语言包里都有；
· 键的首段（`dms` / `nav` / `home` …）必须登记在 `browser_text.APP_BY_PREFIX` 里。

用法：

    python3 scripts/check-frontend-i18n.py           # 报告
    python3 scripts/check-frontend-i18n.py --strict  # 有问题退出码 1
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "frontend", "src")
BROWSER_TEXT = os.path.join(ROOT, "apps", "pearnly_core", "pearnly_core", "browser_text.py")

LANGS = ("en", "zh", "th", "ja")
# 取词调用：t("k") / tf("k", …) / t('k')
USED = re.compile(r"""\b(?:t|tf)\(\s*(['"])(?P<key>[A-Za-z][\w.]*\.[\w.]+)\1""")


def packs() -> dict:
    """Read every product translation pack that can be served to Desk."""
    out: dict = {}
    for app in ("pearnly_core", "pearnly_dms", "pearnly_control", "pearnly_cowork", "pearnly_erp_portal"):
        for lang in LANGS:
            path = os.path.join(ROOT, "apps", app, app, "translations", lang + ".csv")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8", newline="") as handle:
                for row in csv.reader(handle):
                    if len(row) >= 2 and row[0].strip():
                        out.setdefault(row[0], set()).add(lang)
    return out


def allowed_prefixes() -> set:
    src = open(BROWSER_TEXT, encoding="utf-8").read()
    block = src.split("APP_BY_PREFIX = {", 1)[1].split("}", 1)[0]
    return set(re.findall(r'"([\w.]+)":', block))


def used_keys() -> dict:
    """前端代码里用到的键 → 出现在哪些文件。"""
    found: dict = {}
    roots = [SRC] + [os.path.join(ROOT, "apps", app, app, "public", "desk")
                     for app in ("pearnly_core", "pearnly_dms", "pearnly_cowork", "pearnly_erp_portal")]
    for base, dirs, files in (entry for root in roots for entry in os.walk(root)):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "dist")]
        for name in sorted(files):
            if not name.endswith((".vue", ".ts", ".js")):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, ROOT)
            try:
                src = open(path, encoding="utf-8").read()
            except OSError:
                continue
            # 注释里的例子不算（说明里会举 `t("xxx")`）
            body = "\n".join(
                l for l in src.split("\n") if not l.strip().startswith(("//", "*", "/*"))
            )
            for match in USED.finditer(body):
                key = match.group("key")
                if key.endswith("."):
                    continue  # Dynamic prefixes are verified by the page contract tests.
                found.setdefault(key, set()).add(rel)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    table = packs()
    prefixes = allowed_prefixes()
    keys = used_keys()

    missing_pack = []
    missing_lang = []
    bad_prefix = []
    for key in sorted(keys):
        head = key.split(".", 1)[0] + "."
        if head not in prefixes:
            bad_prefix.append((key, sorted(keys[key])[0]))
        langs = table.get(key)
        if not langs:
            missing_pack.append((key, sorted(keys[key])[0]))
        elif set(LANGS) - langs:
            missing_lang.append((key, sorted(set(LANGS) - langs)))

    print("前端用到的文案键：%d 条（%d 个文件）" % (len(keys), len({f for v in keys.values() for f in v})))
    print("取词接口登记的前缀：%s" % " ".join(sorted(prefixes)))
    print("")
    print("① 命名空间没登记（接口会静默返回空 → 整页显示键名）：%d 条" % len(bad_prefix))
    for key, where in bad_prefix:
        print("   %-34s %s" % (key, where))
    print("② 语言包里根本没有这个键：%d 条" % len(missing_pack))
    for key, where in missing_pack:
        print("   %-34s %s" % (key, where))
    print("③ 有键但缺某门语言的译：%d 条" % len(missing_lang))
    for key, langs in missing_lang:
        print("   %-34s 缺 %s" % (key, " ".join(langs)))

    problems = len(bad_prefix) + len(missing_pack) + len(missing_lang)
    print("")
    print("合计问题 %d 条" % problems)
    if args.strict and problems:
        print("RESULT: FAIL（--strict）")
        return 1
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
