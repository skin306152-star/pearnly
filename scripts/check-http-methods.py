#!/usr/bin/env python3
"""前端调白名单接口的请求方式与接口声明的不符（POST 调只收 GET 的、GET 调只收 POST 的）的检查。

框架按 `@frappe.whitelist(methods=[...])` 拦不符的请求，回 403，Desk 的 `frappe.call` / `frappe.xcall` 随即弹「没有权限」
（11-08 上线验收：录入「新建录入」点 Express「连接」，弹窗每 3 秒用 `frappe.xcall`（默认 POST）问一次只收 GET 的
`pearnly_cowork.companion.api.online`，就每 3 秒弹一次「没有权限」，电脑在不在线也一直读不到）。

做法：先从 apps/**/*.py 读出只收 GET、只收 POST 的白名单接口（点路径）；再扫自有前端（apps/*/*/public/**/*.js 与
frontend/*/src/**/*.ts(x)）里的调用，按调用写法定请求方式，把第一个参数解析成点路径（字面量、`常量 + "名字"`、
`对象.键 + "名字"`，常量在同一文件里定义），对上了就比方式：
  - POST：`frappe.xcall(m, a)`（不给第三个参数或给 "POST"）、`frappe.call({method: m, …})`（没写 type: "GET"）、
    `p.call(m, a, "POST")`、`postMethod(m)`、`uploadMethod(m)`、`site.post(m)`、助手页 `post(m)` / `upload(m)`（BASE 前缀）；
  - GET：`p.call(m[, a])`、`frappe.xcall(m, a, "GET")`、`frappe.call({method: m, type: "GET"})`、`getMethod(m)`、
    `site.get(m)`、`getJson(m)`、助手页 `get(m)`（BASE 前缀）。
解析不出点路径的（运行时拼的前缀）不判。用法：python3 scripts/check-http-methods.py   有不符退出 1。
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = ("/public/assistant/", "/public/pearnly-app/", "/dist/", "node_modules", ".min.js", ".bundle.", "/official-pos")
WHITELIST = re.compile(r"@frappe\.whitelist\(([^)]*)\)\s*\n(?:\s*@[^\n]*\n)*\s*def (\w+)")
VERBS = re.compile(r"""["'](GET|POST|PUT|DELETE)["']""")
ASSISTANT_BASE = "pearnly_core.assistant.api."


def declared(root=ROOT) -> dict:
    """点路径 → 只收的方式（"GET" / "POST"）。不限方式或几种都收的不登记。"""
    out = {}
    for path in sorted(root.glob("apps/*/*/**/*.py")):
        text = str(path)
        if "/tests/" in text or path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "frappe.whitelist" not in source:
            continue
        module = ".".join(path.relative_to(root / "apps").with_suffix("").parts[1:])
        for match in WHITELIST.finditer(source):
            arguments = match.group(1)
            if "methods" not in arguments:
                continue
            verbs = set(VERBS.findall(arguments.split("methods", 1)[1]))
            if len(verbs) == 1:
                out[f"{module}.{match.group(2)}"] = verbs.pop()
    return out


def files(root=ROOT):
    for pattern in ("apps/*/*/public/**/*.js", "frontend/*/src/**/*.ts", "frontend/*/src/**/*.tsx"):
        for path in sorted(root.glob(pattern)):
            text = str(path)
            if not any(part in text for part in SKIP) and not text.endswith(".d.ts"):
                yield path


STRING = r"""(?:"([^"\\\n]*)"|'([^'\\\n]*)'|`([^`$\\\n]*)`)"""
CONST = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*[\w<>\[\]| ]+)?=\s*" + STRING)
OBJECT = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*\{([^{}]*)\}", re.S)
KEY = re.compile(r"([A-Za-z_$][\w$]*)\s*:\s*" + STRING)


def constants(source: str) -> dict:
    """同一文件里写死的字符串常量：`const A = "x."`、`const API = {companion: "x."}` → {"A": …, "API.companion": …}。"""
    out = {}
    for match in CONST.finditer(source):
        out[match.group(1)] = next(group for group in match.groups()[1:] if group is not None)
    for match in OBJECT.finditer(source):
        for key in KEY.finditer(match.group(2)):
            out[f"{match.group(1)}.{key.group(1)}"] = next(group for group in key.groups()[1:] if group is not None)
    return out


def _literal(text: str):
    match = re.fullmatch(STRING, text.strip())
    return None if match is None else next(group for group in match.groups() if group is not None)


