#!/usr/bin/env python3
"""页面入口基线（回归方案 C 节）：把页面快照（`scripts/smoke/inventory.py` 的 JSONL）归成「每个产品 × 岗位 × 地址：打不打得开、
页头出现哪些声明码」，写进 `tests/baseline/page-entries/<产品>.json`，之后的快照与它逐格比对。

归法：
- 地址：`/desk/<页>`，具体记录（第三段起）归成 `<记录>`；
- 状态：open（露出来的是这一页）/ denied（原生无权限页）/ home（被换回本岗位首页）/ none（没打开、页面没换）；
- 页头：页头按钮、可见菜单项、下拉里带 `data-pearnly-action` 的声明码（三种界面语言共用一份），没码的记文字并标出；
- 全局：每个产品出现过的快捷键。

比对规矩（主会话 10-26：有差异就不能合入，唯一过法是同一提交里改基线并在任务记录写原因）：
- 失败：地址状态变了；页头出现基线没有的码；快照里出现基线没有的地址；快捷键多了。
- 只报告：基线里有、这次没出现的码（页头随数据状态变，如收银台开没开班）；这次没走到的地址。
基线里一页的码是多次快照的并集（`--write --merge`），所以数据状态不同的几次快照都要并进来。

    python3 scripts/regression/baseline.py --write [--merge] <快照.jsonl>…     # 写 / 并入基线
    python3 scripts/regression/baseline.py --compare <快照.jsonl>…            # 比对，有失败退出 1
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "tests" / "baseline" / "page-entries"
HEAD_KINDS = ("head_buttons", "head_menu", "head_dropdowns")


def _rows(paths):
    for path in paths:
        for line in open(path, encoding="utf-8", errors="replace"):
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 并行写坏的行：快照工具已知问题，跳过
            if row.get("product") and row.get("role"):
                yield row


def key_of(path: str) -> str:
    parts = [p for p in path.split("?")[0].split("/") if p]
    if len(parts) > 2:
        return "/".join(parts[:2]) + "/<记录>"
    return "/".join(parts)


def state_of(row) -> str:
    inv = row.get("inventory") or {}
    if row.get("not_rendered") or not inv:
        return "none"
    if inv.get("not_permitted"):
        return "denied"
    final = key_of(row.get("final") or row["path"])
    return "open" if final == key_of(row["path"]) else "home"


def codes_of(inv) -> set[str]:
    out = set()
    for kind in HEAD_KINDS:
        for item in inv.get(kind) or []:
            if kind == "head_menu" and not item.get("visible"):
                continue
            out.add(item.get("action") or f"text:{item.get('text')}")
    return out


def normalize(paths) -> dict:
    data = defaultdict(lambda: {"pages": defaultdict(dict), "shortcuts": set()})
    for row in _rows(paths):
        product, role = row["product"], row["role"]
        inv = row.get("inventory") or {}
        data[product]["shortcuts"].update(s.split(" · ")[0] for s in inv.get("shortcuts") or [])
        key = key_of(row["path"])
        if key.startswith("desk/user/") and row.get("account") and key == f"desk/user/<记录>":
            key = "desk/user/<本人>" if row["path"].rstrip("/").endswith(row["account"]) else key
        cell = data[product]["pages"][role].setdefault(key, {"state": state_of(row), "codes": set()})
        state = state_of(row)
        # 同一类地址走了两个实例（两张单）：任一实例打开就算打开
        if cell["state"] != state and "open" in (cell["state"], state):
            cell["state"] = "open"
        if state == "open":
            cell["codes"] |= codes_of(inv)
    return data


def _to_json(entry) -> dict:
    return {"shortcuts": sorted(entry["shortcuts"]),
            "pages": {role: {key: {"state": cell["state"], "codes": sorted(cell["codes"])} for key, cell in sorted(pages.items())}
                      for role, pages in sorted(entry["pages"].items())}}


def _shown(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def write(paths, merge=False) -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    for product, entry in normalize(paths).items():
        out = _to_json(entry)
        target = BASE / f"{product}.json"
        if merge and target.exists():
            old = json.loads(target.read_text(encoding="utf-8"))
            out["shortcuts"] = sorted(set(out["shortcuts"]) | set(old.get("shortcuts", [])))
            for role, pages in old.get("pages", {}).items():
                for key, cell in pages.items():
                    mine = out["pages"].setdefault(role, {}).setdefault(key, cell)
                    if mine is not cell:
                        if mine["state"] != cell["state"]:
                            print(f"{product} {role} {key}: 状态 {cell['state']} → {mine['state']}（并入时以这次为准）")
                        mine["codes"] = sorted(set(mine["codes"]) | set(cell["codes"]))
        target.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"写入 {_shown(target)}：{sum(len(p) for p in out['pages'].values())} 格")
    return 0


def compare(paths) -> int:
    failures, notes = [], []
    for product, entry in normalize(paths).items():
        target = BASE / f"{product}.json"
        if not target.exists():
            failures.append(f"{product}: 没有基线文件 {_shown(target)}")
            continue
        base = json.loads(target.read_text(encoding="utf-8"))
        extra = set(entry["shortcuts"]) - set(base.get("shortcuts", []))
        if extra:
            failures.append(f"{product}: 多出快捷键 {sorted(extra)}")
        for role, pages in entry["pages"].items():
            known = base.get("pages", {}).get(role)
            if known is None:
                failures.append(f"{product} {role}: 基线里没有这个岗位")
                continue
            for key, cell in pages.items():
                was = known.get(key)
                if was is None:
                    failures.append(f"{product} {role} {key}: 基线里没有这个地址（这次 {cell['state']}）")
                    continue
                if was["state"] != cell["state"]:
                    failures.append(f"{product} {role} {key}: {was['state']} → {cell['state']}")
                added = cell["codes"] - set(was["codes"])
                if added:
                    failures.append(f"{product} {role} {key}: 页头多出 {sorted(added)}")
                missing = set(was["codes"]) - cell["codes"] if cell["state"] == "open" else set()
                if missing:
                    notes.append(f"{product} {role} {key}: 这次没出现 {sorted(missing)}（可能是数据状态）")
            unseen = set(known) - set(pages)
            if unseen:
                notes.append(f"{product} {role}: 这次没走到 {len(unseen)} 个地址")
    for line in notes:
        print("说明：" + line)
    for line in failures:
        print("不通过：" + line)
    print(f"页面入口基线比对：{'不通过' if failures else '通过'}（{len(failures)} 处差异，{len(notes)} 条说明）")
    return 1 if failures else 0


def main(argv) -> int:
    if not argv or argv[0] not in ("--write", "--compare"):
        print(__doc__)
        return 2
    if argv[0] == "--write":
        merge = "--merge" in argv
        return write([a for a in argv[1:] if a != "--merge"], merge=merge)
    return compare(argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
