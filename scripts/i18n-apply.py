# -*- coding: utf-8 -*-
"""按术语表把代码里的**中文文案**换成英文 msgid（改代码的那一半）。

`i18n-build.py` 负责"术语表 → 各 App 的翻译包"；这个脚本负责
"代码里的中文 → 术语表里的英文 msgid"。两个方向都走同一张术语表，
所以代码和译文不会各说各的。

改什么、不改什么：

· **改**：DocType JSON 的 `label` / `description`；`_("中文")` / `__("中文")`
  / `_lt("中文")` 这类**已经包在翻译函数里**的 msgid。
· **不改**（列出来给人看，由人判断）：裸中文串。它们可能是数据（字典里的角色名、
  清单里的规格说明），也可能是漏了 `_()` 的界面文字 —— 自动包一层 `_()` 会把
  数据也变成 msgid，那是另一类 bug。所以这类只报告，不动手。
· **不动排版**：DocType JSON 是手写的紧凑格式（一个字段一行），用 `json.dumps`
  重排会产生整文件 diff。所以这里按**值**做定点替换。

用法：

    python3 scripts/i18n-apply.py --check      # 只报告会改什么、哪些没收录
    python3 scripts/i18n-apply.py              # 真改
    python3 scripts/i18n-apply.py --app pearnly_control
    python3 scripts/i18n-apply.py --scope json # 只改 DocType 标签，不动服务端消息
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from i18n_glossary import APPS, read_glossary as _read_glossary

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CJK = re.compile(r"[\u4e00-\u9fff]")

# 同一个中文在不同字段要翻成不同英文时在这里点名，键 = (fieldname, key)。
# 只放**真冲突**的：两个英文 msgid 撞了同一个中文，机器分不出来。
OVERRIDES = {
    ("steps", "label"): "Steps",
    ("step_key", "label"): "Step",
    ("feature", "label"): "Feature",
    ("section_features", "label"): "Features",
    # 「发号单」：DocType 名是单数（Pearnly Issue），而删除汇总那行是"几条"（Issues）
    ("issue", "label"): "Issue",
    # 「部署」：字段指向一条部署（单数）；删除汇总那行是"几条"（Deployments）
    ("deployment", "label"): "Deployment",
}

SKIP_DIRS = {"node_modules", "__pycache__", ".git", "public", "patches", "templates", "config"}


def read_glossary():
    """返回 (按 App 的中文→英文, 全局中文→英文)。

    **必须按 App 查**：同一个中文在不同 App 可以是不同的英文
    （`备注` 在 core 是 Notes、`发号单` 在 control 是 Issue）。不分 App 就会
    挑到别家的英文，而且改完看不出来 —— 这种错只能靠这条规则挡（踩过）。

    术语表按产品各存一份（`apps/<app>/<app>/i18n/messages.tsv`），行里带的 `app`
    由文件位置决定，所以"挂错 App"这件事本身不可能再发生。

    同一个 App 内、同一个中文对应多个英文 = 术语表自己有问题，报出来让人点名
    （点名的方式是 OVERRIDES）。
    """
    rows, problems = _read_glossary()
    for problem in problems:
        print("  [术语表] %s" % problem)
    by_app = {}
    anywhere = {}
    for row in rows:
        app, zh, en = row["app"], row["zh"], row["en"]
        if not app or not en or not zh:
            continue
        key = (app, zh)
        if key in by_app and by_app[key] != en:
            print("  [术语表] %s 里同一个中文对应多个英文：%s → %s / %s（字段名不同的用 "
                  "OVERRIDES 点名；只用于出译文的行可以不管）"
                  % (app, zh, by_app[key], en))
        by_app.setdefault(key, en)
        anywhere.setdefault(zh, en)
    return by_app, anywhere


def _lookup(by_app, anywhere, app, zh):
    """先按本 App 找，找不到再全局找（并提示 —— 可能是术语表的 app 列写错了）。"""
    if (app, zh) in by_app:
        return by_app[(app, zh)]
    if zh in anywhere:
        print("  [提示] %s 里用到的「%s」在术语表里挂在别的 App 下，用了 %s。核对一下 app 列。"
              % (app, zh[:20], anywhere[zh]))
        return anywhere[zh]
    return ""


def _rel(path):
    return os.path.relpath(path, ROOT)


def _walk(app, suffixes):
    for base, dirs, files in os.walk(os.path.join(ROOT, "apps", app)):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(suffixes):
                yield os.path.join(base, name)


def apply_doctype_json(app, path, by_app, anywhere, write, todo, missing):
    src = open(path, encoding="utf-8").read()
    try:
        doc = json.loads(src)
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False

    out = src
    for field in doc.get("fields") or []:
        if not isinstance(field, dict):
            continue
        for key in ("label", "description"):
            value = field.get(key)
            if not isinstance(value, str) or not CJK.search(value):
                continue
            en = OVERRIDES.get((field.get("fieldname"), key)) or _lookup(by_app, anywhere, app, value)
            if not en:
                missing.append("%s  %s.%s = %s" % (_rel(path), field.get("fieldname"), key, value))
                continue
            old = '"%s": %s' % (key, json.dumps(value, ensure_ascii=False))
            new = '"%s": %s' % (key, json.dumps(en, ensure_ascii=False))
            if old not in out:
                missing.append("%s  %s.%s 在文件里找不到原文（%s）" % (_rel(path), field.get("fieldname"), key, value[:26]))
                continue
            out = out.replace(old, new, 1)
            todo.append("%s  %s.%s  %s → %s" % (_rel(path), field.get("fieldname"), key, value[:30], en))

    if out == src:
        return False
    # 改完必须还是合法 JSON，而且**只有文案变了**（防止脚本手滑改坏结构）
    before = json.loads(src)
    after = json.loads(out)
    for doc_ in (before, after):
        for field in doc_.get("fields") or []:
            if isinstance(field, dict):
                field.pop("label", None)
                field.pop("description", None)
    if before != after:
        raise SystemExit("替换后结构变了，停手：%s" % _rel(path))
    if write:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(out)
    return True


# `_("…")` / `__("…")` / `_lt("…")`：只换里面的 msgid，不动引号外的写法。
#
# ⚠️ 文案可能**跨行拼接**成一条（`_("前半，" "后半")`，Python 会把相邻字面量接起来）。
# 只吃第一截的话，替换完剩下后半截，代码能跑但 msgid 变成了一个查不到的怪物
# —— 表现是"这行永远显示中文"。所以这里要把整串读全再换（踩过）。
CALL_HEAD = re.compile(r"(?:^|[^\w.])(?:_|__|_lt|N_)\(\s*")
LITERAL = re.compile(r"""(?P<q>['"])(?P<text>(?:\\.|(?!(?P=q)).)*)(?P=q)""")
BETWEEN = re.compile(r"\s*(?:\+\s*)?")


def _read_literals(src, pos):
    """从 pos 开始读相邻的字符串字面量（中间允许空白、换行、+）。返回 (拼接文本, 结束位置, 段数)。"""
    parts = []
    end = pos
    while True:
        match = LITERAL.match(src, end)
        if not match:
            break
        parts.append(match.group("text"))
        end = match.end()
        probe = BETWEEN.match(src, end).end()
        if LITERAL.match(src, probe):
            end = probe
            continue
        break
    return "".join(parts), end, len(parts)


def _python_quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def apply_wrapped_msgid(app, path, by_app, anywhere, write, todo, missing):
    src = open(path, encoding="utf-8").read()
    out = []
    cursor = 0
    changed = 0
    for head in CALL_HEAD.finditer(src):
        start = head.end()
        if start < cursor:
            continue
        text, end, parts = _read_literals(src, start)
        if not parts or not CJK.search(text):
            continue
        en = _lookup(by_app, anywhere, app, text)
        if not en:
            missing.append("%s  %s" % (_rel(path), text))
            continue
        out.append(src[cursor:start])
        out.append(_python_quote(en))
        cursor = end
        changed += 1
        todo.append("%s  %s → %s" % (_rel(path), text[:34], en))
    if not changed:
        return False
    out.append(src[cursor:])
    if write:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("".join(out))
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只报告，不写")
    parser.add_argument("--app", default="", help="只看某个 app")
    parser.add_argument(
        "--scope",
        choices=("all", "json", "msgid"),
        default="all",
        help="json=只改 DocType 标签；msgid=只改已包在 _()/__() 里的文案",
    )
    args = parser.parse_args()

    by_app, anywhere = read_glossary()
    apps = (args.app,) if args.app else APPS
    unknown = [app for app in apps if app not in APPS]
    if unknown:
        print("不认识的 app：%s" % ", ".join(unknown), file=sys.stderr)
        return 2

    todo = []
    missing = []
    files = 0
    for app in apps:
        if args.scope in ("all", "json"):
            for path in _walk(app, (".json",)):
                if os.sep + "doctype" + os.sep in path:
                    files += apply_doctype_json(app, path, by_app, anywhere, not args.check, todo, missing)
        if args.scope in ("all", "msgid"):
            for path in _walk(app, (".py", ".js")):
                files += apply_wrapped_msgid(app, path, by_app, anywhere, not args.check, todo, missing)

    print("术语表：%d 个 App、%d 条中文→英文" % (len({a for a, _ in by_app}), len(by_app)))
    print("%s %d 个文件、%d 处" % ("将修改" if not args.check else "待修改", files, len(todo)))
    for line in todo[:50]:
        print("  " + line)
    if len(todo) > 50:
        print("  …（还有 %d 处）" % (len(todo) - 50))
    if missing:
        print("")
        print("术语表里没有的 %d 处（要补术语表，或人工判断是不是数据）：" % len(missing))
        for line in missing:
            print("  " + line)
    print("")
    print("RESULT: %s" % ("CHECK-ONLY" if args.check else "APPLIED"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
