#!/usr/bin/env python3
"""表单排版巡检（11-01 表单排版整改）：在回归实验室里逐页打开「本页表单 / 新建 / 第一行」，电脑宽 1280、手机宽 375 各截一张，
电脑宽另实测每个分栏 Section 的各栏高度和成对字段（名 | 姓、用户名 | 密码）是不是同一行。

    PEARNLY_FLOWS_LAB=<栈名> REGRESSION_LAB_PORT=<口> python3 scripts/regression/form_layout_shots.py --out <截图目录> [--only erp/pearnly-products]

结果：<截图目录>/<产品>-<页>-<哪一屏>-<desktop|mobile>.png 和 <截图目录>/measure-<产品>.json（每屏每个 Section 的栏高、栏里字段、成对字段的行差）。
只读：只点「新建」「第一行」打开表单再关掉，不提交。静态闸门是 scripts/check-form-layout.py，这里是给人看的截图与实测对照。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("flows_run", ROOT / "scripts/flows/run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

PAGES = {
    "erp": ["pearnly-home", "pearnly-company", "pearnly-warehouses", "pearnly-team", "pearnly-tasks", "pearnly-products",
            "pearnly-suppliers", "pearnly-clients", "pearnly-purchase", "pearnly-sales", "pearnly-stock-in", "pearnly-stock-out",
            "pearnly-stock-transfer", "pearnly-stock-card", "pearnly-stock-count", "pearnly-pos-shifts", "pearnly-pos-receipts",
            "pearnly-inventory", "user"],
    "cowork": ["pearnly-home", "pearnly-intake", "pearnly-recon-bank", "pearnly-recon-income", "pearnly-recon-vat",
               "pearnly-stocktake", "pearnly-team", "pearnly-work", "user"],
    "dms": ["pearnly-home", "pearnly-connect", "pearnly-records", "pearnly-roster", "user"],
}
CONTROL = ["pearnly-tenant", "pearnly-feature", "pearnly-target", "pearnly-deployment", "pearnly-line-channel",
           "pearnly-provisioning-request", "pearnly-topup-request", "pearnly-wallet", "pearnly-subscription",
           "pearnly-credit-transaction"]

IDLE = "const idle=async()=>{let q=0;for(let k=0;k<80&&q<3;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};"
# 量哪一块：最上面那个打开的弹窗，否则当前页面的主区
MEASURE = IDLE + r"""
await idle();
// 弹窗是 position: fixed，offsetParent 恒为空：按有没有排版框判可见
const vis = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
const modals = [...document.querySelectorAll('.modal.show')].filter(vis);
const root = modals.length ? modals[modals.length - 1] : (frappe.container.page && frappe.container.page.querySelector('.layout-main-section')) || document.body;
const sections = [];
root.querySelectorAll('.form-section').forEach(sec => {
  if (!vis(sec)) return;
  const cols = [...sec.querySelectorAll('.form-column')].filter(c => c.closest('.form-section') === sec && vis(c));
  const controls = c => [...c.querySelectorAll('.frappe-control')].filter(x => x.closest('.form-column') === c && vis(x));
  const used = cols.filter(c => controls(c).length);
  if (used.length < 2) return;
  const heights = used.map(c => { const cs = controls(c); const top = c.getBoundingClientRect().top;
    return Math.round(Math.max(...cs.map(x => x.getBoundingClientRect().bottom)) - top); });
  sections.push({label: (sec.querySelector('.section-head')?.innerText || '').trim(), heights,
    names: used.map(c => controls(c).map(x => x.dataset.fieldname || x.dataset.fieldtype)),
    width: Math.round(sec.getBoundingClientRect().width)});
});
const pairs = [];
for (const [a, b] of [['first_name','last_name'],['username','password']]) {
  const ea = [...root.querySelectorAll(`.frappe-control[data-fieldname="${a}"]`)].find(vis);
  const eb = [...root.querySelectorAll(`.frappe-control[data-fieldname="${b}"]`)].find(vis);
  if (ea && eb) pairs.push({pair: `${a}|${b}`, dy: Math.round(Math.abs(ea.getBoundingClientRect().top - eb.getBoundingClientRect().top))});
}
const title = modals.length ? (modals[modals.length-1].querySelector('.modal-title')?.innerText || '') : (document.querySelector('.page-head .title-text')?.innerText || '');
return {route: frappe.get_route_str(), title: title.trim(), modal: !!modals.length, sections, pairs};
"""
CLOSE = IDLE + "if (window.cur_dialog) cur_dialog.hide(); document.querySelectorAll('.modal.show').forEach(m => $(m).modal('hide'));" \
    "if (window.__home && frappe.get_route_str() !== window.__home) { frappe.set_route(window.__home); } await idle(); return true;"
OPEN_NEW = IDLE + r"""
window.__home = frappe.get_route_str();
const b = [...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e => e.offsetParent);
if (!b) return {skip: 'no new button'};
const before = frappe.get_route_str(); b.click();
for (let k = 0; k < 40 && !document.querySelector('.modal.show') && frappe.get_route_str() === before; k++) await new Promise(r => setTimeout(r, 250));
await idle(); await new Promise(r => setTimeout(r, 600));
// 采购 / 销售新建先选录入方式：选「手工录入」才出整张单
const manual = document.querySelector('.frappe-control[data-fieldname="entry_manual"] input');
if (manual && manual.offsetParent) { manual.click(); await idle(); await new Promise(r => setTimeout(r, 600)); }
return {opened: !!document.querySelector('.modal.show') || frappe.get_route_str() !== before, label: b.innerText.trim()};
"""
OPEN_ROW = IDLE + r"""
window.__home = frappe.get_route_str();
const scope = frappe.container.page || document;
const link = [...scope.querySelectorAll('.dt-scrollable .dt-row a, .dt-scrollable .dt-cell a, .list-row a, .frappe-list .list-row-container a')].find(e => e.offsetParent);
const cell = link || [...scope.querySelectorAll('.dt-scrollable .dt-row .dt-cell__content')].find(e => e.offsetParent && e.innerText.trim());
if (!cell) return {skip: 'no row'};
const before = frappe.get_route_str(); cell.click();
for (let k = 0; k < 40 && !document.querySelector('.modal.show') && frappe.get_route_str() === before; k++) await new Promise(r => setTimeout(r, 250));
await idle(); await new Promise(r => setTimeout(r, 600));
const opened = !!document.querySelector('.modal.show') || frappe.get_route_str() !== before;
return opened ? {opened: true, label: (cell.innerText || '').trim().slice(0, 40)} : {skip: 'row opens nothing'};
"""


def steps_for(name):
    out = [{"name": "page", "js": "await new Promise(r => setTimeout(r, 1500));" + MEASURE, "shot": True,
            "full": True, "file": f"{name}-page"}]
    for kind, opener in (("new", OPEN_NEW), ("row", OPEN_ROW)):
        out += [{"name": f"open-{kind}", "js": opener},
                {"name": kind, "js": MEASURE, "shot": True, "full": True, "file": f"{name}-{kind}"},
                {"name": f"close-{kind}", "js": CLOSE}]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", action="append", default=[], help="产品/页，例如 erp/pearnly-products、control/pearnly-tenant")
    parser.add_argument("--dataset", default="old")
    parser.add_argument("--viewport", choices=["desktop", "mobile", "both"], default="both")
    parser.add_argument("--product", action="append", default=[], help="只跑这几个产品（erp / cowork / dms / control），可分几路并行")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    lab, measured = run.Lab(), {}
    wanted = set(args.product) or {*PAGES, "control"}
    wanted |= {only.split("/")[0] for only in args.only}
    measure = out / f"measure-{'-'.join(sorted(wanted))}.json"  # 分几路并行时各写各的
    jobs = []
    for product, pages in PAGES.items():
        if product not in wanted:
            continue
        manifest = lab.dataset(args.dataset, product)
        for page in pages:
            jobs.append((product, page, manifest["site"], f"admin@{product}.regression",
                         f"/desk/user/admin@{product}.regression" if page == "user" else f"/desk/{page}"))
    for page in CONTROL if "control" in wanted else []:
        jobs.append(("control", page, "control.regression.localhost", "Administrator", f"/desk/{page}"))
    viewports = ["desktop", "mobile"] if args.viewport == "both" else [args.viewport]
    for product, page, site, user, start in jobs:
        if args.only and f"{product}/{page}" not in args.only:
            continue
        sid = lab.session(site, user)
        try:
            for viewport in viewports:
                name = f"{product}-{page}"
                steps = [dict(s, file=f"{s['file']}-{viewport}") if s.get("file") else s for s in steps_for(name)]
                results = drive(site, sid, start, steps, viewport, out)
                measured[f"{name}-{viewport}"] = {r["step"]: r.get("value") for r in results if r.get("step") in ("page", "new", "row")
                                                  or str(r.get("step", "")).startswith("open-")}
                errors = [r for r in results if r.get("error") or r.get("page_errors")]
                if errors:
                    measured[f"{name}-{viewport}"]["errors"] = errors
                print(name, viewport, json.dumps({k: (v or {}).get("sections") if isinstance(v, dict) else v
                                                  for k, v in measured[f"{name}-{viewport}"].items() if k in ("page", "new", "row")},
                                                 ensure_ascii=False)[:600], flush=True)
        finally:
            lab.drop(site, user, sid)
        measure.write_text(json.dumps(measured, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


def drive(site, sid, start, steps, viewport, shots):
    job = {"base": f"http://{site}:{run.PORT}", "cookie_domain": site, "sid": sid, "start": start,
           "mobile": viewport == "mobile", "width": 375, "shots": str(shots), "steps": steps}
    env = dict(os.environ, SMOKE_CHROME_RULES=f"MAP {site} 127.0.0.1")
    # 自成进程组：卡住时连同它拉起的无头 Chrome 一起结束，这一页记成超时、接着跑下一页
    proc = subprocess.Popen(["node", str(ROOT / "scripts/regression/ui_steps.mjs"), json.dumps(job)], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        return [json.loads(line) for line in out.splitlines() if line.startswith("{")] + [{"error": "timed out after 300s"}]
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


if __name__ == "__main__":
    sys.exit(main())
