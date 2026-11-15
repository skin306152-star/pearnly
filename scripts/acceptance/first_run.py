#!/usr/bin/env python3
"""第二期场景的跑单：按首跑顺序一条命令跑一组场景，失败即停，每格记「通过 / 不通过 / 没测到（原因）」，最后出一屏结果。

    python3 scripts/acceptance/first_run.py --holder <窗口名> [--group erp …] [--from <场景 id>] [--paid] [--record] [--list]

- 开跑前把这一趟要占的资源（测试机、测试机桌面、验收账套、MR.ERP 账号、实验栈）一次列清并检查：有一样被别人占着、
  要账套服务器而现在不在开机时段（08:00–20:40）、缺环境变量——就一条都不跑，说清缺什么。
- 会花模型费的格（登记里 budget_usd > 0）默认跳过、记「没测到」；给了 `--paid` 才跑，一屏结果里列出本次花费。
- 一格「不通过」就停（后面的格记「没测到：前面不通过，没往下跑」）；「没测到」（前置条件不满足）不停，接着跑下一格，
  `--stop-on-not-run` 改成也停。
- `--list` 只列这一趟会跑什么、要占什么，不碰任何机器。`--from` 从某一格接着跑（前面的不跑）。
- `--record` 把通过的登记成证据（同 run.py；产品代码有未提交改动时拒绝）。

各组与顺序（顺序即依赖：从连接添加排最前，刚重置的站点只有一次机会）：
  erp       公司接入 → 权限不足 → 采购（示范 1）→ 赊销 → 收款 → 库存单 → 收银日汇总 → 作废。Mac 实验栈（PEARNLY_ACC_SITE_TARGET=maclab）或测试机实验栈。
  operator  代录两条（占测试机桌面，一次一条）。
  chat      CC 对话（真模型）：收款、推送、代录对话。要接了模型的实验栈。
  mrerp     MR.ERP 九条（占 mrerp:test01，会踢掉别处登录；依赖三条没进 main 的分支）。
  cowork    Cowork 录入提交两条（只在测试机实验栈，testcowork 域名切过去）。
跑之前照规矩先问主会话要测试机与账号的时间窗；这个脚本只检查占用锁，不替人排时间。
"""
import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

ERP = ["company.connect.fresh", "company.connect", "company.connect.twice", "denied", "purchase.express", "purchase.express.twice",
       "sales.credit.express", "collection.three", "collection.nbsp", "collection.web.twice", "collection.line.edit",
       "collection.line.refused", "stock.in.express", "stock.out.express", "stock.adjust.express", "stock.transfer.express",
       "stock.void.express", "stock.count.express", "pos.day.express", "purchase.cancel.express", "sales.cancel.express"]
GROUPS = {
    "erp": [f"acc.erp.{name}" for name in ERP],
    "operator": ["acc.erp.operator.purchase.express", "acc.erp.operator.sales.express"],
    "chat": ["acc.erp.cc.collection", "acc.erp.cc.collection.nbsp", "acc.erp.cc.push.purchase", "acc.erp.cc.push.sales",
             "acc.erp.operator.talk.purchase"],
    "mrerp": [f"acc.erp.mrerp.{name}" for name in ("purchase.goods", "purchase.expense", "sales.credit", "sales.cash", "twice",
                                                   "purchase.cash", "sales.discount", "purchase.novat", "purchase.nobook")],
    "cowork": ["acc.cowork.intake.express", "acc.cowork.intake.express.twice"],
}
NEEDS = {  # 每组要配的环境变量（值不进这里；缺了说名字）
    "erp": ["PEARNLY_ACC_ERP_USER", "PEARNLY_ACC_ERP_TAX", "PEARNLY_ACC_BASELINE"],
    "operator": ["PEARNLY_ACC_ERP_USER", "PEARNLY_ACC_ERP_TAX", "PEARNLY_ACC_BASELINE"],
    "chat": ["PEARNLY_ACC_ERP_USER", "PEARNLY_ACC_ERP_TAX", "PEARNLY_ACC_BASELINE", "PEARNLY_ACC_REAL_BASE", "PEARNLY_ACC_REAL_USER"],
    "mrerp": ["PEARNLY_ACC_ERP_USER", "PEARNLY_ACC_MRERP_COMPANY"],
    "cowork": ["PEARNLY_ACC_COWORK_USER", "PEARNLY_ACC_COWORK_CONNECTION", "PEARNLY_ACC_BASELINE"],
}
SKIP_PAID = "会花模型费（上限 {budget} 美元），没给 --paid"
AFTER_FAIL = "前面不通过，没往下跑"


