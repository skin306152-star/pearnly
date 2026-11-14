"""并发核对：新建大盘点（抄几万行快照）期间，同站点另一个连接新建一张同样用全站共用编号计数器的档，不该被卡住（只在任务自有的回归实验室跑）。

起因（拼车窗口 11-14）：逐条发号会把 ``tabSeries`` 里所有 ``format:XXX-{#####}`` 共用的那一行计数器锁到请求提交；新建盘点改批量写后
编号一次预留、预留完立刻提交（`express.bulk.reserve`），写几万行期间计数器不再被占着。

两个进程：A 以管理员在 ``--warehouse`` 新建盘点（做完回滚，编号段已提交、只留空号）；A 开始后 ``--delay`` 秒，B 新建一个客户
（``CUS-{#####}``，同一个计数器）并量用时，做完回滚。先单独跑一次 B 作基准（新进程本身就要一秒多），并发时 B 比基准多等超过
``--slack`` 秒就退出 1。末行 ``CONCURRENCY {json}``。

    ../env/bin/python /workspace/scripts/perf/count_create_concurrency.py <站点> --warehouse <仓> [--delay 1] [--slack 0.5]
"""
import argparse
import json
import os
import subprocess
import sys
import time

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

ADMIN = "admin@erp.regression"


def connect(site):
    import frappe

    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    frappe.set_user(ADMIN)
    return frappe


def role_count(site, warehouse):
    frappe = connect(site)
    from pearnly_erp_portal.inventory import count
    from pearnly_erp_portal.masters import company

    before = frappe.defaults.get_user_default("company", ADMIN)
    company.set_active_company(frappe.db.get_value("ERP Warehouse", warehouse, "company"))
    print("STARTED", flush=True)
    started = time.perf_counter()
    try:
        made = count.create_count(title="concurrency check", warehouse=warehouse)
    finally:
        seconds = round(time.perf_counter() - started, 2)
        frappe.db.rollback()
        # 预留编号会提交（连同上面切的当前公司）：还原
        frappe.defaults.set_user_default("company", before, ADMIN)
        frappe.db.commit()
    print("RESULT " + json.dumps({"rows": made["rows"], "seconds": seconds}), flush=True)


def role_other(site):
    frappe = connect(site)
    from pearnly_erp_portal.masters import customers

    started = time.perf_counter()
    made = customers.create_customer(customer_name=f"concurrency {time.time():.0f}", tax_id="0105556000459")
    seconds = round(time.perf_counter() - started, 2)
    frappe.db.rollback()
    print("RESULT " + json.dumps({"customer": made["name"], "seconds": seconds}), flush=True)


def result(proc):
    out, _ = proc.communicate(timeout=1800)
    line = next((l for l in out.splitlines() if l.startswith("RESULT ")), None)
    if not line:
        raise SystemExit(f"child failed: {out[-800:]}")
    return json.loads(line[7:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--slack", type=float, default=0.5, help="并发时比单独跑多等的上限（秒）")
    ap.add_argument("--role", choices=("count", "other"))
    a = ap.parse_args()
    if a.role == "count":
        return role_count(a.site, a.warehouse)
    if a.role == "other":
        return role_other(a.site)
    me = [sys.executable, os.path.abspath(__file__), a.site, "--warehouse", a.warehouse]
    alone = result(subprocess.Popen(me + ["--role", "other"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
    first = subprocess.Popen(me + ["--role", "count"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in first.stdout:  # 等 A 真正开始建
        if line.startswith("STARTED"):
            break
    time.sleep(a.delay)
    second = subprocess.Popen(me + ["--role", "other"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    other, made = result(second), result(first)
    ok = other["seconds"] <= alone["seconds"] + a.slack and made["seconds"] > a.delay + other["seconds"]
    print("CONCURRENCY " + json.dumps({"count": made, "other": other, "other_alone": alone, "slack": a.slack, "ok": ok,
                                       "note": "other 在 count 写入期间完成" if made["seconds"] > a.delay + other["seconds"] else
                                               "count 结束得比预期早，没压到并发"}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
