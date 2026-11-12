#!/usr/bin/env python3
"""静默默认闸：拦写入与映射代码里新增的「缺了就用默认」（CC 引导式录入第 1 期；覆盖表
``docs/work/2026-11-12-cc-guided-entry-express-coverage.md`` 第 6 节第 3 条）。

起因：Express 必填字段缺了就静默写个默认值（到期日按 0 天、现销一律借现金、新物料单位取众数……），用户看不到也改不了。
原则：每个必填字段只能是「后台自动定 / 引导卡问 / 复核卡可改」之一；允许的默认集中登记在
``express_rules/contracts/required_fields.py`` 的 ``DEFAULT_KEYS``（带依据文档）。

算作「缺了就用默认」：``x.get("键", 默认)``、``x.get("键") or 默认``，且默认是**编出来的值**——非空常量（``1``、``"PCS"``、``True``）
或常量名 / 属性（``DEFAULT_UNIT``、``S.MAIN``）。空值归一（``""``、``0``、``None``、``False``、空表、空字典）不算：
那只是把「没有」统一写法，后面照样按「没有」处理（``str(x.get(k) or "").strip()`` 遍布写入路径，算进来就全是噪声）。
键在 ``DEFAULT_KEYS`` 里的不算。存量按文件、按键计数登记在 ``scripts/express-defaults-baseline.json``：**只许减少不许增加**；
修掉一处后用 ``--update`` 把基线降下来（增加时拒绝）。

    python3 scripts/check-express-defaults.py            # 检查；超出基线就失败
    python3 scripts/check-express-defaults.py --list     # 逐处列出（文件:行 键）
    python3 scripts/check-express-defaults.py --update   # 按当前代码重写基线（只能在减少时用）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "express-defaults-baseline.json"
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.express_rules.contracts import required_fields as rf  # noqa: E402

# 写入路径：服务端映射（载荷怎么来）+ CC 桌面版适配与写入（载荷怎么落成 Express 字段）
SCANNED = (
    "apps/pearnly_core/pearnly_core/express_rules/push/mapper.py",
    "apps/pearnly_core/pearnly_core/express_rules/push/sales_mapper.py",
    "apps/pearnly_core/pearnly_core/express_rules/push/common.py",
    "apps/pearnly_core/pearnly_core/express_rules/push/choices.py",
    "companion/src/companion/purchase_adapter.py",
    "companion/src/companion/sales_adapter.py",
    "companion/src/companion/purchase_lines.py",
    "companion/src/companion/dbf_detail.py",
    "companion/src/companion/stock_units.py",
    "companion/src/companion/stock_purchase.py",
    "companion/src/companion/stock_sale.py",
    *rf.WRITERS,
)


def _get_key(node) -> str | None:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args
            and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        return node.args[0].value
    return None


def _invented(node) -> bool:
    """默认值是不是「编出来的值」（见文件头）。"""
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return bool(node.elts)
    if isinstance(node, ast.Dict):
        return bool(node.keys)
    return isinstance(node, (ast.Name, ast.Attribute))


def findings(path: Path) -> list[tuple[int, str]]:
    """一个文件里的「缺了就用默认」：[(行, 键)]。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        key = _get_key(node)
        if key is not None and len(node.args) > 1 and _invented(node.args[1]):
            out.append((node.lineno, key))
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and len(node.values) >= 2:
            first, last = node.values[0], node.values[-1]
            key = _get_key(first)
            if key is not None and isinstance(last, (ast.Constant, ast.Name, ast.Attribute)) and _invented(last):
                out.append((node.lineno, key))
    return sorted(set(out))


def counts() -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for rel in dict.fromkeys(SCANNED):
        path = ROOT / rel
        if not path.exists():
            continue
        c = Counter(key for _, key in findings(path) if key not in rf.DEFAULT_KEYS)
        if c:
            result[rel] = dict(sorted(c.items()))
    return result


def over(current: dict, baseline: dict) -> list[str]:
    out = []
    for rel, keys in current.items():
        for key, n in keys.items():
            allowed = baseline.get(rel, {}).get(key, 0)
            if n > allowed:
                out.append(f"{rel}: {key!r} 缺了就用默认 {n} 处（基线 {allowed}）")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)
    current = counts()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    if args.list:
        for rel in dict.fromkeys(SCANNED):
            path = ROOT / rel
            if path.exists():
                for line, key in findings(path):
                    if key not in rf.DEFAULT_KEYS:
                        print(f"{rel}:{line} {key}")
        return 0
    problems = over(current, baseline)
    if args.update:
        if problems and BASELINE.exists():  # 第一次建基线（文件还没有）时照当前代码登记
            print("基线只许减少：", *problems, sep="\n  ")
            return 1
        BASELINE.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"基线已更新：{sum(sum(v.values()) for v in current.values())} 处")
        return 0
    if problems:
        print("新增了「缺了就用默认」。Express 必填字段要么后台有依据地定、要么引导卡问、要么复核卡可改；",
              "确有依据的默认登记到 express_rules/contracts/required_fields.py 的 DEFAULT_KEYS（写依据文档）：",
              *problems, sep="\n  ")
        return 1
    stale = [f"{rel}: {key}" for rel, keys in baseline.items() for key, n in keys.items()
             if current.get(rel, {}).get(key, 0) < n]
    if stale:
        print("基线里有已经修掉的，请 --update 把基线降下来：", *stale, sep="\n  ")
        return 1
    print(f"check-express-defaults: OK（存量 {sum(sum(v.values()) for v in current.values())} 处，只许减少）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
