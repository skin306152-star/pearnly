# -*- coding: utf-8 -*-
"""盯住"写死的用户可见文案"：Frappe 原生这一层（DocType、Desk 脚本、服务端消息、工作区）。

用户 2026-09-19 的要求："不许写死任何文案和语言，包括管理后台租户的语言一样"。
所以需要一台机器来盯着这件事，而不是靠人记得 —— 这就是本脚本。

判据（标准 Frappe 做法 = 英文原文当 msgid + 翻译包）：

  DocType / 工作区 / 报表 JSON 的 label、title、description  ->  写英文
  Python 里给用户看的消息                                   ->  _("English")
  Desk JS 里给用户看的串                                    ->  __("English")

为什么 msgid 必须是英文：_() 找不到译文时**原样返回 msgid**。msgid 写成中文，
就等于"这门语言永远是中文"；英文 msgid 至少有一个说得通的兜底。

它不扫什么（说清楚，免得以为漏了）：

  · 代码注释与文档字符串 —— 写给开发看的，不进界面（用 ast 精确排除，不靠正则猜）
  · pearnly_dms 的 LINE 卡片文案 —— 已经走自己的语义键（dms.card.*），有各自的防漏键守卫
  · Vue 业务前端（frontend/）—— 按 ADR-005/006 是 Vue I18n 那一摊，单独一批做

用法：

    python3 scripts/check-i18n.py                 # 报告：按类型 / 按 app / 按文件 / 去重文案
    python3 scripts/check-i18n.py --app pearnly_control
    python3 scripts/check-i18n.py --list          # 逐条明细（file:line kind text）
    python3 scripts/check-i18n.py --strict        # 有发现就退出码 1（给闸门用）
    python3 scripts/check-i18n.py --missing zh    # 英文 msgid 里缺 zh 译文的
    python3 scripts/check-i18n.py --missing-all   # zh / th / ja 一起看
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APPS_ROOT = os.path.join(ROOT, "apps")

# 我们自己维护的 app。控制面站点上只装 frappe + core + control，
# 产品站点装 frappe + core + 对应产品 —— 所以译文放哪个 app 是有讲究的。
APPS = ("pearnly_core", "pearnly_control", "pearnly_cowork", "pearnly_dms", "pearnly_erp_portal")

# 翻译包要覆盖的语言（租户默认泰语，控制面中文，英文是 msgid 本身不用翻）
LANGS = ("zh", "th", "ja")

CJK = re.compile(r"[\u4e00-\u9fff]")

SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    ".git",
    "public",
    "patches",
    "templates",
    "config",
}

# Python 里"已经把文案交给翻译层"的函数。__ 只在 JS 里有，Python 用 _ / _lt / N_，
# 这里一并认，免得有人写错还查不出来。
TRANSLATE_CALLS = {"_", "__", "_lt", "N_"}

# 这些调用里的中文是**给开发看的**：日志、断言、内部异常。它们不会出现在界面上，
# 所以单独归类（py-internal-*），闸门默认不因为它们拦人 —— 免得为了凑数量把
# "日志也翻译"这种没意义的事做了。
INTERNAL_CALLS = {
    "log_error",
    "logger",
    "exception",
    "warning",
    "warn",
    "debug",
    "info",
    "critical",
    "error",
    "print",
    "RuntimeError",
    "AssertionError",
    "NotImplementedError",
}

# JS 侧同理：console.* 和 throw new Error(...) 不进界面
INTERNAL_JS = ("console.", "new Error(", "new TypeError(")

# JSON 里会渲染到界面上的键。工作区的 text（段落/标题块）、报表与 DocType 字段
# 的 label / options / description 都在内。
JSON_TEXT_KEYS = (
    "label",
    "title",
    "subtitle",
    "description",
    "options",
    "text",
    "header",
    "help",
    "placeholder",
    "button_label",
    "field_label",
    "message",
)


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT)


def _walk_app(app: str, suffixes: tuple):
    base_root = os.path.join(APPS_ROOT, app)
    for base, dirs, files in os.walk(base_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(suffixes):
                yield os.path.join(base, name)


# ── Python ───────────────────────────────────────────────────────────────────


def _call_name(node: ast.AST) -> str:
    func = getattr(node, "func", None)
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def scan_python(path: str) -> list:
    rel = _rel(path)
    try:
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
    except (OSError, SyntaxError) as exc:
        return [{"file": rel, "line": 0, "kind": "scan-error", "text": str(exc)[:70]}]

    docstrings = set()
    wrapped = set()
    wrapped_fstring = set()
    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node

    def enclosing_call_name(node):
        """往上找最近的调用名 —— 用来判断这条串是进日志还是进界面。"""
        cur = parent.get(id(node))
        while cur is not None:
            if isinstance(cur, ast.Call):
                return _call_name(cur)
            cur = parent.get(id(cur))
        return ""

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if not body:
                continue
            head = body[0]
            # 只有"语句体第一条就是字符串字面量"才算 docstring
            if (
                isinstance(head, ast.Expr)
                and isinstance(head.value, ast.Constant)
                and isinstance(head.value.value, str)
            ):
                docstrings.add(id(head.value))
            continue
        if isinstance(node, ast.Call) and _call_name(node) in TRANSLATE_CALLS:
            args = list(node.args) + [kw.value for kw in node.keywords]
            for arg in args:
                target = wrapped_fstring if isinstance(arg, ast.JoinedStr) else wrapped
                for sub in ast.walk(arg):
                    target.add(id(sub))

    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        text = node.value
        if not CJK.search(text) or id(node) in docstrings:
            continue
        if id(node) in wrapped_fstring:
            # f-string 当 msgid：变量一变 msgid 就变，翻译表永远匹配不上。
            kind = "py-fstring-msgid"
        elif id(node) in wrapped:
            kind = "py-msgid-cjk"
        elif enclosing_call_name(node) in INTERNAL_CALLS:
            kind = "py-internal-cjk"
        else:
            kind = "py-bare-cjk"
        findings.append(
            {"file": rel, "line": node.lineno, "kind": kind, "text": text.strip()[:70]}
        )
    return findings


# ── Desk JS ──────────────────────────────────────────────────────────────────

# 正则字面量的判定：/ 前面要是这些"不像操作数"的字符，才可能是正则开始
_REGEX_PREV = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}


def _scan_js_strings(src: str):
    """走一遍 JS，产出 (字符串内容, 行号, 是否在 __() 里)。跳过注释与正则字面量。

    没有可用的 JS 解析器（本机不装 node 包），所以自己走一遍字符。够用的判据：
    注释不进来、正则字面量不进来、模板串整条算一个串。
    """
    out = []
    i, n = 0, len(src)
    line = 1
    prev_significant = "\n"
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i += 2
            continue
        if ch == "/" and prev_significant in _REGEX_PREV:
            # 正则字面量：跳到没转义的收尾 /（字符组里的 / 不算）
            i += 1
            in_class = False
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "\n":
                    line += 1
                if src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                elif src[i] == "/" and not in_class:
                    i += 1
                    break
                i += 1
            prev_significant = "/"
            continue
        if ch in "'\"`":
            start_line = line
            start_at = i
            quote = ch
            i += 1
            buf = []
            while i < n:
                c = src[i]
                if c == "\\":
                    buf.append(src[i : i + 2])
                    if src[i + 1 : i + 2] == "\n":
                        line += 1
                    i += 2
                    continue
                if c == "\n":
                    line += 1
                    if quote != "`":
                        break  # 单/双引号串不该跨行；当它结束了，免得整份文件被吃掉
                    buf.append(c)
                    i += 1
                    continue
                if c == quote:
                    i += 1
                    break
                buf.append(c)
                i += 1
            text = "".join(buf)
            before = src[max(0, start_at - 60) : start_at]
            wrapped = bool(re.search(r"__?\(\s*$", before[-14:]))
            internal = bool(
                re.search(r"console\.\w+\([^()]*$", before)
                or re.search(r"new (?:Error|TypeError)\([^()]*$", before)
            )
            out.append((text, start_line, wrapped, internal))
            prev_significant = quote
            continue
        if not ch.isspace():
            prev_significant = ch
        i += 1
    return out


def scan_js(path: str) -> list:
    rel = _rel(path)
    try:
        src = open(path, encoding="utf-8").read()
    except OSError as exc:
        return [{"file": rel, "line": 0, "kind": "scan-error", "text": str(exc)[:70]}]
    findings = []
    for text, line, wrapped, internal in _scan_js_strings(src):
        if not CJK.search(text):
            continue
        if wrapped:
            kind = "js-msgid-cjk"
        elif internal:
            kind = "js-internal-cjk"
        else:
            kind = "js-bare-cjk"
        findings.append(
            {
                "file": rel,
                "line": line,
                "kind": kind,
                "text": text.strip()[:70],
            }
        )
    return findings


# ── JSON（DocType / 工作区 / 报表 / 仪表盘…）──────────────────────────────────


def _json_line(src: str, key: str, value: str) -> int:
    """在原文里给这个键值找行号（JSON 解析器不给位置）。找不到就返回 0。"""
    needle = '"%s"' % key
    head = value.split("\n")[0][:24]
    at = 0
    while True:
        found = src.find(needle, at)
        if found < 0:
            return 0
        if head and head in src[found : found + 400]:
            return src.count("\n", 0, found) + 1
        at = found + len(needle)


def scan_json(path: str) -> list:
    rel = _rel(path)
    try:
        src = open(path, encoding="utf-8").read()
        doc = json.loads(src)
    except (OSError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []

    # 目录名当"这类 JSON 是什么"，报错时一眼看得出该在哪改
    parts = rel.split(os.sep)
    area = parts[-2] if len(parts) >= 2 else "json"

    findings = []

    def note(key, value, owner=""):
        if not isinstance(value, str) or not CJK.search(value):
            return
        first = value.split("\n")[0][:40]
        findings.append(
            {
                "file": rel,
                "line": _json_line(src, key, value),
                "kind": "json-%s-%s" % (area, key),
                "text": ("%s=%s" % (owner, first)) if owner else first,
            }
        )

    for field in doc.get("fields") or []:
        if not isinstance(field, dict):
            continue
        owner = str(field.get("fieldname") or field.get("label") or "")
        for key in JSON_TEXT_KEYS:
            note(key, field.get(key), owner)

    for key in JSON_TEXT_KEYS:
        note(key, doc.get(key))

    # 工作区的内容块是 json 字符串，单独解一层
    content = doc.get("content")
    if isinstance(content, str) and CJK.search(content):
        try:
            blocks = json.loads(content)
        except ValueError:
            blocks = None
        if isinstance(blocks, list):
            for block in blocks:
                if isinstance(block, dict):
                    note("content", block.get("text") or block.get("label"))
        else:
            findings.append(
                {
                    "file": rel,
                    "line": _json_line(src, "content", content),
                    "kind": "json-%s-content" % area,
                    "text": content.strip()[:70],
                }
            )
    return findings


# ── 翻译包 ───────────────────────────────────────────────────────────────────


def translations_dir(app: str) -> str:
    return os.path.join(APPS_ROOT, app, app, "translations")


def load_translations(app: str, lang: str) -> dict:
    path = os.path.join(translations_dir(app), lang + ".csv")
    if not os.path.exists(path):
        return {}
    table = {}
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) >= 2 and row[0].strip():
                table[row[0]] = row[1]
    return table


def merged_translations(lang: str) -> dict:
    merged = {}
    for app in APPS:
        merged.update(load_translations(app, lang))
    return merged


MSGID_CALL = re.compile(
    r"""(?:^|[^\w.])(?:_|__|_lt|N_)\(\s*(['"])(?P<text>(?:\\.|(?!\1).)*)\1"""
)


def english_msgids() -> dict:
    """我们自己代码里的英文 msgid → 出现在哪些文件。"""
    found = defaultdict(set)
    for app in APPS:
        for path in _walk_app(app, (".py", ".js")):
            try:
                src = open(path, encoding="utf-8").read()
            except OSError:
                continue
            for match in MSGID_CALL.finditer(src):
                text = match.group("text").strip()
                if not text or CJK.search(text):
                    continue
                found[text].add(_rel(path))
        for path in _walk_app(app, (".json",)):
            rel = _rel(path)
            try:
                doc = json.loads(open(path, encoding="utf-8").read())
            except (OSError, ValueError):
                continue
            if not isinstance(doc, dict):
                continue
            for field in doc.get("fields") or []:
                if not isinstance(field, dict):
                    continue
                label = field.get("label")
                if isinstance(label, str) and label.strip() and not CJK.search(label):
                    found[label.strip()].add(rel)
    return found


def report_missing(langs: tuple) -> int:
    msgids = english_msgids()
    problems = 0
    print("== 英文 msgid 的译文覆盖 ==")
    print("  我们自己代码里的英文 msgid 共 %d 条" % len(msgids))
    for lang in langs:
        table = merged_translations(lang)
        missing = sorted(k for k in msgids if not table.get(k))
        problems += len(missing)
        print("  %-4s 缺 %d 条" % (lang, len(missing)))
    for lang in langs:
        table = merged_translations(lang)
        missing = sorted(k for k in msgids if not table.get(k))
        if not missing:
            continue
        print("== %s 缺译文（前 20）==" % lang)
        for key in missing[:20]:
            print("  %-46s %s" % (key[:46], sorted(msgids[key])[0]))
    for app in APPS:
        for lang in langs:
            path = os.path.join(translations_dir(app), lang + ".csv")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8", newline="") as handle:
                keys = [r[0] for r in csv.reader(handle) if r and r[0].strip()]
            dup = [k for k, c in Counter(keys).items() if c > 1]
            if dup:
                problems += len(dup)
                print(
                    "== %s/%s.csv 重复键 %d 个（后写覆盖先写）: %s"
                    % (app, lang, len(dup), ", ".join(dup[:6]))
                )
    print("")
    print("合计问题 %d 条" % problems)
    print(
        "（注意：frappe 自带的词条，例如 Save / Submit / Search，由框架自己的 "
        "locale/*.po 提供，不在我们这几个 App 的 CSV 里 —— 上面列出的缺项要先判断"
        "是不是这一类。）"
    )
    return problems


# ── 主流程 ───────────────────────────────────────────────────────────────────


def collect(apps: tuple) -> list:
    findings = []
    for app in apps:
        for path in _walk_app(app, (".json",)):
            findings += scan_json(path)
        for path in _walk_app(app, (".js",)):
            findings += scan_js(path)
        for path in _walk_app(app, (".py",)):
            findings += scan_python(path)
    return findings


def report(findings: list, apps: tuple, verbose: bool) -> None:
    by_kind = Counter(f["kind"] for f in findings)
    by_app = Counter(f["file"].split(os.sep)[1] if os.sep in f["file"] else "?" for f in findings)
    by_file = Counter(f["file"] for f in findings)
    by_text = defaultdict(list)
    for item in findings:
        by_text[item["text"]].append(item)

    print("== 按类型 ==")
    for kind, count in sorted(by_kind.items(), key=lambda kv: -kv[1]):
        print("  %-24s %d" % (kind, count))
    print("== 按 app ==")
    for app, count in sorted(by_app.items(), key=lambda kv: -kv[1]):
        print("  %-24s %d" % (app, count))
    print("== 按文件（前 25）==")
    for path, count in sorted(by_file.items(), key=lambda kv: -kv[1])[:25]:
        print("  %-58s %d" % (path, count))
    print("== 去重后的文案（前 40，按出现次数）==")
    for text, items in sorted(by_text.items(), key=lambda kv: -len(kv[1]))[:40]:
        head = items[0]
        print(
            "  %-30s x%-3d %-18s %s:%s"
            % (text[:30], len(items), head["kind"], head["file"], head["line"])
        )
    if verbose:
        print("== 明细（全部）==")
        for item in sorted(findings, key=lambda f: (f["file"], f["line"])):
            print(
                "  %-58s:%-5s %-22s %s"
                % (item["file"], item["line"], item["kind"], item["text"])
            )
    print("")
    print(
        "合计发现 %d 条（%d 个文件，去重后 %d 条文案）"
        % (len(findings), len(by_file), len(by_text))
    )
    if apps != APPS:
        print("（只扫了 %s）" % ", ".join(apps))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="有发现就退出码 1")
    parser.add_argument("--app", default="", help="只看某个 app")
    parser.add_argument("--list", action="store_true", help="逐条列出明细")
    parser.add_argument("--missing", default="", help="报告缺译文的语言（如 zh）")
    parser.add_argument("--missing-all", action="store_true", help="zh / th / ja 一起查")
    args = parser.parse_args()

    if args.missing or args.missing_all:
        langs = LANGS if args.missing_all else (args.missing,)
        unknown = [lang for lang in langs if lang not in LANGS]
        if unknown:
            print(
                "不认识的语言：%s（可选 %s）" % (", ".join(unknown), " / ".join(LANGS)),
                file=sys.stderr,
            )
            return 2
        problems = report_missing(langs)
        if args.strict:
            print("RESULT: %s" % ("FAIL" if problems else "PASS"))
            return 1 if problems else 0
        print("RESULT: REPORT-ONLY")
        return 0

    apps = (args.app,) if args.app else APPS
    unknown = [app for app in apps if app not in APPS]
    if unknown:
        print("不认识的 app：%s" % ", ".join(unknown), file=sys.stderr)
        return 2

    findings = collect(apps)
    report(findings, apps, args.list)
    if args.strict and findings:
        print("RESULT: FAIL（--strict）")
        return 1
    print("RESULT: REPORT-ONLY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