def resolve(expression: str, known: dict):
    """调用的第一个参数 → 点路径（解析不出回 None）。模板字面量 `${API}card` 按 `API + "card"` 解析。"""
    template = re.fullmatch(r"`((?:[^`\\]|\\.)*)`", expression.strip())
    if template and "${" in template.group(1):
        pieces = re.split(r"\$\{([^}]*)\}", template.group(1))
        expression = " + ".join(f'"{piece}"' if index % 2 == 0 else piece for index, piece in enumerate(pieces) if piece)
    parts = [part.strip() for part in _split_plus(expression)]
    out = []
    for part in parts:
        literal = _literal(part)
        if literal is not None:
            out.append(literal)
        elif part in known:
            out.append(known[part])
        else:
            return None
    method = "".join(out)
    return method if re.fullmatch(r"[a-z_][\w]*(\.[\w]+)+", method) else None


def _split_plus(expression: str) -> list:
    parts, depth, quote, start = [], 0, "", 0
    for index, char in enumerate(expression):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "+" and depth == 0:
            parts.append(expression[start:index])
            start = index + 1
    parts.append(expression[start:])
    return parts


def _arguments(source: str, open_at: int) -> list:
    """从左括号起，按顶层逗号切出各个实参（到配对的右括号为止）。"""
    out, depth, quote, start, index = [], 0, "", open_at + 1, open_at
    while index < len(source):
        char = source[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                out.append(source[start:index])
                return [part.strip() for part in out if part.strip()]
        elif char == "," and depth == 1:
            out.append(source[start:index])
            start = index + 1
        index += 1
    return []


# 调用名 → (固定方式或 None, 方法实参的位置, 前缀)
CALLS = {
    "frappe.xcall": None, "frappe.call": None, "p.call": None,
    "postMethod": "POST", "uploadMethod": "POST", "site.post": "POST",
    "getMethod": "GET", "site.get": "GET", "getJson": "GET",
}
ASSISTANT_CALLS = {"post": "POST", "upload": "POST", "get": "GET"}
CALL = re.compile(r"(?<![\w$.])(frappe\.xcall|frappe\.call|p\.call|postMethod|uploadMethod|site\.post|site\.get|getJson|getMethod"
                  r"|post|upload|get)\s*(?:<[^()<>]*(?:<[^()<>]*>[^()<>]*)*>)?\s*\(")


def calls(source: str):
    """(方法表达式, 方式, 行号)。"""
    assistant_lib = 'from "@/lib/frappe"' in source or "from './frappe'" in source or 'from "./frappe"' in source
    for match in CALL.finditer(source):
        name = match.group(1)
        if name in ASSISTANT_CALLS and not (assistant_lib and re.search(r"import\s*\{[^}]*\b" + name + r"\b[^}]*\}\s*from\s*[\"'](@/lib/frappe|\./frappe)[\"']", source)):
            continue
        args = _arguments(source, match.end() - 1)
        if not args:
            continue
        line = source.count("\n", 0, match.start()) + 1
        if name == "frappe.call" and args[0].startswith("{"):
            body = args[0]
            method = re.search(r"\bmethod\s*:\s*([^,\n}]+)", body)
            if not method:
                continue
            verb = "GET" if re.search(r"""\btype\s*:\s*["']GET["']""", body) else "POST"
            yield method.group(1).strip(), verb, line
        elif name == "frappe.call":
            yield args[0], "POST", line
        elif name == "frappe.xcall":
            third = _literal(args[2]) if len(args) > 2 else None
            yield args[0], (third or "POST").upper(), line
        elif name == "p.call":
            third = _literal(args[2]) if len(args) > 2 else None
            yield args[0], (third or "GET").upper(), line
        elif name in ASSISTANT_CALLS:
            yield f'"{ASSISTANT_BASE}" + {args[0]}', ASSISTANT_CALLS[name], line
        else:
            yield args[0], CALLS[name], line


def problems(source: str, rules: dict) -> list:
    known = constants(source)
    out = []
    for expression, verb, line in calls(source):
        method = resolve(expression, known)
        wanted = rules.get(method) if method else None
        if wanted and wanted != verb:
            out.append((line, method, verb, wanted))
    return out


def main(argv=None) -> int:
    rules = declared()
    found = []
    for path in files():
        for line, method, verb, wanted in problems(path.read_text(encoding="utf-8", errors="ignore"), rules):
            found.append(f"{path.relative_to(ROOT)}:{line}: 用 {verb} 调 {method}，接口只收 {wanted}（框架回 403，页面弹「没有权限」）")
    if found:
        print("\n".join(found))
        print(f"请求方式与接口声明不符 {len(found)} 处", file=sys.stderr)
        return 1
    print(f"前端调白名单接口的请求方式与声明一致（{len(rules)} 个限定了方式的接口）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
