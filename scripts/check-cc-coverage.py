#!/usr/bin/env python3
"""CC 覆盖总表闸（引导侧）：哪项业务在哪一格做完了、做了一半、没开始、等谁、范围外——只有一份数据
``scripts/impact/cc-coverage.toml``，总表 ``docs/work/2026-11-12-cc-coverage-matrix.md`` 里的那张表与「按格依据」由它生成
（总设计 ``docs/work/2026-11-14-cc-guided-master-design.md`` 第 4 节：别再出现两张表）。

11-22 起总表跟着证据走（用户问「为什么总表没即时更新」：11-15 之后接线、页面卡片、按账本过滤都进了 main，表一直没动）。检查：

1. 写法：每格 done / half:<期> / todo:<期> / no:<期>:<等谁> / wait:<哪条线> / out:<依据>，范围外必须写依据；
2. 声明 → 总表（往上拦）：产品声明了一项业务（``check-cc-guides.py`` 读到的那一份）的每个模式、每种账本，总表那一格必须有这项业务的行，
   且至少标 half；那条账本线（``[line]``：MR.ERP 写账开关、云端 Express 来源）关着时标 wait。声明面向的账本线开着却还标 wait 也拦；
3. 总表 → 声明（往下拦）：标 done / half 的格必须有声明；done 还要是完整引导（不是只有开场一句的 ``chat``，有确定与结果，写账的有复核）、
   声明面向这种账本、账本线开着，并且登记了至少一条**页面流程**（``page_flows``：真页面点卡，题 → 复核 → 确定 → 结果）；
   完整引导登记了页面流程却还标 half → 拦（应标 done）；
4. 依据登记（``[[row.link]]``）：引用的流程在 ``tests/flows/registry.toml``、验收场景在 ``tests/acceptance/registry.toml``、真机记录
   （``真机记录:<行 id>``，``docs/work/`` 下固定格式、结果「通过」）存在、文档摘句（``docs/work/<文件>::<原文片段>``）在那个文件里；
   每条引导相关的流程（id 或模块带 guide，或覆盖引导文件）、行 id 带 guide 的真机记录、断言了引导地图行的验收场景都要登记到某一格，
   或在 ``[unlinked]`` 写明为什么不对应某一格；
5. 同提交（``--base``，默认 origin/main 的分叉点）：相对分叉点，引导声明（业务、模式、账本、来源、是否有复核 / 确定 / 结果）或引导相关
   流程的集合变了，``cc-coverage.toml`` 却没改 → 拦（「动了引导的分支必须同提交改总表」，verification.md「CC 覆盖总表」）；
6. 总表文件里 ``<!-- cc-coverage:begin -->`` 与 ``<!-- cc-coverage:end -->`` 之间的内容与本数据生成的一致。

证据四档（给用户报数）：真机通过（真机记录，或测试机 / 线上测试租户 / 真手机上跑过的验收场景）/ 真模型通过（真模型记录或本机真模型场景）/
只有服务端（声明与服务端、假模型页面流程，没有真模型、真机）/ 没做（范围内没有引导）；等 MR.ERP、等云端、等公司接入、范围外另列。

    python3 scripts/check-cc-coverage.py            # 检查
    python3 scripts/check-cc-coverage.py --counts   # 只报数
    python3 scripts/check-cc-coverage.py --write    # 按数据重写总表里的那一段

只读（--write 只写总表文件那一段）；不连网络。
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import importlib.util
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "scripts" / "impact" / "cc-coverage.toml"
DATA_REL = "scripts/impact/cc-coverage.toml"
DOC = ROOT / "docs" / "work" / "2026-11-12-cc-coverage-matrix.md"
FLOWS = ROOT / "tests" / "flows" / "registry.toml"
SCENARIOS = ROOT / "tests" / "acceptance" / "registry.toml"
PASSES = ROOT / "tests" / "acceptance" / "evidence.toml"
BEGIN, END = "<!-- cc-coverage:begin -->", "<!-- cc-coverage:end -->"

_spec = importlib.util.spec_from_file_location("check_cc_guides", ROOT / "scripts" / "check-cc-guides.py")
guides_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guides_gate)
_spec = importlib.util.spec_from_file_location("check_acceptance", ROOT / "scripts" / "check-acceptance.py")
acceptance_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(acceptance_gate)
_spec = importlib.util.spec_from_file_location("check_product_map", ROOT / "scripts" / "check-product-map.py")
product_map_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(product_map_gate)

# 组 → (产品 App, 模式, 表头, 三格的格号)
GROUPS = {"cowork_direct": ("pearnly_cowork", "chat", "Cowork 直写", "①·⑤·⑨"),
          "cowork_operator": ("pearnly_cowork", "operator", "Cowork 代录", "②·⑥·⑩"),
          "erp_direct": ("pearnly_erp_portal", "chat", "ERP 直写", "③·⑦·⑪"),
          "erp_operator": ("pearnly_erp_portal", "operator", "ERP 代录", "④·⑧·⑫")}
FLOW_PRODUCT = {"pearnly_cowork": "cowork", "pearnly_erp_portal": "erp"}
LEDGERS = ("express_local", "mrerp", "express_cloud")           # 一组三格的顺序
LEDGER_LINE = {"mrerp": "mrerp", "express_cloud": "cloud"}      # 这种账本走哪条线（本地 Express 没有线开关）
PHASES = ("p0", "p1", "p2", "p3", "p4", "mrerp")
WAITS = {"mrerp": "⇢MR", "cloud": "⇢云", "onboard": "⇢公", "desktop": "⇢桌"}
WAIT_WORDS = {"mrerp": "等 MR.ERP", "cloud": "等云端", "onboard": "等公司接入", "desktop": "等 CC 桌面版"}
DONE, HALF, TODO, NO, WAIT, OUT = "done", "half", "todo", "no", "wait", "out"
MARKS = {DONE: "✅", HALF: "◐", TODO: "○", NO: "✗"}
DEVICE_ENVS = ("B", "C", "D")       # 验收场景的环境：测试机 + CC 桌面版 / 线上测试租户 / 真手机 → 真机档；A 本机真模型 → 真模型档
TIERS = ("device", "model", "server", "none")
TIER_WORDS = {"device": "真机通过", "model": "真模型通过", "server": "只有服务端", "none": "没做"}
GUIDE_FILES = re.compile(r"(assistant/guide|/guide[-_.]|cc_guide|guided)")


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


def load_all(path: Path = DATA) -> Dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


# ---- 账本线：MR.ERP 写账开关、云端 Express 来源 ----

def _constant(root: Path, ref: str) -> bool | None:
    """``路径:常量名`` → 那个模块顶层赋的布尔值；找不到回 None。"""
    path, _, name = ref.partition(":")
    file = root / path
    if not file.is_file():
        return None
    value = guides_gate._assigned(ast.parse(file.read_text(encoding="utf-8")), name)
    try:
        literal = ast.literal_eval(value) if value is not None else None
    except ValueError:
        return None
    return literal if isinstance(literal, bool) else None


def line_states(lines: Dict[str, Any], root: Path = ROOT) -> Tuple[Dict[Tuple[str, str], Tuple[bool, str]], List[str]]:
    """``{(线, 组): (开着没, 说明)}`` 与读不出来的问题。一组写 ``"open"``、``"closed:<为什么>"``，或一串 ``路径:常量名``（全为 True 才算开）。"""
    found: Dict[Tuple[str, str], Tuple[bool, str]] = {}
    errors: List[str] = []
    for line in ("mrerp", "cloud"):
        table = lines.get(line) or {}
        for group in GROUPS:
            spec = table.get(group)
            if spec is None:
                errors.append(f"line.{line}.{group}: say open, closed:<why> or the switch constants")
                continue
            if spec == "open":
                found[(line, group)] = (True, "开着")
            elif isinstance(spec, str) and spec.startswith("closed:") and spec[7:].strip():
                found[(line, group)] = (False, spec[7:].strip())
            elif isinstance(spec, list) and spec:
                values = {ref: _constant(root, ref) for ref in spec}
                missing = [ref for ref, value in values.items() if value is None]
                if missing:
                    errors.append(f"line.{line}.{group}: {', '.join(missing)} is not a bool constant")
                    continue
                shut = [ref.rpartition(":")[2] for ref, value in values.items() if not value]
                found[(line, group)] = (not shut, "开着" if not shut else " · ".join(f"{name} = False" for name in shut))
            else:
                errors.append(f"line.{line}.{group}: {spec!r} is not open, closed:<why> or a list of switch constants")
    return found, errors


# ---- 证据：流程、验收场景、真机记录、文档摘句 ----

def guide_flows(flows: Dict[str, Dict[str, Any]]) -> set:
    """引导相关的流程：id 或模块带 guide，或覆盖引导文件。"""
    return {fid for fid, flow in flows.items()
            if "guide" in fid or "guide" in str(flow.get("module", ""))
            or any(GUIDE_FILES.search(str(path)) for path in flow.get("covers", ()))}


def guide_scenarios(scenarios: Dict[str, Dict[str, Any]]) -> set:
    """断言了引导地图行（行 id 带 guide）的验收场景。"""
    return {sid for sid, item in scenarios.items() if any("guide" in str(row) for row in item.get("rows", ()))}


def load_evidence(root: Path = ROOT) -> Dict[str, Any]:
    flows = {f["id"]: f for f in tomllib.loads((root / "tests/flows/registry.toml").read_text(encoding="utf-8")).get("flow", [])}
    scenarios = {s["id"]: s for s in tomllib.loads((root / "tests/acceptance/registry.toml").read_text(encoding="utf-8")).get("scenario", [])}
    passes: Dict[str, List[Dict[str, Any]]] = {}
    stale: Dict[str, List[Dict[str, Any]]] = {}
    for item in tomllib.loads((root / "tests/acceptance/evidence.toml").read_text(encoding="utf-8")).get("pass", []):
        fresh = _fresh(root, item, scenarios.get(item["scenario"], {}).get("covers", []))
        (passes if fresh else stale).setdefault(item["scenario"], []).append(item)
    records: Dict[str, List[Dict[str, Any]]] = {}
    for path in sorted((root / "docs" / "work").rglob("*.md")):
        for row_id, items in product_map_gate.device_records(path.read_text(encoding="utf-8", errors="ignore")).items():
            for item in items:
                records.setdefault(row_id, []).append({**item, "file": str(path.relative_to(root))})
    return {"flows": flows, "scenarios": scenarios, "passes": passes, "stale": stale, "records": records}


def _fresh(root: Path, item: Dict[str, Any], covers: List[str]) -> bool:
    """验收证据还算不算数：同 ``check-acceptance.py`` 的新鲜度（covers 的内容指纹没变；没有指纹时证据提交之后 covers 没改过）。"""
    if item.get("covers"):
        return acceptance_gate.covers_digest(root, "HEAD", covers) == item["covers"]
    sha = str(item.get("sha", ""))
    if acceptance_gate.git(root, "cat-file", "-e", f"{sha}^{{commit}}") is None:
        return False
    changed = acceptance_gate.git(root, "diff", "--name-only", sha, "HEAD") or ""
    return not any(fnmatch.fnmatch(path, pattern) for path in changed.splitlines() for pattern in covers)


def _quote(root: Path, ref: str) -> str | None:
    """``docs/work/<文件>::<原文片段>`` 的问题；对得上回 None。"""
    path, _, phrase = ref.partition("::")
    if not path.startswith("docs/work/") or not phrase.strip():
        return f"{ref!r} is not docs/work/<file>::<words in it>"
    file = root / path
    if not file.is_file():
        return f"{path} does not exist"
    if phrase.strip() not in file.read_text(encoding="utf-8", errors="ignore"):
        return f"{path} does not contain {phrase.strip()!r}"
    return None


def tier_of(ref: str, kind: str, evidence: Dict[str, Any], root: Path = ROOT) -> Tuple[str | None, str | None]:
    """一条 model / device 依据 → (算哪一档, 问题)。没跑过的验收场景不算档、也不算问题（登记了、等跑）。"""
    if ref.startswith("acc:"):
        sid = ref[4:]
        scenario = evidence["scenarios"].get(sid)
        if scenario is None:
            return None, f"acceptance scenario {sid} is not in tests/acceptance/registry.toml"
        if not evidence["passes"].get(sid):
            return None, None     # 没跑过或证据已过期（check-acceptance 的新鲜度）：登记着、不算档
        if scenario.get("env") in DEVICE_ENVS:
            return "device", None
        return ("model", None) if scenario.get("driver") == "real" else (None, None)
    if ref.startswith("真机记录:"):
        row_id = ref.split(":", 1)[1]
        items = [item for item in evidence["records"].get(row_id, ()) if item["result"] == "通过"]
        return ("device", None) if items else (None, f"no passing 真机记录 for {row_id} under docs/work/")
    problem = _quote(root, ref)
    return (None, problem) if problem else (kind, None)


# ---- 引导声明的指纹（同提交检查用）----

def signature(guides: Dict[str, List[Dict[str, Any]]]) -> set:
    return {(app, g.get("id"), tuple(sorted(g.get("modes") or ())), tuple(sorted(g.get("ledgers") or ())), g.get("source"),
             "review" in g, "confirm" in g, "result" in g) for app, items in guides.items() for g in items}


def complete(guide: Dict[str, Any]) -> bool:
    """完整引导：不是只有开场一句；有确定、结果；写账的还要有复核。"""
    return guide.get("source") != "chat" and "confirm" in guide and "result" in guide \
        and ("review" in guide or guide.get("writes") is not True)


# ---- 主检查 ----

def _declared(guides, app: str, mode: str, guide_id) -> Dict[str, Any] | None:
    return next((g for g in guides.get(app, []) if g.get("id") == guide_id and mode in g.get("modes", ())), None)


def _cell_keys(row: Dict[str, Any], group: str) -> List[Tuple[str, str]]:
    """[(格键, 账本)]：三格的写 ``组.账本``，一格的写 ``组``（账本为空）。"""
    cells = row.get(group) or []
    return [(f"{group}.{LEDGERS[i]}", LEDGERS[i]) for i in range(3)] if len(cells) == 3 else [(group, "")]


def check(rows: List[Dict[str, Any]], guides: Dict[str, List[Dict[str, Any]]], lines: Dict | None = None,
          evidence: Dict[str, Any] | None = None, unlinked: Dict | None = None, root: Path = ROOT) -> List[str]:
    """``lines`` / ``evidence`` 不给时只核写法与声明（单测里的小数据）。"""
    errors: List[str] = []
    line_state = {}
    if lines is not None:
        line_state, line_errors = line_states(lines, root)
        errors += line_errors
    seen = set()
    linked_flows, linked_scenarios, linked_records = set(), set(), set()
    covered = set()          # (app, guide id, mode, ledger) 总表里有至少 half / wait 的
    for row in rows:
        name = row.get("id")
        if not isinstance(name, str) or not re.match(r"^[a-z][a-z0-9_]*$", name) or name in seen or not row.get("name"):
            errors.append(f"{name!r}: a row needs a unique id and a name")
            continue
        seen.add(name)
        links: Dict[str, Dict[str, Any]] = {}
        for link in row.get("link", ()):
            key = link.get("cell")
            group = str(key).partition(".")[0]
            if group not in GROUPS or key not in [k for k, _ in _cell_keys(row, group)]:
                errors.append(f"{name}.link {key!r}: not a cell of this row (group.ledger, or group for a one-cell group)")
                continue
            if key in links:
                errors.append(f"{name}.link {key}: registered twice")
            links[key] = link
        for group, (app, mode, _title, _numbers) in GROUPS.items():
            cells = row.get(group)
            if not isinstance(cells, list) or len(cells) not in (1, 3):
                errors.append(f"{name}.{group}: one cell (ledger does not matter) or three (local, MR.ERP, cloud)")
                continue
            declared = _declared(guides, app, mode, row.get("guide"))
            for (key, ledger), cell in zip(_cell_keys(row, group), cells):
                try:
                    state, _phase, extra = parse(cell)
                except ValueError as exc:
                    errors.append(f"{name}.{group}: {exc}")
                    continue
                where = f"{name}.{group}" + (f"[{ledger}]" if ledger else "")
                covers_ledger = declared is not None and (not ledger or ledger in declared.get("ledgers", ()))
                line = LEDGER_LINE.get(ledger)
                is_open = line_state.get((line, group), (True, ""))[0] if line and lines is not None else True
                link = links.get(key) or {}
                page = link.get("page_flows") or []
                if state in (DONE, HALF) and declared is None:
                    errors.append(f"{where}: marked {state} but {app} declares no guide {row.get('guide')!r} for {mode}")
                if state == DONE and declared is not None:
                    if declared.get("source") == "chat":
                        errors.append(f"{where}: marked done but the guide only has an opening line (source chat)")
                    elif not complete(declared):
                        errors.append(f"{where}: marked done but the guide has no confirm / result (or no review while it writes)")
                    if ledger and ledger not in declared.get("ledgers", ()):
                        errors.append(f"{where}: marked done but the guide does not cover {ledger}")
                    if evidence is not None and not page:
                        errors.append(f"{where}: marked done but no page flow is registered for {key} (link.page_flows)")
                if state == OUT and covers_ledger:
                    errors.append(f"{where}: marked out of scope but {app} declares this guide for {mode}")
                if lines is None:
                    continue
                if line and not is_open and state not in (WAIT, OUT):
                    errors.append(f"{where}: the {line} line is closed for {group} ({line_state[(line, group)][1]}): mark it wait:{line}")
                if covers_ledger:
                    if state in (TODO, NO):
                        errors.append(f"{where}: {app} declares guide {row.get('guide')!r} for {mode} on {ledger or 'every ledger'}: "
                                      f"mark it half (or done) instead of {state}")
                    if state == WAIT and line and is_open and extra == line:
                        errors.append(f"{where}: the {line} line is open for {group} and the guide covers {ledger}: mark it half or done")
                    if state == HALF and evidence is not None and page and complete(declared) and is_open:
                        errors.append(f"{where}: a complete guide with a registered page flow ({', '.join(page)}): mark it done")
                    if state in (DONE, HALF, WAIT):
                        covered.add((app, row.get("guide"), mode, ledger))
                if link and state in (TODO, NO, WAIT, OUT) and (page or link.get("model") or link.get("device")):
                    errors.append(f"{where}: evidence is registered but the cell is {state}")
                if link and state in (DONE, HALF) and not str(link.get("basis") or "").strip():
                    errors.append(f"{where}: say what the mark rests on (link.basis: declaration, flows, records, commits)")
                if state in (DONE, HALF) and not link and evidence is not None:
                    errors.append(f"{where}: marked {state} without a [[row.link]] saying what it rests on")
                if evidence is None:
                    continue
                for fid in [*(link.get("flows") or []), *page]:
                    flow = evidence["flows"].get(fid)
                    if flow is None:
                        errors.append(f"{where}: flow {fid} is not in tests/flows/registry.toml")
                    elif flow.get("product", "erp") != FLOW_PRODUCT[app]:
                        errors.append(f"{where}: flow {fid} runs {flow.get('product', 'erp')}, not {FLOW_PRODUCT[app]}")
                    linked_flows.add(fid)
                for kind in ("model", "device"):
                    for ref in link.get(kind) or []:
                        _tier, problem = tier_of(ref, kind, evidence, root)
                        if problem:
                            errors.append(f"{where}: {kind} {problem}")
                        if ref.startswith("acc:"):
                            linked_scenarios.add(ref[4:])
                        if ref.startswith("真机记录:"):
                            linked_records.add(ref.split(":", 1)[1])
                for ref in link.get("scenarios") or []:
                    if ref not in evidence["scenarios"]:
                        errors.append(f"{where}: acceptance scenario {ref} is not in tests/acceptance/registry.toml")
                    linked_scenarios.add(ref)
    if lines is not None:
        rows_by_guide = {row.get("guide") for row in rows}
        for app, items in guides.items():
            group_of = {mode: group for group, (a, mode, _t, _n) in GROUPS.items() if a == app}
            for guide in items:
                if guide.get("id") not in rows_by_guide:
                    errors.append(f"{app}: guide {guide.get('id')!r} is declared but no row of {DATA_REL} has guide = {guide.get('id')!r}")
                    continue
                for mode in guide.get("modes", ()):
                    for ledger in guide.get("ledgers", ()):
                        if (app, guide["id"], mode, ledger) not in covered and (app, guide["id"], mode, "") not in covered:
                            errors.append(f"{app}: guide {guide['id']!r} for {mode} on {ledger} has no cell marked half / done / wait "
                                          f"(row guide = {guide['id']!r}, group {group_of.get(mode)})")
    if evidence is not None:
        unlinked = unlinked or {}
        for fid in sorted(guide_flows(evidence["flows"]) - linked_flows - set(unlinked.get("flows", {}))):
            errors.append(f"guide flow {fid} is not registered to any cell ([[row.link]] flows / page_flows, or [unlinked.flows] with why)")
        for sid in sorted(guide_scenarios(evidence["scenarios"]) - linked_scenarios - set(unlinked.get("scenarios", {}))):
            errors.append(f"acceptance scenario {sid} asserts a guide row but is not registered to any cell")
        guide_records = {rid for rid in evidence["records"] if "guide" in rid}
        for rid in sorted(guide_records - linked_records - set(unlinked.get("records", {}))):
            errors.append(f"真机记录 {rid} is about a guide but is not registered to any cell")
        for kind, known in (("flows", evidence["flows"]), ("scenarios", evidence["scenarios"]), ("records", evidence["records"])):
            for ref, why in (unlinked.get(kind) or {}).items():
                if ref not in known:
                    errors.append(f"unlinked.{kind}: {ref} no longer exists: remove it")
                if not str(why).strip():
                    errors.append(f"unlinked.{kind}: {ref} needs a reason")
    return errors


# ---- 同提交：动了引导就要改总表 ----

def _git(root: Path, *args) -> str | None:
    result = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=root, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def _guides_at(root: Path, ref: str) -> Dict[str, List[Dict[str, Any]]] | None:
    """某个提交上的引导声明（按源码读，同 ``check-cc-guides.load``）；读不出回 None。"""
    listing = _git(root, "ls-tree", "-r", "--name-only", ref, "apps")
    if listing is None:
        return None
    found: Dict[str, List[Dict[str, Any]]] = {}
    for hooks in sorted(p for p in listing.splitlines() if re.fullmatch(r"apps/[^/]+/[^/]+/hooks\.py", p)):
        value = guides_gate._assigned(ast.parse(_git(root, "show", f"{ref}:{hooks}") or ""), guides_gate.HOOK)
        if value is None:
            continue
        app = hooks.split("/")[2]
        found[app] = []
        for dotted in ast.literal_eval(value):
            module, _, attr = dotted.rpartition(".")
            parts = module.split(".")
            text = None
            for candidate in (f"apps/{parts[0]}/{'/'.join(parts)}.py", f"apps/{parts[0]}/{'/'.join(parts)}/__init__.py"):
                text = _git(root, "show", f"{ref}:{candidate}")
                if text is not None:
                    break
            node = guides_gate._assigned(ast.parse(text), attr) if text else None
            try:
                literal = ast.literal_eval(node) if node is not None else []
            except ValueError:
                literal = []
            found[app].extend(literal if isinstance(literal, list) else [literal])
    return found


def same_commit(root: Path = ROOT, base: str = "auto") -> Tuple[List[str], str]:
    """(问题, 说明)：相对分叉点，引导声明或引导相关流程的集合变了而 cc-coverage.toml 没改。"""
    base = base if base != "auto" else (os.environ.get("CC_COVERAGE_BASE") or os.environ.get("PRODUCT_MAP_BASE") or "origin/main")
    merge_base = (_git(root, "merge-base", base, "HEAD") or "").strip()
    if not merge_base:
        return [], f"no merge-base with {base}: same-commit rule skipped"
    then = _guides_at(root, merge_base)
    flows_then = _git(root, "show", f"{merge_base}:tests/flows/registry.toml")
    if then is None or flows_then is None:
        return [], "cannot read the merge-base: same-commit rule skipped"
    now, _ = guides_gate.load(root)
    flows_now = {f["id"]: f for f in tomllib.loads((root / "tests/flows/registry.toml").read_text(encoding="utf-8")).get("flow", [])}
    try:
        flows_old = {f["id"]: f for f in tomllib.loads(flows_then).get("flow", [])}
    except tomllib.TOMLDecodeError:
        flows_old = {}
    moved = []
    if signature(then) != signature(now):
        moved.append("guide declarations (business, mode, ledger, source, review / confirm / result)")
    if guide_flows(flows_old) != guide_flows(flows_now):
        moved.append("guide flows in tests/flows/registry.toml")
    if not moved:
        return [], f"guides unchanged since {merge_base[:9]}"
    diff = set((_git(root, "diff", "--name-only", merge_base) or "").splitlines())
    if DATA_REL in diff:
        return [], f"guides changed since {merge_base[:9]} and {DATA_REL} changed with them"
    return [f"{' and '.join(moved)} changed since {merge_base[:9]} but {DATA_REL} did not: update the cells this touches "
            f"(and run --write) in the same branch"], ""


# ---- 报数与生成 ----

def tiers(rows: List[Dict[str, Any]], evidence: Dict[str, Any] | None, root: Path = ROOT) -> Dict[Tuple[str, str], str]:
    """``{(行 id, 格键): 档}``，只给 done / half / todo / no 的格。"""
    found = {}
    for row in rows:
        links = {link.get("cell"): link for link in row.get("link", ())}
        for group in GROUPS:
            for (key, _ledger), cell in zip(_cell_keys(row, group), row.get(group) or ()):
                state = parse(cell)[0]
                if state in (TODO, NO):
                    found[(row["id"], key)] = "none"
                elif state in (DONE, HALF):
                    link = links.get(key) or {}
                    got = set()
                    for kind in ("model", "device"):
                        for ref in link.get(kind) or []:
                            tier = tier_of(ref, kind, evidence, root)[0] if evidence is not None else None
                            if tier:
                                got.add(tier)
                    found[(row["id"], key)] = "device" if "device" in got else "model" if "model" in got else "server"
    return found


def counts(rows: List[Dict[str, Any]], evidence: Dict[str, Any] | None = None) -> Dict[str, int]:
    found = {DONE: 0, HALF: 0, TODO: 0, NO: 0, "wait:mrerp": 0, "wait:cloud": 0, "wait:onboard": 0, "wait:desktop": 0}
    for row in rows:
        for group in GROUPS:
            for cell in row.get(group) or ():
                state, _phase, extra = parse(cell)
                if state == WAIT:
                    found[f"wait:{extra}"] += 1
                elif state != OUT:
                    found[state] += 1
    found["in_scope"] = sum(found.values())
    found["out"] = sum(1 for row in rows for group in GROUPS for cell in row.get(group) or () if parse(cell)[0] == OUT)
    for tier in TIERS:
        found[tier] = 0
    for tier in tiers(rows, evidence).values():
        found[tier] += 1
    return found


def _cell(cell: str) -> str:
    state, phase, extra = parse(cell)
    if state == OUT:
        return f"—（{extra}）"
    if state == WAIT:
        return WAITS[extra]
    return MARKS[state] + (f" {WAITS[extra]}" if state == NO else "") + (f" {phase.upper()}" if phase else "")


def _auto_basis(row, group, ledger, cell, declared, line_state) -> str:
    """没写 link.basis 的格的依据：按声明与账本线说。"""
    state, _phase, extra = parse(cell)
    app, mode = GROUPS[group][0], GROUPS[group][1]
    has = f"{app} 声明了 {row.get('guide')}（{mode}）" if declared is not None else \
        f"{app} 没有声明{(' ' + row['guide']) if row.get('guide') else '这项业务的引导'}（{mode}）"
    if state == WAIT:
        line = LEDGER_LINE.get(ledger) or extra
        why = (line_state or {}).get((line, group), (False, ""))[1]
        return f"{has}；{WAIT_WORDS[extra]}" + (f"（{why}）" if why else "")
    if state == NO:
        return f"{has}；打字也办不了，{WAIT_WORDS[extra]}"
    return f"{has}；打字路径能办，没有引导卡"


def _refs(link: Dict[str, Any]) -> str:
    parts = []
    if link.get("page_flows"):
        parts.append("页面流程 " + "、".join(f"`{f}`" for f in link["page_flows"]))
    if link.get("flows"):
        parts.append("流程 " + "、".join(f"`{f}`" for f in link["flows"]))
    for kind, word in (("model", "真模型"), ("device", "真机")):
        if link.get(kind):
            parts.append(word + " " + "、".join(f"`{r}`" for r in link[kind]))
    if link.get("scenarios"):
        parts.append("验收场景（登记未跑）" + "、".join(f"`{s}`" for s in link["scenarios"]))
    return "；".join(parts)


def render(rows: List[Dict[str, Any]], guides: Dict | None = None, lines: Dict | None = None,
           evidence: Dict[str, Any] | None = None, root: Path = ROOT) -> str:
    found = counts(rows, evidence)
    line_state = line_states(lines, root)[0] if lines is not None else {}
    head = "| # | 业务 | " + " | ".join(f"{title} {numbers}" for _a, _m, title, numbers in GROUPS.values()) + " |"
    lines_out = [BEGIN,
                 "（本段由 `scripts/check-cc-coverage.py --write` 按 `scripts/impact/cc-coverage.toml` 生成，不要手改。）", "",
                 "记号：**✅** 引导做完（声明完整、页面流程从题点到结果）· **◐** 做了一半（已声明、服务端能走，页面整条没有流程证据）· "
                 "**○** 范围内、没有引导（现在靠打字能办）· **✗** 范围内、打字也办不了 · "
                 "**⇢MR / ⇢云 / ⇢公 / ⇢桌** 等 MR.ERP 写账线 / 云端 Express / 公司接入 / CC 桌面版 · **—** 范围外（括号里是依据）。"
                 "每组三格按 本地 Express · MR.ERP · 云端 Express；只有一格的是与账本无关的业务。P 几是排在哪一期。", "",
                 head, "|---|---|" + "---|" * len(GROUPS)]
    for number, row in enumerate(rows, 1):
        lines_out.append(f"| {number} | {row['name']} | " + " | ".join(" · ".join(_cell(c) for c in row[group]) for group in GROUPS) + " |")
    lines_out += ["", f"范围内共 **{found['in_scope']} 格**：✅ {found[DONE]}、◐ {found[HALF]}、○ {found[TODO]}、✗ {found[NO]}、"
                      f"等 MR.ERP {found['wait:mrerp']}、等云端 {found['wait:cloud']}、等公司接入 {found['wait:onboard']}。",
                  "",
                  f"按证据：**真机通过 {found['device']}** · **真模型通过 {found['model']}** · **只有服务端 {found['server']}** · "
                  f"**没做 {found['none']}**；另 等 MR.ERP {found['wait:mrerp']} · 等云端 {found['wait:cloud']} · "
                  f"等公司接入 {found['wait:onboard']} · 范围外 {found['out']}。",
                  "",
                  "### 按格依据", "",
                  "| 业务 | 格 | 标记 | 证据档 | 依据 |", "|---|---|---|---|---|"]
    cell_tiers = tiers(rows, evidence, root)
    for row in rows:
        links = {link.get("cell"): link for link in row.get("link", ())}
        for group, (app, mode, title, _numbers) in GROUPS.items():
            declared = _declared(guides or {}, app, mode, row.get("guide"))
            for (key, ledger), cell in zip(_cell_keys(row, group), row[group]):
                state = parse(cell)[0]
                if state == OUT:
                    continue
                link = links.get(key) or {}
                basis = str(link.get("basis") or "").strip() or _auto_basis(row, group, ledger, cell, declared, line_state)
                refs = _refs(link)
                where = title + ({"express_local": " · 本地", "mrerp": " · MR.ERP", "express_cloud": " · 云端"}.get(ledger, ""))
                tier = TIER_WORDS.get(cell_tiers.get((row["id"], key), ""), WAIT_WORDS.get(parse(cell)[2], ""))
                lines_out.append(f"| {row['id']} | {where} | {_cell(cell)} | {tier} | {basis}" + (f"；{refs}" if refs else "") + " |")
    lines_out.append(END)
    return "\n".join(lines_out)


def doc_section(text: str) -> str | None:
    if BEGIN not in text or END not in text:
        return None
    return text[text.index(BEGIN):text.index(END) + len(END)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--counts", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--base", default="auto", help="same-commit rule base (default CC_COVERAGE_BASE / PRODUCT_MAP_BASE / origin/main)")
    args = parser.parse_args(argv)
    data = load_all()
    rows = data.get("row", [])
    guides, errors = guides_gate.load()
    evidence = load_evidence()
    errors = [*errors, *check(rows, guides, data.get("line", {}), evidence, data.get("unlinked", {}))]
    same, note = same_commit(base=args.base)
    if not args.write:
        errors += same
    if errors:
        for text in errors:
            print(text, file=sys.stderr)
        print(f"check-cc-coverage: {len(errors)} problem(s)", file=sys.stderr)
        return 1
    wanted = render(rows, guides, data.get("line", {}), evidence)
    if args.counts:
        found = counts(rows, evidence)
        print(f"范围内 {found['in_scope']} 格：✅ {found[DONE]} ◐ {found[HALF]} ○ {found[TODO]} ✗ {found[NO]}；"
              f"真机 {found['device']} · 真模型 {found['model']} · 只有服务端 {found['server']} · 没做 {found['none']}；"
              f"等 MR.ERP {found['wait:mrerp']} · 等云端 {found['wait:cloud']} · 等公司接入 {found['wait:onboard']} · 范围外 {found['out']}")
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
        print(f"check-cc-coverage: the table in {DOC.relative_to(ROOT)} differs from {DATA_REL}: run --write", file=sys.stderr)
        return 1
    found = counts(rows, evidence)
    print(f"check-cc-coverage: {len(rows)} businesses, {found['in_scope']} cells in scope, {found[DONE]} done, {found[HALF]} half; "
          f"device {found['device']}, model {found['model']}, server only {found['server']}, none {found['none']} ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
