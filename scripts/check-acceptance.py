#!/usr/bin/env python3
"""验收登记检查（验收测试标准 §6）：该有的场景类别齐不齐、真环境证据新不新；机器打印一屏总览。

一处定义、别处引用：入口只在产品地图（`scripts/impact/product-map/`）定义，本检查只用行 id。
- `tests/acceptance/rows.toml`：地图行的验收属性（effect、upload、card、shared_doc、na…），按行 id 挂。
- `tests/acceptance/registry.toml`：L3 真环境场景。L2 场景就是流程用例（`tests/flows/registry.toml`，可选 `class`、`misuse`、`chain`）。
- `tests/acceptance/chains.toml`：业务链经过哪些行。
- `tests/acceptance/evidence.toml`：每条 L3 场景最近一次通过（运行器写）。
- `tests/acceptance/owed_baseline.txt`：存量欠的场景（`行 id|欠项`），只许减少。

规则：
1. 登记格式与引用：场景的模块、行、链、资源名、不正常使用代号都要对得上；属性表、链、证据引用的行与场景要存在。
2. 欠账：按属性推出每一行欠哪些场景（标准 §3.1），没有场景、没写 na、又不在基线 → 失败；基线里已经还上的条目要删；
   基线只许减少——相对 merge-base 新加条目只允许给「这次才第一次标属性的存量行」。
3. 证据新鲜度：证据的提交之后场景的 covers 文件又改了（或提交不在仓库）→ 这条证据过期，状态算「未验」。状态由机器算，不由人填。
4. 新入口（`[policy] enforce_new_rows`，主会话定何时打开）：相对 merge-base 新加的地图行必须标属性；关着时只列出。
5. `--gate release --since <上次发布的提交>`：改到的文件命中必跑集（tier = "gate"）L3 场景的 covers，而它没有新鲜证据 → 失败
   （`[policy] gate_blocks` 关着时只列出、退出码 0）。

用法：
    python3 scripts/check-acceptance.py                 # 检查（CI 跑）
    python3 scripts/check-acceptance.py --report        # 一屏总览（对外报数只用这个）
    python3 scripts/check-acceptance.py --gate release --since <提交>
退出码：0 = 通过；1 = 有问题。只读，不连网络。
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("tests/acceptance")
CLASSES = ("normal", "variant", "chain", "misuse", "explore")
LAYERS = ("L2", "L3")
ENVS = {"A": "本机实验栈 + 真模型", "B": "测试机实验栈 + CC 桌面版 + 验收账套", "C": "线上测试租户 + CC 桌面版 + 账本", "D": "真手机"}
TIERS = ("gate", "weekly")
EFFECTS = ("read", "site", "ledger", "send", "paid")
MISUSE = {
    "M01": "错文件 / 超限 / 空文件", "M02": "重复提交 / 双击 / 重复事件", "M03": "中途喊停", "M04": "中途换公司 / 账套 / 月份",
    "M05": "卡片挂着打字", "M06": "浏览器断网", "M07": "电脑离线 / 关机", "M08": "Express 开着占用账套", "M09": "权限不足 / 只读账号",
    "M10": "两人同时操作同一张单", "M11": "手机切后台再回来", "M12": "刷新 / 换设备", "M13": "过期的卡片 / 链接 / 票据",
    "M14": "同一张票两条路各走一遍", "M15": "余额不足 / 订阅到期", "M16": "未绑定 / 解绑后点旧入口", "M17": "跨租户 / 跨公司",
    "M18": "结果不明后再来",
}
SHA = re.compile(r"^[0-9a-f]{7,40}$")


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    except FileNotFoundError:
        return None
    return result.stdout if result.returncode == 0 else None


def base_text(root: Path, relative: str, base: str) -> str | None:
    """merge-base 上某个文件的内容；取不到（浅克隆、没有远端）或那时还没有这个文件 → None。"""
    merge = git(root, "merge-base", base, "HEAD")
    return git(root, "show", f"{merge.strip()}:{relative}") if merge else None


class Registry:
    """读入全部登记；`problems` 收格式与引用问题。`map_rows` 可注入（单测），默认读产品地图。"""

    def __init__(self, root: Path = ROOT, map_rows: dict | None = None, tracked: list[str] | None = None):
        self.root, self.problems = root, []
        home = root / HOME
        if map_rows is None:
            product_map = _module("product_map", root / "scripts" / "impact" / "product_map.py")
            map_rows = {row.id: {"ends": row.ends, "tests": row.tests, "product": row.product, "kind": row.kind}
                        for row in product_map.load(root / "scripts" / "impact" / "product-map").rows.values()}
        self.map_rows = map_rows
        self.tracked = tracked if tracked is not None else (git(root, "ls-files") or "").splitlines()
        self.flows = {flow["id"]: flow for flow in _toml(root / "tests" / "flows" / "registry.toml").get("flow", [])}
        rows = _toml(home / "rows.toml")
        self.policy = rows.get("policy", {})
        self.attrs = rows.get("row", {})
        self.scenarios = {}
        for scenario in _toml(home / "registry.toml").get("scenario", []):
            if scenario.get("id") in self.scenarios:
                self.problems.append(f"registry.toml：场景 id 重复 {scenario.get('id')}")
            self.scenarios[scenario.get("id", "?")] = scenario
        self.chains = {chain.get("id", "?"): chain for chain in _toml(home / "chains.toml").get("chain", [])}
        talk = _toml(home / "cc_talk.toml")
        self.talk = {item.get("id", "?"): item for item in talk.get("kind", [])}
        self.lines = {item.get("id", "?"): item for item in talk.get("line", [])}
        self.evidence = {item.get("scenario", "?"): item for item in _toml(home / "evidence.toml").get("pass", [])}
        baseline = home / "owed_baseline.txt"
        self.baseline = read_baseline(baseline.read_text(encoding="utf-8")) if baseline.is_file() else set()
        self._validate()

    # ---- 规则 1：格式与引用
    def _validate(self) -> None:
        locks = _module("acceptance_locks", ROOT / "scripts" / "acceptance" / "locks.py")
        say = self.problems.append
        for row_id, attrs in self.attrs.items():
            where = f"rows.toml [{row_id}]"
            if row_id not in self.map_rows:
                say(f"{where}：产品地图里没有这一行")
            effect = attrs.get("effect")
            if not effect or any(item not in EFFECTS for item in effect):
                say(f"{where}：effect 必填，只能是 {' / '.join(EFFECTS)}")
            for code, reason in attrs.get("na", {}).items():
                if code.partition(":")[2] not in MISUSE and code not in ("chain", "L3:normal", "variant:new+old"):
                    say(f"{where}：na 的键 {code} 不认识")
                if not str(reason).strip():
                    say(f"{where}：na.{code} 要写原因")
        for scenario_id, scenario in self.scenarios.items():
            where = f"registry.toml [{scenario_id}]"
            for key in ("id", "title", "class", "layer", "env", "module", "rows", "covers", "resources", "tier"):
                if key not in scenario:
                    say(f"{where}：缺 {key}")
            if scenario.get("class") not in CLASSES:
                say(f"{where}：class 只能是 {' / '.join(CLASSES)}")
            if scenario.get("layer") != "L3":
                say(f"{where}：这里只登记 L3 场景；L2 场景登记在 tests/flows/registry.toml")
            if scenario.get("env") not in ENVS:
                say(f"{where}：env 只能是 {' / '.join(ENVS)}")
            if scenario.get("tier") not in TIERS:
                say(f"{where}：tier 只能是 {' / '.join(TIERS)}")
            if not isinstance(scenario.get("budget_usd", 0), (int, float)) or scenario.get("budget_usd", 0) < 0:
                say(f"{where}：budget_usd 要是不小于 0 的数")
            if scenario.get("module") and not (self.root / scenario["module"]).is_file():
                say(f"{where}：模块不存在 {scenario['module']}")
            for row_id in scenario.get("rows", []):
                if row_id not in self.map_rows:
                    say(f"{where}：rows 里的 {row_id} 不在产品地图")
            for pattern in scenario.get("covers", []):
                if not any(fnmatch.fnmatch(path, pattern) for path in self.tracked):
                    say(f"{where}：covers 的 {pattern} 一个文件都对不上")
            for resource in scenario.get("resources", []):
                try:
                    locks.check_name(resource)
                except ValueError:
                    say(f"{where}：不认识的资源名 {resource}（见 scripts/acceptance/locks.py）")
            for code in scenario.get("misuse", []):
                if code not in MISUSE:
                    say(f"{where}：不正常使用的代号 {code} 不在目录里")
            if scenario.get("class") == "misuse" and not scenario.get("misuse"):
                say(f"{where}：misuse 类场景要写 misuse = [代号…]")
            for code in scenario.get("talk", []):
                if code not in self.talk:
                    say(f"{where}：talk 的 {code} 不在 cc_talk.toml")
            if not isinstance(scenario.get("repeat", 1), int) or not 1 <= scenario.get("repeat", 1) <= 5:
                say(f"{where}：repeat 要是 1–5 的整数")
            if scenario.get("chain") and scenario["chain"] not in self.chains:
                say(f"{where}：chain {scenario['chain']} 不在 chains.toml")
            if scenario.get("class") == "chain" and not scenario.get("chain"):
                say(f"{where}：chain 类场景要写 chain = \"<链 id>\"")
        for flow_id, flow in self.flows.items():
            if flow.get("class", "normal") not in CLASSES:
                say(f"tests/flows/registry.toml [{flow_id}]：class 只能是 {' / '.join(CLASSES)}")
            for code in flow.get("misuse", []):
                if code not in MISUSE:
                    say(f"tests/flows/registry.toml [{flow_id}]：不正常使用的代号 {code} 不在目录里")
        for kind_id, kind in self.talk.items():
            where = f"cc_talk.toml [{kind_id}]"
            if not kind.get("name") or not kind.get("examples"):
                say(f"{where}：name、examples 必填")
            for line in kind.get("lines", []):
                if line not in self.lines:
                    say(f"{where}：底线 {line} 没有定义")
            for code in kind.get("misuse", []):
                if code not in MISUSE:
                    say(f"{where}：不正常使用的代号 {code} 不在目录里")
        for line_id, line in self.lines.items():
            if not line.get("text") or not line.get("proof"):
                say(f"cc_talk.toml [{line_id}]：text、proof 必填")
        for chain_id, chain in self.chains.items():
            for row_id in chain.get("rows", []):
                if row_id not in self.map_rows:
                    say(f"chains.toml [{chain_id}]：{row_id} 不在产品地图")
            if not chain.get("title") or not chain.get("rows"):
                say(f"chains.toml [{chain_id}]：title、rows 必填")
        for scenario_id, item in self.evidence.items():
            where = f"evidence.toml [{scenario_id}]"
            if scenario_id not in self.scenarios:
                say(f"{where}：场景不在 registry.toml")
            if not SHA.match(str(item.get("sha", ""))) or not re.match(r"^\d{4}-\d{2}-\d{2}$", str(item.get("date", ""))):
                say(f"{where}：sha（提交）与 date（年-月-日）必填")
            if not item.get("run"):
                say(f"{where}：run（证据目录的运行编号）必填")

    # ---- 一行被哪些场景覆盖
    def covering(self, row_id: str) -> list[dict]:
        found = []
        for ref in self.map_rows.get(row_id, {}).get("tests", []):
            kind, _, value = ref.partition(":")
            flow = self.flows.get(value.partition("@")[0]) if kind == "flow" else None
            if flow:
                found.append({"id": flow["id"], "layer": "L2", "class": flow.get("class", "normal"), "misuse": flow.get("misuse", []),
                              "datasets": flow.get("datasets", ["new", "old"])})
        for scenario in self.scenarios.values():
            if row_id in scenario.get("rows", []):
                found.append({"id": scenario["id"], "layer": "L3", "class": scenario.get("class"), "misuse": scenario.get("misuse", []),
                              "datasets": [], "via": scenario.get("via", "")})
        return found

    # ---- 规则 2：欠账（标准 §3.1 的 A1–A10）
    def obligations(self, row_id: str) -> list[str]:
        attrs, ends = self.attrs[row_id], self.map_rows.get(row_id, {}).get("ends", [])
        effect = set(attrs.get("effect", []))
        need, writes = ["normal"], bool(effect & {"site", "ledger"})
        if writes:
            need += ["variant:new+old", "misuse:M02", "misuse:M09"]
        if attrs.get("shared_doc"):
            need.append("misuse:M10")
        if attrs.get("upload"):
            need.append("misuse:M01")
        if "ledger" in effect:
            need += ["L3:normal", "misuse:M03", "misuse:M04", "misuse:M07", "misuse:M08", "chain"]
        if attrs.get("card"):
            need += ["misuse:M03", "misuse:M04", "misuse:M05", "misuse:M12"]
        if writes and "mobile" in ends:
            need.append("misuse:M11")
        if attrs.get("line"):
            need += ["misuse:M02", "misuse:M13", "misuse:M16"]
        return sorted(set(need) - set(attrs.get("na", {})))

    def owed(self, row_id: str) -> list[str]:
        covering, missing = self.covering(row_id), []
        for need in self.obligations(row_id):
            kind, _, value = need.partition(":")
            if need == "normal":
                # 被验收的那段没走界面（via = "api"）的不算界面上的正常主线；它仍能证明账本结果（L3:normal）
                met = any(item["class"] in ("normal", "chain") and item.get("via") != "api" for item in covering)
            elif need == "L3:normal":
                met = any(item["layer"] == "L3" and item["class"] in ("normal", "chain") for item in covering)
            elif need == "chain":
                met = any(item["class"] == "chain" for item in covering)
            elif kind == "variant":
                met = any({"new", "old"} <= set(item["datasets"]) for item in covering)
            else:
                met = any(value in item["misuse"] for item in covering)
            if not met:
                missing.append(need)
        return missing

    def all_owed(self) -> set[str]:
        return {f"{row_id}|{need}" for row_id in self.attrs if row_id in self.map_rows for need in self.owed(row_id)}

    # ---- 规则 3：证据新鲜度
    def freshness(self, scenario_id: str) -> tuple[str, str]:
        """（状态, 说明）：已验 / 过期 / 没有证据。"""
        item = self.evidence.get(scenario_id)
        if not item:
            return "没有证据", ""
        sha, covers = str(item.get("sha", "")), self.scenarios.get(scenario_id, {}).get("covers", [])
        if git(self.root, "cat-file", "-e", f"{sha}^{{commit}}") is None:
            return "过期", f"证据的提交 {sha[:9]} 不在仓库"
        changed = git(self.root, "diff", "--name-only", sha, "HEAD") or ""
        hit = sorted(path for path in changed.splitlines() if any(fnmatch.fnmatch(path, pattern) for pattern in covers))
        if hit:
            return "过期", f"{item.get('date')} 之后改过 {hit[0]}" + (f" 等 {len(hit)} 个文件" if len(hit) > 1 else "")
        return "已验", f"{item.get('date')} {sha[:9]}"


def read_baseline(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")}


def check(root: Path = ROOT, base: str = "origin/main", registry: Registry | None = None) -> tuple[list[str], list[str]]:
    """返回（问题, 只提示不拦的）。"""
    registry = registry or Registry(root)
    problems, notes = list(registry.problems), []
    owed = registry.all_owed()
    for item in sorted(owed - registry.baseline):
        row_id, _, need = item.partition("|")
        problems.append(f"{row_id}：欠场景 {describe(need)}——补场景，或在 rows.toml 写 na（带原因）；存量行第一次标属性时可进 owed_baseline.txt")
    for item in sorted(registry.baseline - owed):
        problems.append(f"owed_baseline.txt：{item} 已经不欠了（或这一行没了），同一提交删掉这条")
    before_baseline = base_text(root, str(HOME / "owed_baseline.txt"), base)
    before_rows = base_text(root, str(HOME / "rows.toml"), base)
    if before_baseline is not None:
        had_attrs = set(tomllib.loads(before_rows).get("row", {})) if before_rows else set()
        for item in sorted(registry.baseline - read_baseline(before_baseline)):
            if item.partition("|")[0] in had_attrs:
                problems.append(f"owed_baseline.txt：{item} 是新加的条目——基线只许减少（只有第一次标属性的存量行可以进）")
    # 规则 4：新加的地图行要标属性
    merge = git(root, "merge-base", base, "HEAD")
    if merge:
        added = new_map_rows(root, merge.strip(), set(registry.map_rows))
        unmarked = sorted(row_id for row_id in added if row_id not in registry.attrs)
        target = problems if registry.policy.get("enforce_new_rows") else notes
        for row_id in unmarked:
            target.append(f"{row_id}：新加的地图行没有验收属性（tests/acceptance/rows.toml）")
    return problems, notes


def new_map_rows(root: Path, merge: str, now: set[str]) -> set[str]:
    listing = git(root, "ls-tree", "--name-only", merge, "scripts/impact/product-map/") or ""
    before = set()
    for path in listing.splitlines():
        if path.endswith(".toml") and not path.endswith(("not_entries.toml", "config.toml")):
            text = git(root, "show", f"{merge}:{path}") or ""
            before |= {row.get("id") for row in tomllib.loads(text).get("row", [])}
    return now - before if before else set()


def describe(need: str) -> str:
    kind, _, value = need.partition(":")
    if kind == "misuse":
        return f"不正常使用 {value}（{MISUSE.get(value, '?')}）"
    return {"normal": "正常主线", "L3:normal": "真环境的正常主线（真账本读回）", "chain": "串联（终点是账本读回）",
            "variant:new+old": "多情形（新开户与老数据两套）"}.get(need, need)


def gate(registry: Registry, since: str) -> tuple[list[str], bool]:
    """上线闸口径：since 之后改到的文件命中必跑集 L3 场景的 covers，而它没有新鲜证据。返回（清单, 是否拦）。"""
    changed = (git(registry.root, "diff", "--name-only", since, "HEAD") or "").splitlines()
    lines = []
    for scenario_id, scenario in sorted(registry.scenarios.items()):
        if scenario.get("tier") != "gate":
            continue
        hit = [path for path in changed if any(fnmatch.fnmatch(path, pattern) for pattern in scenario.get("covers", []))]
        state, detail = registry.freshness(scenario_id)
        if hit and state != "已验":
            lines.append(f"{scenario_id}（{scenario.get('title', '')}）：本批改到 {hit[0]} 等 {len(hit)} 个文件，证据{state}"
                         + (f"（{detail}）" if detail else ""))
    return lines, bool(registry.policy.get("gate_blocks"))


def report(registry: Registry) -> str:
    rows, attrs = registry.map_rows, registry.attrs
    by_product: dict[str, int] = {}
    for row in rows.values():
        by_product[row.get("product", "?")] = by_product.get(row.get("product", "?"), 0) + 1
    ledger_rows = [row_id for row_id, item in attrs.items() if "ledger" in item.get("effect", [])]
    states = {scenario_id: registry.freshness(scenario_id)[0] for scenario_id in registry.scenarios}
    verified_rows = {row_id for scenario_id, state in states.items() if state == "已验"
                     for row_id in registry.scenarios[scenario_id].get("rows", [])}
    classes = {name: 0 for name in CLASSES}
    for flow in registry.flows.values():
        classes[flow.get("class", "normal")] = classes.get(flow.get("class", "normal"), 0) + 1
    l3 = {name: 0 for name in CLASSES}
    for scenario in registry.scenarios.values():
        l3[scenario.get("class", "normal")] = l3.get(scenario.get("class", "normal"), 0) + 1
    owed = registry.all_owed()
    lines = [
        "验收总览（机器打印；对外报数只用这一屏）",
        f"入口登记（产品地图行）：{len(rows)}（" + "、".join(f"{name} {count}" for name, count in sorted(by_product.items())) + "）",
        f"  已标验收属性：{len([r for r in attrs if r in rows])}；还没标：{len(rows) - len([r for r in attrs if r in rows])}（没标的行不算欠账，第四期补全）",
        f"  会写外部账本的行：{len(ledger_rows)}；其中真环境验过（证据新鲜）：{len([r for r in ledger_rows if r in verified_rows])}",
        "场景：",
        "  L2 流程（实验栈）：" + str(len(registry.flows)) + " 条（" + "、".join(f"{label} {classes[name]}" for name, label in LABELS) + "）",
        "  L3 真环境：" + str(len(registry.scenarios)) + " 条（" + "、".join(f"{label} {l3[name]}" for name, label in LABELS) + "）",
        "  L3 证据：" + "、".join(f"{state} {list(states.values()).count(state)}" for state in ("已验", "过期", "没有证据")),
        f"业务链：{len(registry.chains)} 条登记；有串联场景的 {len({s.get('chain') for s in registry.scenarios.values() if s.get('chain')} | {f.get('chain') for f in registry.flows.values() if f.get('chain')})} 条",
        f"欠的场景：{len(owed)} 项（其中在存量基线里 {len(owed & registry.baseline)} 项）",
        f"CC 对话乱用种子：{len(registry.talk)} 类；有固定场景的 "
        f"{len({code for s in registry.scenarios.values() for code in s.get('talk', [])})} 类；底线 {len(registry.lines)} 条",
    ]
    for scenario_id, scenario in sorted(registry.scenarios.items()):
        state, detail = registry.freshness(scenario_id)
        lines.append(f"  · {scenario_id}  {scenario.get('title', '')}：{state}" + (f"（{detail}）" if detail else ""))
    return "\n".join(lines)


LABELS = (("normal", "正常"), ("variant", "多情形"), ("chain", "串联"), ("misuse", "不正常使用"), ("explore", "探索"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--gate", choices=["release"])
    parser.add_argument("--since")
    args = parser.parse_args(argv)
    registry = Registry(ROOT)
    if args.report:
        print(report(registry))
        return 0
    if args.gate:
        if not args.since:
            parser.error("--gate release 要带 --since <上次发布的提交>")
        lines, blocks = gate(registry, args.since)
        print("\n".join(lines) or "验收闸：本批改到的必跑集场景都有新鲜证据")
        if lines and not blocks:
            print("（验收闸现在只报告、不拦：tests/acceptance/rows.toml 的 [policy] gate_blocks 还没打开）")
        return 1 if lines and blocks else 0
    problems, notes = check(ROOT, args.base, registry)
    for line in notes:
        print(f"提示：{line}")
    for line in problems:
        print(f"不通过：{line}")
    if problems:
        print(f"\n验收登记检查不通过（{len(problems)} 项）。规则见 docs/development/acceptance-testing.md §6")
        return 1
    print(f"验收登记检查通过（L3 场景 {len(registry.scenarios)} 条，已标属性的行 {len(registry.attrs)}，存量欠账 {len(registry.baseline)} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
