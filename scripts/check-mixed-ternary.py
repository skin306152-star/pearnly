#!/usr/bin/env python3
"""三元里混 `||` / `??` 不加括号的检查（等同 ESLint no-mixed-operators 的这一类）。

`a ? b : c || d` 实际是 `a ? b : (c || d)`，常被当成 `(a ? b : c) || d` 写错（10-29：按台登记扫码，
`String(register ? row.serial_no : row.serial || "")` 在登记模式下变成字符串 "undefined"，扫到的号填不进去）。
这里要求三元「否则」分支里出现 `||` / `??` 时整段加括号，意图写明。

扫自有前端：apps/*/*/public/**/*.js 与 www/**/*.html 里的脚本；跳过照官方抄来的 POS 文件和构建产物。
存量写在 `scripts/mixed-ternary-baseline.tsv`（文件 → 处数，主会话 10-29：存量随各模块补流程用例时逐个改、改一个删一个）：
某个文件超过基线就拦；少于基线也拦，要求把基线一起调低（只许减少）。
用法：python3 scripts/check-mixed-ternary.py [文件 ...]   有问题退出 1（指定文件时不看基线）。
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "mixed-ternary-baseline.tsv"
SKIP = ("/official-pos", "/public/assistant/", "/dist/", "node_modules", ".min.js", ".bundle.")
CLOSE = {")": "(", "]": "[", "}": "{"}


def files():
    for pattern in ("apps/*/*/public/**/*.js", "apps/*/*/www/**/*.html"):
        for path in sorted(ROOT.glob(pattern)):
            text = str(path)
            if not any(part in text for part in SKIP):
                yield path


def _strip(line: str) -> str:
    """去掉字符串、模板字面量与行尾注释的内容（保留长度），只看代码。"""
    out, quote, i = [], "", 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                out.append("  ")
                i += 2
                continue
            if ch == quote:
                quote = ""
                out.append(ch)
            else:
                out.append(" ")
        elif ch in "'\"`":
            quote = ch
            out.append(ch)
        elif line.startswith("//", i):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def problems(line: str) -> list:
    """这一行里「? … :」之后、同一层括号内、到这段表达式结束前出现的 `||` / `??` 的列号。"""
    code = _strip(line)
    found, stack = [], []  # stack: [(depth_char, saw_question, in_alternate)]
    frame = {"q": 0, "alt": False}
    frames = [frame]
    i = 0
    while i < len(code):
        two = code[i:i + 2]
        ch = code[i]
        if two in ("?.", "??"):
            if two == "??" and frames[-1]["alt"]:
                found.append(i + 1)
            i += 2
            continue
        if two == "||":
            if frames[-1]["alt"]:
                found.append(i + 1)
            i += 2
            continue
        if ch == "?":
            frames[-1]["q"] += 1
        elif ch == ":" and frames[-1]["q"]:
            frames[-1]["q"] -= 1
            frames[-1]["alt"] = True
        elif ch in "([{":
            frames.append({"q": 0, "alt": False})
        elif ch in ")]}":
            if len(frames) > 1:
                frames.pop()
        elif ch in ",;":
            frames[-1] = {"q": 0, "alt": False}
        elif code.startswith("=>", i):
            frames[-1] = {"q": 0, "alt": False}
            i += 2
            continue
        i += 1
    return found


def baseline() -> dict:
    out = {}
    for line in BASELINE.read_text(encoding="utf-8").splitlines() if BASELINE.exists() else []:
        if line.strip() and not line.startswith("#"):
            path, count = line.rsplit("\t", 1)
            out[path] = int(count)
    return out


def main(argv):
    targets = [pathlib.Path(arg) for arg in argv] or list(files())
    found = {}
    for path in targets:
        try:
            name = str(path.resolve().relative_to(ROOT))
        except ValueError:  # 仓库外的文件：照原路径报
            name = str(path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for column in problems(line):
                found.setdefault(name, []).append(f"{name}:{number}:{column}: {line.strip()[:160]}")
    allowed = {} if argv else baseline()
    bad = [hit for name, hits in sorted(found.items()) if len(hits) > allowed.get(name, 0) for hit in hits]
    stale = [f"{name}: 基线 {count} 处，现在 {len(found.get(name, []))} 处" for name, count in sorted(allowed.items())
             if len(found.get(name, [])) < count]
    if bad:
        print("三元「否则」分支里的 || / ?? 要加括号写明意图（a ? b : (c || d)，或 (a ? b : c) || d）：")
        print("\n".join(bad))
    if stale:
        print("这些文件比基线少了，把 scripts/mixed-ternary-baseline.tsv 一起调低（只许减少）：")
        print("\n".join(stale))
    if bad or stale:
        return 1
    print(f"三元混用 || / ?? 检查：通过（存量 {sum(allowed.values())} 处）" if allowed else "三元混用 || / ?? 检查：通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
