#!/usr/bin/env python3
"""原生 SQL 里用数据库时钟写时间的闸门（11-17：Cowork 对账缓存用 NOW(6)、DMS 快照用 UTC_TIMESTAMP(6) 写 creation / modified，
数据库时区与站点不同时比框架写的差几个小时，流程收尾按站点时钟查不到这些行）。

产品代码（`apps/` 下的 Python，不含测试）里字符串中出现 NOW( / UTC_TIMESTAMP / CURRENT_TIMESTAMP / SYSDATE( / CURDATE( /
UTC_DATE / CURRENT_DATE 一律失败：时间按框架口径从 Python 传参（`frappe.utils.now()`，站点时区）。文档字符串不算。
只读、不连网络。

    python3 scripts/check-sql-clock.py
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 本仓 SQL 一律大写：函数名与关键字都只认大写，说明文字里的英文 now ( 不算
CLOCK = re.compile(r"\b(NOW\s*\(|UTC_TIMESTAMP|CURRENT_TIMESTAMP|SYSDATE\s*\(|CURDATE\s*\(|UTC_DATE|CURRENT_DATE)")


def _docstrings(tree) -> set:
    found = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            found.add(id(body[0].value))
    return found


def hits(path: Path, text: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    skip = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            match = CLOCK.search(node.value)
            # 只认像 SQL 的串（带关键字），Python 里 `datetime.now(` 之类的说明文字不在字符串里
            if match and re.search(r"\b(SELECT|INSERT|UPDATE|DELETE|VALUES|SET|WHERE)\b", node.value):
                out.append((node.lineno, match.group(1)))
    return out


def product_files() -> list[Path]:
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "apps"], capture_output=True, text=True, check=True).stdout.split()
    return [ROOT / name for name in listed if name.endswith(".py") and "/tests/" not in name
            and not Path(name).name.startswith("test_")]


def main() -> int:
    found = []
    for path in product_files():
        for line, word in hits(path, path.read_text(encoding="utf-8", errors="replace")):
            found.append(f"{path.relative_to(ROOT)}:{line}  SQL 用了数据库时钟 {word}——时间从 Python 传参（frappe.utils.now()，站点时区）")
    for row in found:
        print(row)
    if found:
        return 1
    print("SQL 时钟闸门：通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
