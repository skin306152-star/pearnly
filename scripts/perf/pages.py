#!/usr/bin/env python3
"""性能体检：在本机回归实验室逐页打开产品页面，出「页面可用用时 + 首屏各接口的服务端用时与查询条数」表，标出超标的。

前提：实验栈 `pearnly-perf-lab`（或 PERF_LAB_CONTAINER 指定的栈）的网页进程换成 `trace_server.py`（按请求记查询条数），
大账套用 `seed_big_company.py` 造好。页面清单默认取产品地图（`scripts/impact/product-map/`）里该产品的 Desk 页。

    python3 scripts/perf/pages.py --product erp --user admin@erp.regression --company CO-00001 --out /tmp/perf-erp.jsonl
    python3 scripts/perf/pages.py --product erp ... --page /desk/pearnly-stock-count/STC-00005   # 另加的地址
    python3 scripts/perf/pages.py ... --budget scripts/perf/budgets.json   # 按上限表核，超了退出 1

指标（11-13 主会话定）：列表首屏接口 < 300 毫秒、页面 2 秒内可用；超了标 ✗。只读：只打开页面，不点按钮；会话跑完即删。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTAINER = os.environ.get("PERF_LAB_CONTAINER", "pearnly-perf-lab-bench-1")
PORTS = {"erp": 18871, "cowork": 18872, "dms": 18873}
TRACE = "/tmp/perf-trace.jsonl"
API_LIMIT_MS, PAGE_LIMIT_MS = 300, 2000


def over_budget(rows, budget):
    """按上限表核：页面可用用时、每个接口的服务端毫秒与查询条数。接口按点分名末段对上表里的键。"""
    page_ms = budget.get("page_ms", PAGE_LIMIT_MS)
    api_ms = budget.get("api_ms", API_LIMIT_MS)
    apis = budget.get("apis", {})
    problems = []
    for row in rows:
        if row["ready_ms"] is None and row.get("final") and not row["final"].startswith(row["path"]):
            continue  # 这个账号打开会被换到别的页（如 /desk/user → 首页），不是慢；接口照样核
        if row["ready_ms"] is None or row["ready_ms"] > page_ms:
            problems.append(f"{row['path']}: 页面 {row['ready_ms']} ms（上限 {page_ms}）")
        for call in row["server"]:
            limit = apis.get(call["cmd"].rsplit(".", 1)[-1], {})
            if call["ms"] > limit.get("ms", api_ms):
                problems.append(f"{row['path']}: {call['cmd']} {call['ms']} ms（上限 {limit.get('ms', api_ms)}）")
            if "queries" in limit and call["queries"] > limit["queries"]:
                problems.append(f"{row['path']}: {call['cmd']} {call['queries']} 条查询（上限 {limit['queries']}，"
                                f"同一语句 {call['top_n']} 次：{call['top_sql'][:80]}）")
    return problems


def lab(*args, timeout=300):
    return subprocess.run(["docker", "exec", "-w", "/home/frappe/frappe-bench/sites", CONTAINER, *args],
                          capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL).stdout


def map_pages(product):
    spec = importlib.util.spec_from_file_location("smoke_run", ROOT / "scripts" / "smoke" / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    _parts, entries, _errors = run.impact.load()
    urls = [e.url for e in entries.values()
            if getattr(e, "url", "") and (e.site or e.product.lower()) == product and e.url.startswith("/desk")]
    return list(dict.fromkeys(urls))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", required=True, choices=sorted(PORTS))
    ap.add_argument("--user", required=True)
    ap.add_argument("--company", help="ERP：先把账号当前公司切到这家（大账套），收尾还原")
    ap.add_argument("--page", action="append", default=[])
    ap.add_argument("--pages-only", action="store_true")
    ap.add_argument("--out", required=True)
    ap.add_argument("--budget", help="上限表（scripts/perf/budgets.json）：超了退出 1；不给只出表")
    a = ap.parse_args(argv)
    site = f"{a.product}.regression.localhost"
    pages = ([] if a.pages_only else map_pages(a.product)) + a.page
    session = ["../env/bin/python", "/workspace/scripts/smoke/session.py", site, a.user]
    out = lab(*session, *(["company", a.company] if a.company else []))
    sid = next((line.split()[1] for line in out.splitlines() if line.startswith("SID ")), "")
    prev = next((line.split()[1] for line in out.splitlines() if line.startswith("PREV ")), None)
    if not sid:
        sys.exit(f"建会话失败：{out[-300:]}")
    lab("sh", "-c", f": > {TRACE}")
    try:
        env = dict(os.environ, SMOKE_CHROME_RULES=f"MAP {site} 127.0.0.1")
        job = {"base": f"http://{site}:{PORTS[a.product]}", "pages": pages, "cookie_domain": site, "sid": sid}
        proc = subprocess.run(["node", str(HERE / "pages.mjs"), json.dumps(job)], capture_output=True, text=True,
                              timeout=3600, env=env)
    finally:
        lab(*session, "drop", sid, *([prev] if prev else []))
    trace = [json.loads(line) for line in lab("cat", TRACE).splitlines() if line.startswith("{")]
    rows = []
    for line in proc.stdout.splitlines():
        if not line.startswith("{"):
            continue
        page = json.loads(line)
        if "path" not in page:
            print(page, file=sys.stderr)
            continue
        server = [t for t in trace if t["page"] == page["path"] and t["path"].startswith("/api/")]
        page["server"] = server
        page["slow_api"] = [s for s in server if s["ms"] > API_LIMIT_MS]
        page["over"] = (page["ready_ms"] is None or page["ready_ms"] > PAGE_LIMIT_MS) or bool(page["slow_api"])
        rows.append(page)
    with open(a.out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    for row in rows:
        worst = max(row["server"], key=lambda s: s["ms"], default=None)
        heavy = max(row["server"], key=lambda s: s["queries"], default=None)
        mark = "✗" if row["over"] else "✓"
        if row["ready_ms"] is None and row.get("final") and not row["final"].startswith(row["path"]):
            mark = "→"
        print(f"{mark} {row['path'][:48]:48} 可用 {row['ready_ms']} ms{' 换到 ' + row['final'] if mark == '→' else ''} | 接口 {len(row['server'])} 个，"
              f"最慢 {worst['cmd'][-40:] if worst else '-'} {worst['ms'] if worst else 0} ms / "
              f"查询最多 {heavy['cmd'][-40:] if heavy else '-'} {heavy['queries'] if heavy else 0} 条（同形状 {heavy['top_n'] if heavy else 0}）")
    if a.budget:
        problems = over_budget(rows, json.loads(Path(a.budget).read_text(encoding="utf-8")))
        for line in problems:
            print("超标 " + line)
        print(f"上限表：{'不通过' if problems else '通过'}（{len(rows)} 页）")
        return 1 if problems or not rows else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
