#!/usr/bin/env python3
"""真环境验收运行器（L3，验收测试标准 §2、§5）：按登记跑场景，自动拿占用锁、守费用上限、留统一格式的证据，
只把「断言全过、没靠重跑」的结果登记为证据。

    python3 scripts/acceptance/run.py --scenario <id> [--scenario <id>…]    # 指定场景
    python3 scripts/acceptance/run.py --tier gate [--since <提交>]           # 必跑集；带 --since 只跑本批改到的
    python3 scripts/acceptance/run.py --scenario <id> --dry-run              # 只打印要占用什么、花多少、跑哪个模块；不碰任何机器
    … --record        通过的结果写进 tests/acceptance/evidence.toml（要求产品代码没有未提交改动）
    … --post-status   把结果回写成提交状态 pearnly/acceptance（只对 --tier gate）

场景模块（`scripts/acceptance/scenarios/<名>.py`）接口与流程用例同一个思路（基线 → 备数据 → 步骤 → 核对 → 收尾）：
    def run(ctx):      # 用 ctx.step("名字", "预期") 包每一步；ctx.check(名字, 预期, 实际) 记断言；ctx.save(文件名, 内容) 留证据
    def cleanup(ctx):  # 可选；成功、失败、出错都调用

结果三态：通过 / 不通过 / 没测到（资源被占、不在可用时段、--dry-run、前置不满足——都写原因，不算通过）。
偶发规则（标准 §5.4）：断言失败不重跑；只有环境类错误（`Infra`：连不上、超时）自动重跑一次并在结果里写明；
一条场景最近 10 次里环境类重跑达到 2 次 → 标「不稳」，不登记证据。
真环境的时间与费用逐次报主会话批；本运行器不替任何人做这个决定。
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
STATUS = "pearnly/acceptance"
PASS, FAIL, NOT_RUN = "通过", "不通过", "没测到"
KEEP_DAYS = 30
SERVER_HOURS = (8, 0, 20, 40)  # 账套服务器约 21:00 关机：要它的场景只在 08:00–20:40 之间开跑


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


locks = _load("acceptance_locks", HERE / "locks.py")
registry_check = _load("check_acceptance", ROOT / "scripts" / "check-acceptance.py")


# 错误类全进程只有一份：已经加载过就用那一份（场景、驱动、执行通道抛的要和这里接的是同一个类）
errors = sys.modules.get("acc_errors") or _load("acc_errors", HERE / "acc_errors.py")
Infra, Unmet, OverBudget = errors.Infra, errors.Unmet, errors.OverBudget


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def runs_home() -> Path:
    path = Path(os.environ.get("PEARNLY_ACCEPTANCE_RUNS") or Path.home() / "pearnly-devtools" / "acceptance-runs")
    path.mkdir(parents=True, exist_ok=True)
    return path


class Ctx:
    """交给场景模块的上下文：记步骤与断言、留证据、记费用、取执行通道。"""

    def __init__(self, scenario: dict, folder: Path, holder: str, attempt: int = 1, targets=None):
        self.scenario, self.folder, self.holder, self.attempt = scenario, folder, holder, attempt
        self.steps: list[dict] = []
        self.spent, self.budget = 0.0, float(scenario.get("budget_usd", 0))
        self.facts: dict = {}          # 写成的单号、各端版本等，进证据登记
        self.targets = targets          # 执行通道工厂（targets.py）；单测注入替身
        self._on_failure: list = []     # 失败时要留的现场（标准 §5.3）：[(名字, 取现场的函数)]
        folder.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def step(self, name: str, expected: str = ""):
        record = {"name": name, "expected": expected, "ok": False, "checks": []}
        self.steps.append(record)
        started = time.monotonic()
        try:
            yield record
            record["ok"] = all(check["ok"] for check in record["checks"] if not check.get("soft"))
        except Infra as error:
            record["error"], record["infra"] = str(error), True
            raise
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            record["seconds"] = round(time.monotonic() - started, 1)

    def check(self, name: str, expected, actual, ok: bool | None = None, soft: bool = False) -> bool:
        """一条断言：默认 预期 == 实际；复杂比较自己算好传 ok。记在当前步骤里，不立即中断（一步里的断言看全）。
        `soft=True` 只给真模型场景里「说法」类的断言用（问了几次、给没给选项、回复语言）：单次不过不判失败，按多次运行的通过比例判
        （标准 §5.5）。账本、执行记录、底线类的断言一律是硬的，任何一次不过就是不通过。"""
        passed = (expected == actual) if ok is None else bool(ok)
        if not self.steps:
            self.steps.append({"name": "（步骤外）", "expected": "", "ok": True, "checks": []})
        self.steps[-1]["checks"].append({"name": name, "expected": expected, "actual": actual, "ok": passed, **({"soft": True} if soft else {})})
        return passed

    def require(self, name: str, expected, actual, ok: bool | None = None) -> None:
        """断言不过就停在这一步（后面的步骤依赖它）。"""
        if not self.check(name, expected, actual, ok):
            raise AssertionError(f"{name}：预期 {expected!r}，实际 {actual!r}")

    def save(self, name: str, content) -> Path:
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=1, default=str),
                            encoding="utf-8")
        return path

    def on_failure(self, name: str, collect) -> None:
        """登记一样「失败时自动留」的现场（标准 §5.3）：服务端原值（断言涉及的那条记录按主键读出的原始内容、会话事件、写账件与回执）
        或页面现场（截图、页面文字、控制台报错、地址）。场景在拿到主键 / 开了页面的那一刻就登记；这条场景不通过时运行器在收尾
        还原**之前**逐个调用，结果存进 `failure/<名字>`（bytes 原样存，其余存 JSON）。通过时不调用。"""
        self._on_failure.append((name, collect))

    def collect_failure(self) -> list[str]:
        """逐个取现场并存盘；某一样取不到不影响别的，也不盖住原来的失败（原因存在 `failure/<名字>.error.txt`）。"""
        saved = []
        for name, collect in self._on_failure:
            try:
                content = collect()
                target = f"failure/{name}" if isinstance(content, bytes) or "." in name else f"failure/{name}.json"
                self.save(target, content)
                saved.append(target)
            except Exception as failed:  # noqa: BLE001 —— 留现场尽力而为，但取不到要看得见
                self.save(f"failure/{name}.error.txt", "".join(traceback.format_exception(failed)))
                saved.append(f"failure/{name}.error.txt")
        return saved

    def spend(self, usd: float, what: str) -> None:
        """按实际调用记费用；超过这条场景登记的上限就停（标准 §8：到额即停，不靠事前估算）。"""
        self.spent = round(self.spent + usd, 4)
        if self.spent > self.budget:
            raise OverBudget(f"{what} 后累计 {self.spent} 美元，超过这条场景的上限 {self.budget}")


def outcome_of(ctx: Ctx, error: BaseException | None) -> tuple[str, str]:
    if isinstance(error, Unmet):
        return NOT_RUN, str(error)
    if error is not None:
        return FAIL, f"{type(error).__name__}: {error}"
    failed = [f"{step['name']} / {check['name']}" for step in ctx.steps for check in step["checks"]
              if not check["ok"] and not check.get("soft")]
    if failed:
        return FAIL, "断言不过：" + "；".join(failed[:5])
    if not any(not check.get("soft") for step in ctx.steps for check in step["checks"]):
        return FAIL, "这条场景一条断言都没有（能跑完不算通过）"
    return PASS, ""


def in_server_hours(now=None) -> bool:
    moment = time.localtime(now)
    minute = moment.tm_hour * 60 + moment.tm_min
    return SERVER_HOURS[0] * 60 + SERVER_HOURS[1] <= minute < SERVER_HOURS[2] * 60 + SERVER_HOURS[3]


def unstable(scenario_id: str, home: Path) -> bool:
    """最近 10 次里环境类重跑达到 2 次。"""
    path = home / "history.jsonl"
    if not path.exists():
        return False
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    recent = [line for line in lines if line.get("scenario") == scenario_id][-10:]
    return sum(1 for line in recent if line.get("retried")) >= 2


def run_one(scenario: dict, run_dir: Path, holder: str, dry: bool, targets=None, module=None, now=None) -> dict:
    """跑一条场景。返回结果字典（进 run.json）。"""
    scenario_id = scenario["id"]
    result = {"scenario": scenario_id, "title": scenario.get("title", ""), "class": scenario.get("class"), "env": scenario.get("env"),
              "resources": scenario.get("resources", []), "budget_usd": scenario.get("budget_usd", 0), "retried": False}
    if dry:
        return {**result, "outcome": NOT_RUN, "detail": "--dry-run：没有碰任何机器", "module": scenario.get("module")}
    if scenario.get("needs_server") and not in_server_hours(now):
        return {**result, "outcome": NOT_RUN, "detail": "账套服务器约 21:00 关机：这条场景只在 08:00–20:40 之间开跑"}
    module = module or _load(f"acc_{scenario_id.replace('.', '_')}", ROOT / scenario["module"])
    started = time.monotonic()
    try:
        held = locks.hold_all(scenario.get("resources", []), holder, f"验收场景 {scenario_id}")
    except locks.Busy as busy:
        return {**result, "outcome": NOT_RUN, "detail": f"资源被占用：{busy}"}
    try:
        for attempt in (1, 2):
            ctx = Ctx(scenario, run_dir / scenario_id / (f"attempt-{attempt}" if attempt > 1 else ""), holder, attempt, targets)
            error = None
            try:
                module.run(ctx)
            except BaseException as caught:  # noqa: BLE001 —— 场景里的任何错误都要记下来并走收尾
                if isinstance(caught, KeyboardInterrupt):
                    raise
                error = caught
                ctx.save("error.txt", "".join(traceback.format_exception(caught)))
            finally:
                if outcome_of(ctx, error)[0] == FAIL:
                    result["failure_evidence"] = ctx.collect_failure()  # 先留现场，再收尾还原
                else:
                    result.pop("failure_evidence", None)
                if hasattr(module, "cleanup"):
                    try:
                        module.cleanup(ctx)
                    except Exception as failed:  # noqa: BLE001 —— 收尾失败要显眼，但不盖住原来的错
                        ctx.save("cleanup-error.txt", "".join(traceback.format_exception(failed)))
                        result["cleanup_error"] = f"{type(failed).__name__}: {failed}"
            outcome, detail = outcome_of(ctx, error)
            result.update(outcome=outcome, detail=detail, steps=ctx.steps, spent_usd=ctx.spent, facts=ctx.facts,
                          evidence=str(ctx.folder))
            if isinstance(error, Infra) and attempt == 1:
                result["retried"], result["retry_reason"] = True, str(error)
                continue  # 只有环境类错误重跑，且只一次
            break
        if result.get("cleanup_error") and result["outcome"] == PASS:
            result.update(outcome=FAIL, detail=f"断言都过了，但收尾还原失败：{result['cleanup_error']}")
    finally:
        for item in reversed(held):
            item.__exit__(None, None, None)
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def run_repeated(scenario: dict, run_dir: Path, holder: str, dry: bool, targets=None, module=None, now=None) -> dict:
    """真模型场景跑 `repeat` 次（登记里写，默认 1）：硬断言每一次都要过；软断言按通过比例（`soft_pass`，默认三分之二）判。
    任何一次不通过或没测到就停，不再往下花钱。"""
    repeat = int(scenario.get("repeat", 1))
    if repeat <= 1 or dry:
        result = run_one(scenario, run_dir, holder, dry, targets, module, now)
        return _judge_soft(result, [result], scenario) if not dry else result
    rounds = []
    for index in range(1, repeat + 1):
        rounds.append(run_one(scenario, run_dir / f"round-{index}", holder, dry, targets, module, now))
        if rounds[-1]["outcome"] != PASS:
            break
    result = dict(rounds[-1], rounds=[{"outcome": item["outcome"], "detail": item.get("detail", ""), "evidence": item.get("evidence"),
                                        "retried": item.get("retried", False)} for item in rounds],
                  spent_usd=round(sum(item.get("spent_usd", 0) for item in rounds), 4),
                  seconds=round(sum(item.get("seconds", 0) for item in rounds), 1), retried=any(item.get("retried") for item in rounds))
    return _judge_soft(result, rounds, scenario)


def _judge_soft(result: dict, rounds: list[dict], scenario: dict) -> dict:
    tally: dict[str, list[bool]] = {}
    for item in rounds:
        for step in item.get("steps", []):
            for check in step["checks"]:
                if check.get("soft"):
                    tally.setdefault(f"{step['name']} / {check['name']}", []).append(check["ok"])
    need = float(scenario.get("soft_pass", 2 / 3))
    result["soft"] = {name: f"{sum(values)}/{len(values)}" for name, values in tally.items()}
    weak = [f"{name} 只过 {sum(values)}/{len(values)}" for name, values in tally.items() if sum(values) / len(values) < need - 1e-9]
    if result["outcome"] == PASS and weak:
        result.update(outcome=FAIL, detail="说法类断言通过比例不够：" + "；".join(weak))
    return result


def select(registry, args) -> list[dict]:
    scenarios = registry.scenarios
    if args.scenario:
        missing = [name for name in args.scenario if name not in scenarios]
        if missing:
            raise SystemExit(f"不在 tests/acceptance/registry.toml：{', '.join(missing)}")
        return [scenarios[name] for name in args.scenario]
    chosen = [item for item in scenarios.values() if item.get("tier") == args.tier]
    if args.since:
        changed = git("diff", "--name-only", args.since, "HEAD").splitlines()
        chosen = [item for item in chosen
                  if any(fnmatch.fnmatch(path, pattern) for path in changed for pattern in item.get("covers", []))]
    return sorted(chosen, key=lambda item: item["id"])


def record_evidence(results: list[dict], sha: str, run_id: str, path: Path, digests: dict | None = None) -> list[str]:
    """通过且没靠重跑的结果写进证据登记：一条场景一段，按 id 排序（改动小、好合并）。``digests``：{场景 id: covers 内容指纹}，
    有就一起记（证据按它判新鲜，变基换了提交号不影响）。"""
    import tomllib

    current = {item["scenario"]: item for item in tomllib.loads(path.read_text(encoding="utf-8")).get("pass", [])} if path.is_file() else {}
    written = []
    for result in results:
        if result["outcome"] == PASS and not result.get("unstable"):
            current[result["scenario"]] = {"scenario": result["scenario"], "sha": sha, "date": time.strftime("%Y-%m-%d"),
                                           "env": result.get("env", ""), "run": run_id, "retried": bool(result.get("retried")),
                                           **({"covers": digests[result["scenario"]]} if (digests or {}).get(result["scenario"]) else {}),
                                           **{key: value for key, value in (result.get("facts") or {}).items()
                                              if isinstance(value, (str, int, float, bool)) or
                                              (isinstance(value, list) and all(isinstance(v, str) for v in value))}}
            written.append(result["scenario"])
    head = ("# 每条 L3 场景最近一次通过（scripts/acceptance/run.py --record 写，不手改）。证据之后 covers 文件的内容又变了，\n"
            "# check-acceptance.py 自动把它算成「过期」。原始证据在本机 ~/pearnly-devtools/acceptance-runs/<run>/（保留 30 天）。\n")
    body = []
    for scenario_id in sorted(current):
        lines = ["[[pass]]"]
        for key, value in current[scenario_id].items():
            lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        body.append("\n".join(lines))
    path.write_text(head + "\n" + "\n\n".join(body) + ("\n" if body else ""), encoding="utf-8")
    return written


def sweep(home: Path, now: float | None = None) -> None:
    limit = (now or time.time()) - KEEP_DAYS * 86400
    for folder in home.iterdir():
        if folder.is_dir() and folder.stat().st_mtime < limit and not (folder / "KEEP").exists():
            import shutil
            shutil.rmtree(folder, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--tier", choices=registry_check.TIERS)
    parser.add_argument("--since")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--post-status", action="store_true")
    parser.add_argument("--holder", default=os.environ.get("PEARNLY_LOCK_HOLDER", ""))
    args = parser.parse_args(argv)
    if not args.scenario and not args.tier:
        parser.error("要给 --scenario 或 --tier")
    if not args.holder and not args.dry_run:
        parser.error("要给 --holder <窗口名>（或环境变量 PEARNLY_LOCK_HOLDER）：占用锁上写谁在用")
    registry = registry_check.Registry(ROOT)
    if registry.problems:
        raise SystemExit("登记有问题，先修（python3 scripts/check-acceptance.py）：\n" + "\n".join(registry.problems[:10]))
    chosen = select(registry, args)
    sha, dirty = git("rev-parse", "HEAD"), bool(git("status", "--porcelain", "apps", "frontend", "companion"))
    if (args.record or args.post_status) and dirty:
        raise SystemExit("产品代码有未提交改动：这样跑出来的结果不能登记为证据，也不能回写状态")
    if args.post_status and (args.scenario or not args.tier == "gate"):
        raise SystemExit("--post-status 只对 --tier gate 用")
    home = runs_home()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + sha[:9]
    run_dir = home / run_id
    targets = None if args.dry_run else _load("acceptance_targets", HERE / "targets.py").Targets()
    results, started = [], time.monotonic()
    for scenario in chosen:
        result = run_repeated(scenario, run_dir, args.holder or "dry-run", args.dry_run, targets)
        if not args.dry_run and result["outcome"] != NOT_RUN:
            with open(home / "history.jsonl", "a", encoding="utf-8") as out:
                out.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "scenario": scenario["id"], "sha": sha,
                                      "outcome": result["outcome"], "retried": result.get("retried", False)}, ensure_ascii=False) + "\n")
            if result["outcome"] == PASS and unstable(scenario["id"], home):
                result["unstable"] = True
                result["detail"] = "这次过了，但最近 10 次里环境类重跑达到 2 次：标「不稳」，不登记证据，先修场景或环境"
        results.append(result)
        mark = result["outcome"] + ("（重跑过）" if result.get("retried") else "") + ("（不稳）" if result.get("unstable") else "")
        print(f"{mark} {scenario['id']} {result.get('seconds', 0)}s {result.get('detail', '')}".rstrip())
    counts = {name: sum(1 for item in results if item["outcome"] == name) for name in (PASS, FAIL, NOT_RUN)}
    ok = bool(results) and counts[FAIL] == 0 and counts[NOT_RUN] == 0 and not any(item.get("unstable") for item in results)
    scope = f"tier {args.tier}" + (f" since {args.since[:12]}" if args.since else "") if args.tier else "scenarios " + ",".join(args.scenario)
    summary = (f"{scope}; {len(results)} scenarios; {counts[PASS]} passed, {counts[FAIL]} failed, {counts[NOT_RUN]} not run; "
               f"{round(sum(item.get('spent_usd', 0) for item in results), 2)} usd; {round(time.monotonic() - started)}s")
    if not args.dry_run:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({"run": run_id, "sha": sha, "dirty": dirty, "scope": scope, "ok": ok, "summary": summary,
                                                      "holder": args.holder, "results": results}, ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
        sweep(home)
        if args.record:
            digests = {scenario["id"]: registry_check.covers_digest(ROOT, sha, scenario.get("covers", [])) for scenario in registry.scenarios.values()}
            written = record_evidence(results, sha, run_id, ROOT / registry_check.HOME / "evidence.toml", digests)
            print(f"已登记证据：{', '.join(written) or '无'}（tests/acceptance/evidence.toml，随本分支提交）")
        if args.post_status:
            repo = json.loads(subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner"], cwd=ROOT, capture_output=True, text=True).stdout)
            subprocess.run(["gh", "api", f"repos/{repo['nameWithOwner']}/statuses/{sha}", "-f", f"state={'success' if ok else 'failure'}",
                            "-f", f"context={STATUS}", "-f", f"description={summary[:139]}"], cwd=ROOT, check=True, capture_output=True)
        print(f"证据：{run_dir}")
    print(summary)
    return 0 if ok or args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