def load_run():
    """运行器（场景加载、占用锁、证据登记都用它那一份）。"""
    if "acceptance_run" not in sys.modules:
        spec = importlib.util.spec_from_file_location("acceptance_run", HERE / "run.py")
        sys.modules["acceptance_run"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sys.modules["acceptance_run"])
    return sys.modules["acceptance_run"]


def plan(scenarios: dict, groups: list[str], start: str = "", paid: bool = False) -> tuple[list[dict], list[dict], list[str]]:
    """这一趟的（要跑的场景, 跳过的格 [{scenario, detail}], 问题）。登记里没有的 id、不认识的组、`--from` 找不到都算问题。"""
    problems, ordered = [], []
    for group in groups:
        if group not in GROUPS:
            problems.append(f"不认识的组 {group}（有：{'、'.join(GROUPS)}）")
            continue
        for scenario_id in GROUPS[group]:
            if scenario_id not in scenarios:
                problems.append(f"{scenario_id} 不在登记里（tests/acceptance/registry.toml）")
            elif scenario_id not in [item["id"] for item in ordered]:
                ordered.append(scenarios[scenario_id])
    if start:
        ids = [item["id"] for item in ordered]
        if start not in ids:
            problems.append(f"--from {start} 不在这一趟里")
        else:
            ordered = ordered[ids.index(start):]
    chosen, skipped = [], []
    for scenario in ordered:
        budget = float(scenario.get("budget_usd", 0) or 0)
        if budget > 0 and not paid:
            skipped.append({"scenario": scenario["id"], "detail": SKIP_PAID.format(budget=budget)})
        else:
            chosen.append(scenario)
    return chosen, skipped, problems


def preflight(run, chosen: list[dict], groups: list[str], holder: str, now=None, environ=None) -> tuple[list[str], list[str]]:
    """开跑前的检查：回（要占的资源, 挡着的事）。挡着的事有一条就不跑。"""
    environ = os.environ if environ is None else environ
    resources = sorted({name for scenario in chosen for name in run.resources_of(scenario, environ.get("PEARNLY_ACC_SITE_TARGET", "lab"))})
    blockers = []
    held = dict(run.locks.status())
    for resource in resources:
        record = held.get(resource)
        if record and record.get("holder") != holder:
            blockers.append("被占用：" + run.locks.describe(resource, record))
    if any(scenario.get("needs_server") for scenario in chosen) and not run.in_server_hours(now):
        blockers.append("账套服务器约 21:00 关机：要它的格只在 08:00–20:40 之间开跑")
    missing = sorted({name for group in groups for name in NEEDS.get(group, []) if not environ.get(name)})
    if missing:
        blockers.append("没配环境变量：" + "、".join(missing))
    if not holder:
        blockers.append("要给 --holder <窗口名>：占用锁上写谁在用")
    return resources, blockers


def execute(run, chosen: list[dict], run_dir: Path, holder: str, targets, stop_on_not_run: bool = False, say=print) -> list[dict]:
    """按顺序跑；不通过即停，没跑到的格照样有一行。"""
    results, stopped = [], ""
    for scenario in chosen:
        if stopped:
            results.append({"scenario": scenario["id"], "outcome": run.NOT_RUN, "detail": stopped, "skipped": True})
            continue
        result = run.run_repeated(scenario, run_dir, holder, False, targets)
        results.append(result)
        say(f"{result['outcome']} {scenario['id']} {result.get('seconds', 0)}s {result.get('detail', '')}".rstrip())
        if result["outcome"] == run.FAIL or (stop_on_not_run and result["outcome"] == run.NOT_RUN):
            stopped = AFTER_FAIL if result["outcome"] == run.FAIL else "前面没测到，按 --stop-on-not-run 没往下跑"
    return results


