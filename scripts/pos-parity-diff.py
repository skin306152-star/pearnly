# -*- coding: utf-8 -*-
"""把官方与我们收银台的同一份取样（`pos-parity-probe.js` 的输出）逐项对照。

用法：

    python3 scripts/pos-parity-diff.py 官方.json 我们.json            # 全部差异
    python3 scripts/pos-parity-diff.py 官方.json 我们.json --only discount_box,checkout_btn
    python3 scripts/pos-parity-diff.py 官方.json 我们.json --px 2      # 尺寸容差 2px（默认 1）

怎么读：

· `缺失` = 官方有、我们没有（或反过来）—— 这本身就是最该先看的差异；
· 颜色按 `rgb()` 三通道比，原样打印两边值（方便直接抄）；
· 尺寸带容差（默认 1px），字号/字重/边框这类离散值必须一致。

退出码：有差异 1，没有 0（可以进闸门）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

NUMERIC = re.compile(r"^-?\d+(\.\d+)?px$")


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def same(a, b, px: float) -> bool:
    if a == b:
        return True
    if isinstance(a, str) and isinstance(b, str) and NUMERIC.match(a) and NUMERIC.match(b):
        return abs(float(a[:-2]) - float(b[:-2])) <= px
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("official")
    parser.add_argument("ours")
    parser.add_argument("--px", type=float, default=1.0, help="尺寸容差（px）")
    parser.add_argument("--only", default="", help="只看这些元素名，逗号分隔")
    args = parser.parse_args()

    official = load(args.official)
    ours = load(args.ours)
    only = {x.strip() for x in args.only.split(",") if x.strip()}

    names = [n for n in official if not only or n in only]
    names += [n for n in ours if n not in official and (not only or n in only)]

    rows = []
    for name in names:
        a = official.get(name)
        b = ours.get(name)
        if a is None and b is None:
            continue
        if a is None or b is None:
            rows.append((name, "-", "缺失" if a is None else "—", "缺失" if b is None else "—", "只有一边有"))
            continue
        if a.get("visible") != b.get("visible"):
            rows.append((name, "visible", a.get("visible"), b.get("visible"), "一边看不见"))
        keys = sorted(set(a) | set(b))
        for key in keys:
            if key in ("box", "text", "visible"):
                continue
            va, vb = a.get(key), b.get(key)
            if not same(va, vb, args.px):
                rows.append((name, key, va, vb, "样式不同"))
        for key in ("w", "h"):
            va, vb = (a.get("box") or {}).get(key), (b.get("box") or {}).get(key)
            if va is None or vb is None:
                continue
            if abs(va - vb) > max(args.px, 2):
                rows.append((name, "box." + key, f"{va}px", f"{vb}px", "尺寸不同"))

    if not rows:
        print("两边一致（在容差内）：%d 个元素" % len(names))
        return 0

    width = max(len(r[0]) for r in rows)
    print("== 官方 vs 我们：收银台差异（%d 处）==" % len(rows))
    print(f"{'元素'.ljust(width)}  属性              官方               我们               说明")
    for name, key, va, vb, why in rows:
        print(f"{name.ljust(width)}  {str(key).ljust(16)}  {str(va)[:18].ljust(18)}  {str(vb)[:18].ljust(18)}  {why}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
