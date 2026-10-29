#!/usr/bin/env python3
"""吞错闸门（主会话 10-28；起因：polymer 手机盘点扫码查到商品却出不来录入框——`stocktake-entry.js` 的 `match()` 把前端异常吞成
「找不到商品」，`errors.js` 一条都没收到，生产 Error Log 是 0）。

规则：产品前端代码（`apps/*/*/public/**`、DocType / Page / Report 的 JS、`frontend/src`、`frontend/assistant/src`）里每一个
`catch`——`try { … } catch (e) { … }` 和 `.catch(handler)`——都必须做到下面之一：
- 重抛（`throw`）；
- 交给统一出错回传：Desk / 站点页面 JS 调 `window.pearnlyCaught(error, "哪里")`，或把处理包成
  `.catch(window.pearnlyReported(原来的处理, "哪里"))`（`pearnly_core/public/desk/errors.js`）；两个前端（`frontend/src`、
  `frontend/assistant/src`）用各自 `lib/caught.ts` 的 `reportCaught` / `withReport`。请求被站点拒收、网络失败那里自己跳过（服务端已记），
  其余前端异常记进 Error Log。

只 `console.error`、只弹提示、空 catch、换成默认值都算吞错，失败。确属预期的（例如探测浏览器能力、用户取消、解析用户自己贴的文本），
逐条登记到 `tests/baseline/swallowed_errors.toml`：文件、所在函数或那一行的特征文字、原因。登记表只许减少；登记了却找不到的条目也算失败（删掉）。

    python3 scripts/check-swallowed-errors.py            # 检查（CI）
    python3 scripts/check-swallowed-errors.py --list     # 列出全部 catch 与归类
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "tests" / "baseline" / "swallowed_errors.toml"
PATTERNS = ("apps/*/*/public/**/*.js", "apps/*/*/**/doctype/**/*.js", "apps/*/*/**/page/**/*.js", "apps/*/*/**/report/**/*.js",
            "frontend/src/**/*.ts", "frontend/src/**/*.vue", "frontend/assistant/src/**/*.ts", "frontend/assistant/src/**/*.tsx")
SKIP = ("/dist/", "node_modules", "official-pos", "/vendor/", "/pearnly_control/", "/public/assistant/", ".d.ts", "/test", ".test.", ".spec.")
REPORT = re.compile(r"\bpearnly(ReportError|Caught|Reported)\b|\breportCaught\s*\(|\bwithReport\s*\(")
RETHROW = re.compile(r"\bthrow\b|\breject\s*\(")


def files():
    seen = set()
    for pattern in PATTERNS:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            if rel in seen or any(s in rel for s in SKIP) or not path.is_file():
                continue
            seen.add(rel)
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def mask(text: str) -> str:
    """把字符串、模板串、注释、正则字面量换成空格（保留长度与换行），后面的配对与匹配只看代码。"""
    out, i, n = list(text), 0, len(text)
    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j); i = j; continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j); i = j; continue
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif c == "`" and text[j] == "$" and j + 1 < n and text[j + 1] == "{":
                    depth, j = 1, j + 2
                    while j < n and depth:
                        depth += {"{": 1, "}": -1}.get(text[j], 0)
                        j += 1
                    continue
                j += 1
            blank(i + 1, min(j, n)); i = j + 1; continue
        i += 1
    return "".join(out)


def match_close(code: str, start: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    for k in range(start, len(code)):
        if code[k] == open_ch:
            depth += 1
        elif code[k] == close_ch:
            depth -= 1
            if depth == 0:
                return k
    return len(code) - 1


def try_body(code: str, text: str, at: int) -> str:
    """`catch` 前面那个 try 块的原文（不是 try 就回空）。"""
    close = code.rfind("}", 0, at)
    depth, k = 0, close
    while k >= 0:
        depth += {"}": 1, "{": -1}.get(code[k], 0)
        if depth == 0:
            break
        k -= 1
    return text[k:close] if close >= 0 and k >= 0 and re.search(r"\btry\s*$", code[max(0, k - 20):k]) else ""


def catches(text: str):
    """每个 catch：(行号, 形式, 处理代码原文, 上下文 = try 块 + 处理代码)。"""
    code = mask(text)
    for m in re.finditer(r"(\.\s*catch\s*\()|(\bcatch\b\s*(\([^)]*\))?\s*\{)", code):
        line = code.count("\n", 0, m.start()) + 1
        if m.group(1):
            start = m.end() - 1
            end = match_close(code, start, "(", ")")
            handler = text[start + 1:end]
            # 上下文带上前两行：同一个文件里一模一样的 `.catch(() => null)` 行常有好几处，单看这一行认不出是哪个
            near = "\n".join(text.splitlines()[max(0, line - 3):line])
            yield line, ".catch()", handler, near + " " + handler
        else:
            start = m.end() - 1
            end = match_close(code, start, "{", "}")
            handler = text[start + 1:end]
            yield line, "try/catch", handler, try_body(code, text, m.start()) + " " + text.splitlines()[line - 1] + " " + handler


def classify(handler: str) -> str:
    body = handler.strip()
    if RETHROW.search(mask(handler)):
        return "rethrow"
    if REPORT.search(handler):
        return "report"
    if not body or re.fullmatch(r"(\(\s*\)|_|\w+)\s*=>\s*(\{\s*\}|null|undefined|void 0|false|true|\[\]|\{\}|\(\s*\)|\"\"|''|0)", body) \
            or re.fullmatch(r"(\(\s*\)|_|\w+)\s*=>\s*\{\s*\}", body):
        return "empty"
    if re.fullmatch(r"(console\.(error|warn|log|debug))", body) or (
            re.search(r"\bconsole\.(error|warn|log|debug)\b", body) and not re.search(r"\b(msgprint|show_alert|fail|alert|toast|throw)\b", body)
            and len(re.sub(r"console\.\w+\([^)]*\);?", "", body).strip(" ;{}\n=>()e_")) < 3):
        return "console"
    if re.search(r"\b(msgprint|show_alert|p\.fail|p\.error|toast|frappe\.throw|alert)\b", body):
        return "ui"
    return "other"


def scan():
    for rel, text in files():
        lines = text.splitlines()
        for line, form, handler, context in catches(text):
            yield {"file": rel, "line": line, "form": form, "kind": classify(handler),
                   "text": lines[line - 1].strip()[:140], "handler": " ".join(handler.split())[:160],
                   "context": " ".join(context.split())}


def registry():
    if not REGISTRY.exists():
        return []
    return tomllib.loads(REGISTRY.read_text(encoding="utf-8")).get("allow", [])


def hits(entry, items):
    match = " ".join(entry["match"].split())
    return [i for i in items if i["file"] == entry["file"] and match in i["context"]]


def registered(item, allow, items):
    return any(item in hits(entry, items) for entry in allow)


def main(argv) -> int:
    items = list(scan())
    if "--list" in argv:
        for item in items:
            print(f'{item["kind"]:<8} {item["form"]:<10} {item["file"]}:{item["line"]}  {item["text"]}')
        counts = {}
        for item in items:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        print(counts)
        return 0
    allow = registry()
    bad = [i for i in items if i["kind"] not in ("rethrow", "report") and not registered(i, allow, items)]
    unused = [e for e in allow if not hits(e, items)]
    # 一条登记只许对上一个 catch：写成「} catch {」这种会把同文件里以后新加的吞错也放过去
    loose = [e for e in allow if len(hits(e, items)) > 1]
    missing = [e for e in allow if not str(e.get("reason", "")).strip()]
    for item in bad:
        print(f'{item["file"]}:{item["line"]}: {item["form"]} 吞了错误（{item["kind"]}）：重抛，或调 window.pearnlyCaught / '
              f'pearnlyReported（前端用 reportCaught / withReport）；确属预期的登记到 {REGISTRY.relative_to(ROOT)}')
    for entry in unused:
        print(f'{REGISTRY.relative_to(ROOT)}: 登记的 {entry["file"]} 「{entry["match"]}」找不到了，删掉这一条')
    for entry in loose:
        print(f'{REGISTRY.relative_to(ROOT)}: {entry["file"]} 「{entry["match"]}」对上了 {len(hits(entry, items))} 个 catch，写得更具体些')
    for entry in missing:
        print(f'{REGISTRY.relative_to(ROOT)}: {entry["file"]} 「{entry["match"]}」没写原因')
    total = len(items)
    if bad or unused or missing or loose:
        print(f"吞错闸门：不通过（{len(bad)} 处吞错，{len(unused)} 条失效登记，{len(loose)} 条登记不唯一，{len(missing)} 条缺原因；共 {total} 个 catch）")
        return 1
    print(f"吞错闸门：通过（{total} 个 catch，{len(allow)} 条登记的预期吞错）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