def screen(run, results: list[dict], skipped: list[dict], resources: list[str], seconds: float) -> str:
    """一屏结果：三种结论各几格、花费、每格一行（没测到的带原因）。"""
    rows = results + [{"scenario": item["scenario"], "outcome": run.NOT_RUN, "detail": item["detail"]} for item in skipped]
    counts = {name: sum(1 for item in rows if item["outcome"] == name) for name in (run.PASS, run.FAIL, run.NOT_RUN)}
    spent = round(sum(float(item.get("spent_usd", 0) or 0) for item in results), 2)
    lines = [f"首跑结果：{counts[run.PASS]} 通过、{counts[run.FAIL]} 不通过、{counts[run.NOT_RUN]} 没测到；"
             f"模型花费 {spent} 美元；用时 {round(seconds / 60, 1)} 分钟",
             "占用过：" + ("、".join(resources) or "无")]
    for item in rows:
        mark = item["outcome"] + ("（重跑过，不算过）" if item.get("retried") else "") + ("（不稳）" if item.get("unstable") else "")
        cost = f"　{item['spent_usd']} 美元" if item.get("spent_usd") else ""
        detail = f"：{item['detail']}" if item.get("detail") else ""
        lines.append(f"  {mark}　{item['scenario']}{cost}{detail}"[:400])
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--group", action="append", choices=list(GROUPS))
    parser.add_argument("--from", dest="start", default="")
    parser.add_argument("--paid", action="store_true", help="连会花模型费的格一起跑")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--list", action="store_true", help="只列会跑什么、要占什么")
    parser.add_argument("--stop-on-not-run", action="store_true")
    parser.add_argument("--holder", default=os.environ.get("PEARNLY_LOCK_HOLDER", ""))
    args = parser.parse_args(argv)
    groups = args.group or ["erp"]
    run = load_run()
    registry = run.registry_check.Registry(ROOT)
    if registry.problems:
        raise SystemExit("登记有问题，先修（python3 scripts/check-acceptance.py）：\n" + "\n".join(registry.problems[:10]))
    chosen, skipped, problems = plan(registry.scenarios, groups, args.start, args.paid)
    if problems:
        raise SystemExit("\n".join(problems))
    resources, blockers = preflight(run, chosen, groups, args.holder or ("list" if args.list else ""))
    print(f"这一趟：{len(chosen)} 格要跑" + (f"、{len(skipped)} 格跳过（会花模型费）" if skipped else "")
          + f"；模型费上限合计 {round(sum(float(item.get('budget_usd', 0) or 0) for item in chosen), 2)} 美元")
    print("要占：" + ("、".join(resources) or "无"))
    if args.list:
        for scenario in chosen:
            print(f"  {scenario['id']}　{scenario.get('title', '')}"[:200])
        for item in skipped:
            print(f"  （跳过）{item['scenario']}：{item['detail']}")
        for blocker in blockers:
            print("现在跑会被挡：" + blocker)
        return 0
    if blockers:
        print("没有开跑：\n" + "\n".join("  " + blocker for blocker in blockers))
        return 2
    sha, dirty = run.git("rev-parse", "HEAD"), bool(run.git("status", "--porcelain", "apps", "frontend", "companion"))
    if args.record and dirty:
        raise SystemExit("产品代码有未提交改动：这样跑出来的结果不能登记为证据")
    home = run.runs_home()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + sha[:9]
    run_dir, started = home / run_id, time.monotonic()
    targets = run._load("acceptance_targets", HERE / "targets.py").Targets()
    results = execute(run, chosen, run_dir, args.holder, targets, args.stop_on_not_run)
    for result in results:
        if result["outcome"] != run.NOT_RUN:
            with open(home / "history.jsonl", "a", encoding="utf-8") as out:
                out.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "scenario": result["scenario"], "sha": sha,
                                      "outcome": result["outcome"], "retried": result.get("retried", False)}, ensure_ascii=False) + "\n")
            if result["outcome"] == run.PASS and run.unstable(result["scenario"], home):
                result.update(unstable=True, detail="这次过了，但最近 10 次里环境类重跑达到 2 次：标「不稳」，不登记证据")
    text = screen(run, results, skipped, resources, time.monotonic() - started)
    run_dir.mkdir(parents=True, exist_ok=True)
    ok = bool(results) and all(item["outcome"] == run.PASS and not item.get("unstable") for item in results)
    (run_dir / "run.json").write_text(json.dumps({"run": run_id, "sha": sha, "dirty": dirty, "scope": "first-run " + ",".join(groups), "ok": ok,
                                                  "holder": args.holder, "results": results, "skipped": skipped}, ensure_ascii=False, indent=1,
                                                 default=str), encoding="utf-8")
    (run_dir / "first-run.txt").write_text(text + "\n", encoding="utf-8")
    if args.record:
        ran = [item for item in results if not item.get("skipped")]
        digests = {scenario["id"]: run.registry_check.covers_digest(ROOT, sha, scenario.get("covers", [])) for scenario in registry.scenarios.values()}
        written = run.record_evidence(ran, sha, run_id, ROOT / run.registry_check.HOME / "evidence.toml", digests)
        print(f"已登记证据：{', '.join(written) or '无'}（tests/acceptance/evidence.toml，随本分支提交）")
    print(text)
    print(f"证据：{run_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
