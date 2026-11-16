#!/usr/bin/env python3
"""GitHub 工作流 `.github/workflows/flows.yml` 的两步小活（11-16 仓库公开、流程用例搬到 GitHub 免费标准机器）：

    python3 scripts/flows/github.py decide      # plan 任务：定范围、要不要跑、要不要回写，切份写进 $GITHUB_OUTPUT
    python3 scripts/flows/github.py failed-sites 汇总.json              # finish 任务：补跑要建的站点
    python3 scripts/flows/github.py summary 结果.json [补跑前.json]   # finish 任务：结果摘要（markdown，写进 $GITHUB_STEP_SUMMARY）

范围（与本机同一套选法，`run.py`）：分支 push → `--changed --base origin/main`（受影响的流程，产品地图并集）；
手动触发 → 选 changed / all（全套，部署闸认它）/ flows（指定流程，只看结果、不回写状态）。
不跑的情况（push）：这个提交已在 origin/main 上（main 不自动跑全套）；这个提交已有同范围或全套通过的 `pearnly/flows`
（例如本机已跑过再推上来——GitHub 再回写一个小范围的状态会把全套盖掉，部署闸就不认了）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = [sys.executable, str(ROOT / "scripts/flows/run.py")]
STATUS = "pearnly/flows"


def scope_args(event, scope, flows):
    """回 (run.py 的范围参数, 回不回写状态)。"""
    if event == "workflow_dispatch" and scope == "all":
        return ["--all"], True
    if event == "workflow_dispatch" and scope == "flows":
        ids = [item.strip() for item in (flows or "").replace(",", " ").split() if item.strip()]
        if not ids:
            raise SystemExit("scope=flows 要填流程编号（tests/flows/registry.toml 的 id，逗号或空格隔开）")
        return [arg for flow in ids for arg in ("--flow", flow)], False
    return ["--changed", "--base", "origin/main"], True


def existing_status(sha):
    """这个提交现有的 `pearnly/flows`：(state, description) 或 None（同部署闸：合并状态里这个名字最新的一条）。"""
    out = subprocess.run(["gh", "api", f"repos/{os.environ['GITHUB_REPOSITORY']}/commits/{sha}/status", "--jq",
                          f'[.statuses[] | select(.context == "{STATUS}")][0] | select(. != null) | [.state, .description]'],
                         capture_output=True, text=True)
    return tuple(json.loads(out.stdout)) if out.returncode == 0 and out.stdout.strip() else None


def covered(status, scope):
    """已有的状态够不够：全套通过盖住一切；同一范围通过也不用再跑。"""
    if not status or status[0] != "success":
        return False
    description = status[1] or ""
    return description.startswith("all flows") or description.startswith(scope + ";")


def on_main(sha):
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=ROOT).returncode == 0


def calibration(message):
    """提交说明里的 `[flows-on-github: 流程1,流程2]`（只跑这几条、不回写，同手动 scope=flows）或 `[flows-on-github: all]`（全套）：校准用。"""
    found = re.search(r"\[flows-on-github:([^\]]+)\]", message or "")
    return found.group(1).strip() if found else ""


def decide():
    event, sha = os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_SHA"]
    scope, flows = os.environ.get("INPUT_SCOPE", ""), os.environ.get("INPUT_FLOWS", "")
    if event == "push":
        message = subprocess.run(["git", "log", "-1", "--format=%B", sha], cwd=ROOT, capture_output=True, text=True).stdout
        wanted = calibration(message)
        if wanted:  # `all` = 全套（在工作流还没进 main、手动触发用不了时校准墙钟）
            event, scope, flows = ("workflow_dispatch", "all", "") if wanted == "all" else ("workflow_dispatch", "flows", wanted)
    args, post = scope_args(event, scope, flows)
    plan = json.loads(subprocess.run(RUN + args + ["--plan"], cwd=ROOT, capture_output=True, text=True, check=True).stdout)
    skip = ""
    if event == "push" and post:
        if on_main(sha):
            skip = "commit already on origin/main (main does not run the whole suite by itself)"
        else:
            status = existing_status(sha)
            if covered(status, plan["scope"]):
                skip = f"already {status[0]}: {status[1]}"
    print(json.dumps(plan, ensure_ascii=False, indent=1))
    print("skip: " + skip if skip else f"run: {' '.join(args)}; post status: {post}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"args={' '.join(args)}\n")
        out.write(f"post={'--post-status' if post else ''}\n")
        out.write(f"run={'false' if skip else 'true'}\n")
        out.write(f"cells={plan['cells']}\n")
        out.write(f"scope={plan['scope']}\n")
        out.write(f"matrix={json.dumps({'include': plan['shards']}, ensure_ascii=False)}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as out:
        out.write(f"**Flows** `{plan['scope']}`：{plan['flows']} flows, {plan['cells']} runs in {len(plan['shards'])} shards"
                  + (f"；skipped — {skip}" if skip else "") + "\n")


def cell(r):
    return f"`{r['flow']}` {r['dataset']}/{r['viewport']}"


def summary(path, before=None):
    """结果摘要：结论、不通过的格、补跑才过的格、留下了数据的格、常驻 Python 退回老路的次数。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    results = data["results"]
    lines = [f"### Flows {'passed' if data['ok'] else 'FAILED'}", "", f"`{data['summary']}`", ""]
    failed = [r for r in results if not r["ok"]]
    if failed:
        lines += [f"**Failed ({len(failed)})**", ""]
        for r in failed:
            bad = [c.get("case") for c in r.get("checks", []) if not c.get("ok")]
            lines.append(f"- {cell(r)} {r.get('error', '')[:200]} {', '.join(map(str, bad))[:200]}".rstrip())
        lines.append("")
    reran = [r for r in results if r.get("rerun") and r["ok"]]
    if reran:
        lines += [f"**Passed only on the rerun ({len(reran)})**", ""] + [f"- {cell(r)}" for r in reran] + [""]
    left = [r for r in results if r.get("left_data")]
    if left:
        lines += [f"**Left data behind ({len(left)}, recorded, not failed)**", ""]
        lines += [f"- {cell(r)} `{json.dumps(r['left_data'], ensure_ascii=False)[:300]}`" for r in left] + [""]
    fallbacks = data.get("lab_python_fallbacks") or []
    if fallbacks:
        lines += [f"Lab worker fell back to one process per call {len(fallbacks)} times: `{fallbacks[0][:200]}`", ""]
    if before:
        first = json.loads(Path(before).read_text(encoding="utf-8"))
        lines += [f"Before the rerun: `{first['summary']}`", ""]
    return "\n".join(lines)


def failed_sites(path):
    """补跑要建的站点：不通过的格各自的数据集站点。"""
    sys.path.insert(0, str(ROOT / "scripts/flows"))
    import run

    products = {flow["id"]: flow.get("product", "erp") for flow in run.registry()}
    results = json.loads(Path(path).read_text(encoding="utf-8"))["results"]
    return sorted({run.dataset_site(products[r["flow"]], r["dataset"]) for r in results if not r["ok"]})


def main(argv):
    if argv[:1] == ["failed-sites"] and len(argv) == 2:
        print(" ".join(failed_sites(argv[1])))
        return 0
    if argv[:1] == ["decide"]:
        decide()
        return 0
    if argv[:1] == ["summary"] and len(argv) in (2, 3):
        print(summary(*argv[1:]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
