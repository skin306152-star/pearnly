#!/usr/bin/env python3
"""CC 覆盖总表闸（引导侧）：哪项业务在哪一格做完了、做了一半、没开始、等谁、范围外——只有一份数据
``scripts/impact/cc-coverage.toml``，总表 ``docs/work/2026-11-12-cc-coverage-matrix.md`` 里的那张表由它生成
（总设计 ``docs/work/2026-11-14-cc-guided-master-design.md`` 第 4 节：别再出现两张表）。

检查：

1. 每一格的写法合规（done / half:<期> / todo:<期> / no:<期>:<等谁> / wait:<哪条线> / out:<依据>），范围外必须写依据；
2. 与引导声明（``check-cc-guides.py`` 读到的那一份）对得上：标 done / half 的格，这个产品这个模式必须声明了这项业务，
   done 还要是完整引导（不是只有开场一句的 ``chat``）且声明面向这种账本；声明了的格不许标 out / no；
3. 总表文件里 ``<!-- cc-coverage:begin -->`` 与 ``<!-- cc-coverage:end -->`` 之间的内容与本数据生成的一致。

    python3 scripts/check-cc-coverage.py            # 检查
    python3 scripts/check-cc-coverage.py --counts   # 只报数
    python3 scripts/check-cc-coverage.py --write    # 按数据重写总表里的那一段

只读（--write 只写总表文件那一段）；不连网络。
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "scripts" / "impact" / "cc-coverage.toml"
DOC = ROOT / "docs" / "work" / "2026-11-12-cc-coverage-matrix.md"
BEGIN, END = "<!-- cc-coverage:begin -->", "<!-- cc-coverage:end -->"

_spec = importlib.util.spec_from_file_location("check_cc_guides", ROOT / "scripts" / "check-cc-guides.py")
guides_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guides_gate)

# 组 → (产品 App, 模式, 表头, 三格的格号)
GROUPS = {"cowork_direct": ("pearnly_cowork", "chat", "Cowork 直写", "①·⑤·⑨"),
          "cowork_operator": ("pearnly_cowork", "operator", "Cowork 代录", "②·⑥·⑩"),
          "erp_direct": ("pearnly_erp_portal", "chat", "ERP 直写", "③·⑦·⑪"),
          "erp_operator": ("pearnly_erp_portal", "operator", "ERP 代录", "④·⑧·⑫")}
LEDGERS = ("express_local", "mrerp", "express_cloud")           # 一组三格的顺序
PHASES = ("p0", "p1", "p2", "p3", "p4", "mrerp")
WAITS = {"mrerp": "⇢MR", "cloud": "⇢云", "onboard": "⇢公", "desktop": "⇢桌"}
DONE, HALF, TODO, NO, WAIT, OUT = "done", "half", "todo", "no", "wait", "out"
MARKS = {DONE: "✅", HALF: "◐", TODO: "○", NO: "✗"}


def parse(cell: str) -> Tuple[str, str, str]:
    """一格 → (状态, 期, 等谁 / 依据)；写法不对抛 ValueError。"""
    state, _, rest = str(cell).partition(":")
    if state == DONE and not rest:
        return DONE, "", ""
    if state in (HALF, TODO) and rest in PHASES:
        return state, rest, ""
    if state == NO:
        phase, _, who = rest.partition(":")
        if phase in PHASES and who in WAITS:
            return NO, phase, who
    if state == WAIT and rest in WAITS:
        return WAIT, "", rest
    if state == OUT and rest.strip():
        return OUT, "", rest.strip()
    raise ValueError(f"cell {cell!r} is not written as done / half:<phase> / todo:<phase> / no:<phase>:<who> / wait:<line> / out:<why>")


def load(path: Path = DATA) -> List[Dict[str, Any]]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("row", [])


def check(rows: List[Dict[str, Any]], guides: Dict[str, List[Dict[str, Any]]]) -> List[str]:
    errors: List[str] = []
    seen = set()
    for row in rows:
        name = row.get("id")
        if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9_]*$", name) or name in seen or not row.get("name"):
            errors.append(f"{name!r}: a row needs a unique id and a name")
            continue
        seen.add(name)
        for group, (app, mode, _title, _numbers) in GROUPS.items():
            cells = row.get(group)
            if not isinstance(cells, list) or len(cells) not in (1, 3):
                errors.append(f"{name}.{group}: one cell (ledger does not matter) or three (local, MR.ERP, cloud)")
                continue
            declared = next((g for g in guides.get(app, []) if g.get("id") == row.get("guide") and mode in g.get("modes", ())), None)
            for index, cell in enumerate(cells):
                try:
                    state, _phase, _extra = parse(cell)
                except ValueError as exc:
                    errors.append(f"{name}.{group}: {exc}")
                    continue
                ledger = LEDGERS[index] if len(cells) == 3 else ""
                where = f"{name}.{group}" + (f"[{ledger}]" if ledger else "")
                if state in (DONE, HALF) and declared is None:
                    errors.append(f"{where}: marked {state} but {app} declares no guide {row.get('guide')!r} for {mode}")
                if state == DONE and declared is not None:
                    if declared.get("source") == "chat":
                        errors.append(f"{where}: marked done but the guide only has an opening line (source chat)")
                    if ledger and ledger not in declared.get("ledgers", ()):
                        errors.append(f"{where}: marked done but the guide does not cover {ledger}")
                if state in (OUT, NO) and declared is not None and state == OUT:
                    errors.append(f"{where}: marked out of scope but {app} declares this guide for {mode}")
    return errors


def counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    found = {DONE: 0, HALF: 0, TODO: 0, NO: 0, "wait:mrerp": 0, "wait:cloud": 0, "wait:onboard": 0}
    for row in rows:
        for group in GROUPS:
            for cell in row.get(group) or ():
                state, _phase, extra = parse(cell)
                if state == WAIT:
                    found[f"wait:{extra}"] += 1
                elif state != OUT:
                    found[state] += 1
    found["in_scope"] = sum(found.values())
    return found


def _cell(cell: str) -> str:
    state, phase, extra = parse(cell)
    if state == OUT:
        return f"—（{extra}）"
    if state == WAIT:
        return WAITS[extra]
    return MARKS[state] + (f" {WAITS[extra]}" if state == NO else "") + (f" {phase.upper()}" if phase else "")


def render(rows: List[Dict[str, Any]]) -> str:
    found = counts(rows)
    head = "| # | 业务 | " + " | ".join(f"{title} {numbers}" for _a, _m, title, numbers in GROUPS.values()) + " |"
    lines = [BEGIN,
             "（本段由 `scripts/check-cc-coverage.py --write` 按 `scripts/impact/cc-coverage.toml` 生成，不要手改。）", "",
             "记号：**✅** 引导做完 · **◐** 做了一半 · **○** 范围内、没有引导（现在靠打字能办）· **✗** 范围内、打字也办不了 · "
             "**⇢MR / ⇢云 / ⇢公 / ⇢桌** 等 MR.ERP 写账线 / 云端 Express 实测 / 公司接入 / CC 桌面版 · **—** 范围外（括号里是依据）。"
             "每组三格按 本地 Express · MR.ERP · 云端 Express；只有一格的是与账本无关的业务。P 几是排在哪一期。", "",
             head, "|---|---|" + "---|" * len(GROUPS)]
    for number, row in enumerate(rows, 1):
        lines.append(f"| {number} | {row['name']} | " + " | ".join(" · ".join(_cell(c) for c in row[group]) for group in GROUPS) + " |")
    lines += ["", f"范围内共 **{found['in_scope']} 格**：✅ {found[DONE]}、◐ {found[HALF]}、○ {found[TODO]}、✗ {found[NO]}、"
                  f"等 MR.ERP {found['wait:mrerp']}、等云端 {found['wait:cloud']}、等公司接入 {found['wait:onboard']}。", END]
    return "\n".join(lines)


def doc_section(text: str) -> str | None:
    if BEGIN not in text or END not in text:
        return None
    return text[text.index(BEGIN):text.index(END) + len(END)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--counts", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    rows = load()
    guides, errors = guides_gate.load()
    errors = [*errors, *check(rows, guides)]
    if errors:
        for text in errors:
            print(text, file=sys.stderr)
        print(f"check-cc-coverage: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    wanted = render(rows)
    if args.counts:
        print(wanted.splitlines()[-2])
        return 0
    text = DOC.read_text(encoding="utf-8")
    current = doc_section(text)
    if args.write:
        if current is None:
            raise SystemExit(f"check-cc-coverage: {DOC.name} has no {BEGIN} … {END} section to write into")
        DOC.write_text(text.replace(current, wanted), encoding="utf-8")
        print("check-cc-coverage: table written")
        return 0
    if current != wanted:
        print(f"check-cc-coverage: the table in {DOC.relative_to(ROOT)} differs from scripts/impact/cc-coverage.toml: run --write",
              file=sys.stderr)
        return 1
    found = counts(rows)
    print(f"check-cc-coverage: {len(rows)} businesses, {found['in_scope']} cells in scope, {found[DONE]} done, {found[HALF]} half")
    return 0


if __name__ == "__main__":
    sys.exit(main())
