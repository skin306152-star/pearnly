#!/usr/bin/env python3
"""流程用例（主会话 10-28 第 3 道闸）：按登记表 `tests/flows/registry.toml` 在回归实验室的两套固定数据上跑流程，断言页面结果与落库。

    python3 scripts/flows/run.py --all
    python3 scripts/flows/run.py --flow erp.stocktake.scan
    python3 scripts/flows/run.py --changed --base origin/main      # 只跑改动文件涉及的流程
    ... --out 结果.json --post-status                                # 结果作为提交状态 `pearnly/flows` 回写 GitHub

分支：`--changed --post-status` 只跑改动文件涉及的流程；部署前：在要部署的 main 提交上 `--all --post-status` 跑全套，
`scripts/production/deploy.sh` 只认全套通过（说明以 `all flows` 开头）。回写前核对实验室挂的就是本工作树的源码、产品代码没有未提交改动。
本机睡眠或实验室没起来时没有这个状态，部署闸照样拦；补跑方法见 `tests/flows/README.md`。

每个流程 × 每套数据（datasets.py new / old，先跑一遍保证数据齐，拿清单）× 每个宽度（desktop / mobile）：
建会话 → 流程模块 `prepare`（在站点里开单等）→ 无头 Chrome 按 `steps` 点页面（`scripts/regression/ui_steps.mjs`，手机宽度、假摄像头）
→ `judge`（页面上看到的）→ `verify`（库里落的）→ 页面不能有报错、站点 Error Log 不能多出前端回传。全部通过才算过。
实验室：`infrastructure/compose/regression-lab.yml`（站点由 `scripts/regression/lab_init.py` 建，数据由 `datasets.py` 填）。
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "tests" / "flows" / "registry.toml"
STATUS = "pearnly/flows"
# 实验栈名（docker compose -p）：每个窗口用自己的栈跑、回写同一个 pearnly/flows 状态，不抢一套（主会话 10-29）
LAB = os.environ.get("PEARNLY_FLOWS_LAB", "pearnly-regression-lab")
CONTAINER = os.environ.get("REGRESSION_LAB_CONTAINER", f"{LAB}-bench-1")
PORT = int(os.environ.get("REGRESSION_LAB_PORT", "18771"))


def registry() -> list[dict]:
    return tomllib.loads(REGISTRY.read_text(encoding="utf-8"))["flow"]


def merge_base(base: str) -> str:
    found = subprocess.run(["git", "merge-base", base, "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if not found:
        raise SystemExit(f"找不到 {base} 与 HEAD 的分叉点")
    return found


def changed_files(base: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", base, "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def covers(flow, path) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("**/", ""))
               for pattern in flow.get("covers", []))


SPA = [(ROOT / "frontend/assistant", ROOT / "apps/pearnly_core/pearnly_core/public/assistant/index.html")]


def spa_fresh():
    """前端单页的构建产物不进 git、实时源码也盖不到：产物要比源码新，否则 CC 对话页测的不是这个提交。"""
    for source, built in SPA:
        newest = max((f.stat().st_mtime for f in source.rglob("*") if f.is_file() and "node_modules" not in f.parts
                      and f.suffix in (".ts", ".tsx", ".css", ".html", ".json")), default=0)
        if not built.exists() or built.stat().st_mtime < newest:
            raise SystemExit(f"{built.relative_to(ROOT)} 比 {source.relative_to(ROOT)} 的源码旧：先在 {source.relative_to(ROOT)} 里 npm run build 再回写状态")


def lab_runs_this_tree():
    """回写状态前：实验室挂的产品源码就是本工作树，且产品代码没有未提交改动（结果才算这个提交的）。"""
    mounts = subprocess.run(["docker", "inspect", CONTAINER, "--format", "{{range .Mounts}}{{.Source}}\n{{end}}"],
                            capture_output=True, text=True).stdout.split()
    apps = [m for m in mounts if "/apps/" in m]
    if not apps or not all(m.startswith(str(ROOT / "apps") + "/") for m in apps):
        raise SystemExit(f"实验室挂的不是本工作树的源码（{apps[:1]}），用 PEARNLY_REGRESSION_SRC={ROOT} 重建实验室后再回写状态")
    dirty = subprocess.run(["git", "status", "--porcelain", "apps", "frontend"], cwd=ROOT, capture_output=True, text=True).stdout
    if dirty.strip():
        raise SystemExit("产品代码有未提交改动，结果不代表这个提交，不回写状态：\n" + dirty[:600])


class Lab:
    """回归实验室容器：在站点里跑一段 Python（已 init / connect，结尾 commit），回它最后一行打印的 JSON。"""

    def __init__(self):
        self.manifests = {}  # 每套数据一次运行只补齐一遍

    def exec(self, *args, timeout=900):
        return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", CONTAINER, *args],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)

    def python(self, site, body):
        code = f"import frappe, json\nfrappe.init(site={site!r}, sites_path='.')\nfrappe.connect()\ntry:\n" + \
            "\n".join("    " + line for line in body.strip().splitlines()) + "\nfinally:\n    frappe.destroy()\n"
        result = self.exec("../env/bin/python", "-c", code)
        lines = [line for line in result.stdout.splitlines() if line.startswith(("{", "["))]
        if result.returncode or not lines:
            raise RuntimeError(f"lab python failed on {site}: {result.stderr[-800:]}")
        return json.loads(lines[-1])

    def dataset(self, which, product="erp"):
        """这套数据的清单（每个产品各两套：`datasets.py new|old [erp|cowork]`）。"""
        key = (product, which)
        if key in self.manifests:
            return self.manifests[key]
        result = self.exec("../env/bin/python", "/workspace/scripts/regression/datasets.py", which, product)
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if result.returncode or not lines:
            raise RuntimeError(f"dataset {product} {which} failed: {result.stderr[-800:]}")
        self.manifests[key] = json.loads(lines[-1])
        return self.manifests[key]

    def session(self, site, user):
        out = self.exec("../env/bin/python", "/workspace/scripts/smoke/session.py", site, user).stdout
        return next((line.split()[1] for line in out.splitlines() if line.startswith("SID ")), "")

    def drop(self, site, user, sid):
        self.exec("../env/bin/python", "/workspace/scripts/smoke/session.py", site, user, "drop", sid)

    def restart(self):
        """重启 bench 容器再等网页起来：网页进程用 --noreload 起，已导入的模块一直留在内存里，换了源码不重启就是新旧混跑
        （10-30 变基后 terminals.py 新、warehouses 还是旧的，报找不到 main_of）。"""
        subprocess.run(["docker", "restart", CONTAINER], capture_output=True, check=True, timeout=300)
        for _ in range(120):
            ping = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-H", "Host: erp.regression.localhost",
                                   f"http://127.0.0.1:{PORT}/api/method/ping"], capture_output=True, text=True)
            if ping.stdout == "200":
                return
            time.sleep(2)
        raise SystemExit("实验室网页重启后没起来")

    def migrate(self):
        """全部站点迁移到挂上来的源码（表结构、补丁、hooks）：站点比源码旧时流程结果不代表这个提交。"""
        result = subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench", CONTAINER, "bench", "--site", "all", "migrate"],
                                capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise SystemExit(f"实验室站点迁移失败：{result.stderr[-800:]}")

    def client_errors(self, site):
        return self.python(site, 'print(json.dumps({"n": frappe.db.count("Error Log", {"method": ["like", "Browser error%"]})}))')["n"]


def load_module(path):
    # 流程模块可以用同目录的共用片段（`desk_list.py`）
    if str(ROOT / "scripts" / "flows") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts" / "flows"))
    spec = importlib.util.spec_from_file_location(Path(path).stem, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def drive(site, sid, start, steps, mobile, video, ready=None):
    job = {"base": f"http://{site}:{PORT}", "cookie_domain": site, "sid": sid, "start": start, "mobile": mobile,
           "video": video or "", "steps": steps, **({"ready": ready} if ready else {})}
    env = dict(os.environ, SMOKE_CHROME_RULES=f"MAP {site} 127.0.0.1")
    # 自成进程组：超时连同它拉起的无头 Chrome 一起结束，不留残留进程
    proc = subprocess.Popen(["node", str(ROOT / "scripts/regression/ui_steps.mjs"), json.dumps(job)], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise RuntimeError("page steps timed out after 900s")
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


_VIDEO_LOCK = threading.Lock()  # 并行时两格可能要同一个码的视频：只生成一次


def barcode_video(code):
    spec = importlib.util.spec_from_file_location("make_barcode_video", ROOT / "scripts/smoke/make_barcode_video.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = Path(tempfile.gettempdir()) / f"flow-{code}.y4m"
    with _VIDEO_LOCK:
        if not path.exists():
            module.write(str(path), code)
    return str(path)


def run_flow(lab, flow, dataset, viewport):
    module = load_module(flow["module"])
    product = flow.get("product", "erp")
    manifest = lab.dataset(dataset, product)
    site, user = manifest["site"], f"{flow.get('user', 'admin')}@{product}.regression"
    before = lab.client_errors(site)
    params = module.prepare(lab, manifest)
    started = time.monotonic()
    runs = [(None, None)]
    camera = flow.get("camera") and next((c for c in params.get("cases", []) if c["key"] == "by_barcode"), None)
    if camera and viewport == "mobile":
        params_camera = module.prepare(lab, manifest)
        runs.append((params_camera, camera["scan"]))
    checks, page_errors = [], []
    for run_params, code in runs:
        run_params = run_params or params
        sid = lab.session(site, user)
        try:
            start = flow["start"].format(**run_params)
            results = drive(site, sid, start, module.steps(run_params, code), viewport == "mobile",
                            barcode_video(code) if code else None, getattr(module, "READY", None))
        finally:
            lab.drop(site, user, sid)
        # 页面报错、步骤本身出错、步骤自己判出的错（返回 {error}）都算
        page_errors += [e for r in results for e in (r.get("page_errors") or []) + ([r["error"]] if r.get("error") else [])
                        + ([f"{r.get('step')}: {r['value']['error']}"] if isinstance(r.get("value"), dict) and r["value"].get("error") else [])]
        label = "camera" if code else "typed"
        checks += [dict(c, run=label) for c in module.judge(run_params, results, code)]
        checks += [dict(c, run=label, saved_check=True) for c in module.verify(lab, manifest, run_params, code)]
    after = lab.client_errors(site)
    checks.append({"case": "page errors", "seen": page_errors, "ok": not page_errors})
    checks.append({"case": "client errors in Error Log", "seen": after - before, "ok": after == before})
    return {"flow": flow["id"], "dataset": dataset, "viewport": viewport, "ok": all(c["ok"] for c in checks),
            "seconds": round(time.monotonic() - started, 1), "checks": checks}


def post_status(ok, summary):
    """回写 GitHub 提交状态 `flows`；说明里带比较基线，部署闸据此确认覆盖到上次上线以来的全部改动。"""
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    subprocess.run(["gh", "api", f"repos/{{owner}}/{{repo}}/statuses/{sha}", "-f", f"state={'success' if ok else 'failure'}",
                    "-f", f"context={STATUS}", "-f", f"description={summary[:140]}"], cwd=ROOT, check=True, capture_output=True)
    return sha


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--flow", action="append", default=[])
    parser.add_argument("--changed", action="store_true")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--dataset", choices=["new", "old"], help="只跑这套数据（调试用）")
    parser.add_argument("--viewport", choices=["desktop", "mobile"], help="只跑这个宽度（调试用）")
    parser.add_argument("--out")
    parser.add_argument("--post-status", action="store_true")
    parser.add_argument("--migrate", action="store_true", help="先重启实验室网页进程、把全部站点迁移到挂上来的源码（--post-status 时总是做）")
    parser.add_argument("--jobs", type=int, default=1, help="同时跑几个站点（同一站点的格依次跑；实验室 bench 有 3 个网页进程，建议不超过 3）")
    args = parser.parse_args(argv)
    flows = registry()
    if args.post_status:
        if args.flow or args.dataset or args.viewport:
            parser.error("--post-status 只配 --changed 或 --all（部分流程的结果不能代表这个提交）")
        lab_runs_this_tree()
        spa_fresh()
    scope = "all flows"
    if args.dataset or args.viewport:
        scope = "partial"
    if args.flow:
        scope = "flows " + ",".join(args.flow) + (" partial" if scope == "partial" else "")
        flows = [f for f in flows if f["id"] in args.flow]
    elif args.changed:
        base = merge_base(args.base)
        scope = f"base {base[:12]}"
        files = changed_files(base)
        flows = [f for f in flows if any(covers(f, path) for path in files)]
    elif not args.all:
        parser.error("--all, --flow or --changed")
    lab, results, started = Lab(), [], time.monotonic()
    if flows and (args.migrate or args.post_status):
        lab.restart()
        lab.migrate()
        lab.restart()  # 迁移后再起一次：迁移里导入的模块与缓存不带进流程
        print(f"实验室站点已迁移（{round(time.monotonic() - started)}s）", flush=True)
    cells = [(flow, dataset, viewport) for flow in flows
             for dataset in [d for d in flow.get("datasets", ["new", "old"]) if not args.dataset or d == args.dataset]
             for viewport in [v for v in flow.get("viewports", ["desktop", "mobile"]) if not args.viewport or v == args.viewport]]
    # 各套数据先串行补齐（datasets.py 不并发跑），之后各格可以并行
    for product, dataset in sorted({(flow.get("product", "erp"), dataset) for flow, dataset, _ in cells}):
        lab.dataset(dataset, product)
    printing = threading.Lock()

    def one(cell):
        flow, dataset, viewport = cell
        try:
            result = run_flow(lab, flow, dataset, viewport)
        except Exception as error:  # noqa: BLE001 - 一格出错记不通过，其余照跑
            result = {"flow": flow["id"], "dataset": dataset, "viewport": viewport, "ok": False, "error": str(error)[:600],
                      "checks": []}
        bad = [c for c in result["checks"] if not c["ok"]]
        with printing:
            print(f"{'通过' if result['ok'] else '不通过'} {flow['id']} [{dataset} / {viewport}] {result.get('seconds', '')}s "
                  f"{result.get('error', '')} {json.dumps(bad, ensure_ascii=False)[:600] if bad else ''}", flush=True)
        return result

    # 按站点并行：同一站点的格依次跑（编号序列、同一岗位账号、按站点数的 Error Log 都不互相干扰），不同站点之间同时跑
    groups = {}
    for cell in cells:
        groups.setdefault((cell[0].get("product", "erp"), cell[1]), []).append(cell)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        done = dict(zip(groups, pool.map(lambda group: [one(cell) for cell in group], groups.values())))
    results = [done[key][group.index(cell)] for key, group in groups.items() for cell in group]
    results.sort(key=lambda r: cells.index(next(c for c in cells if c[0]["id"] == r["flow"] and c[1] == r["dataset"] and c[2] == r["viewport"])))
    ok = all(r["ok"] for r in results)  # 没有涉及的流程 = 通过（覆盖闸另管改到的入口有没有登记）
    datasets = ",".join(sorted({r["dataset"] for r in results})) or "-"
    summary = (f"{scope}; {len(flows)} flows; datasets {datasets}; {sum(r['ok'] for r in results)}/{len(results)} runs passed; "
               f"{round(time.monotonic() - started)}s")
    print(("流程用例：通过 " if ok else "流程用例：不通过 ") + summary)
    if args.out:
        Path(args.out).write_text(json.dumps({"ok": ok, "summary": summary, "results": results}, ensure_ascii=False, indent=1))
    if args.post_status:
        print("status posted for", post_status(ok, summary))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
