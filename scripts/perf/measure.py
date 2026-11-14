"""量一个白名单读接口的服务端用时与查询条数（在实验栈网页容器里跑，只读：每次调用后回滚）。

记下每条 SQL（`frappe.db.sql` 是框架所有查询的出口，`frappe.qb` 也经它），按「去掉参数后的语句形状」归并，
打印：总用时、总条数、重复最多的形状（逐行查询的特征是同一形状出现几百次）和最慢的几条。

    ../env/bin/python /workspace/scripts/perf/measure.py <站点> '<JSON>'
JSON：{"method": "点分路径", "kwargs": {...}, "user": "…", "company": "CO-…"（先切当前公司，跑完还原）, "runs": 3}
末行 `PERF {json}`；用时取第二次起最慢（第一次算预热）。
"""
import json
import re
import sys
import time
from collections import defaultdict

import frappe

SHAPE = re.compile(r"('(?:[^'\\]|\\.)*'|\b\d+(?:\.\d+)?\b)")


def shape(query):
    text = SHAPE.sub("?", " ".join(str(query).split()))
    return re.sub(r"\(\?(?:, ?\?)+\)", "(?…)", text)[:220]


def measure(method, kwargs, runs=3):
    db = frappe.db
    original = db.sql
    log = []

    def traced(query, *args, **kw):
        started = time.perf_counter()
        try:
            return original(query, *args, **kw)
        finally:
            log.append((shape(query), (time.perf_counter() - started) * 1000))

    fn = frappe.get_attr(method)
    timings, counts, answer = [], [], None
    db.sql = traced
    try:
        for _ in range(max(runs, 1)):
            log.clear()
            frappe.local.cache = {}
            started = time.perf_counter()
            answer = fn(**kwargs)
            timings.append(round((time.perf_counter() - started) * 1000))
            counts.append(len(log))
            frappe.db.rollback()
    finally:
        db.sql = original
    groups = defaultdict(lambda: [0, 0.0])
    for text, ms in log:
        groups[text][0] += 1
        groups[text][1] += ms
    repeated = sorted(groups.items(), key=lambda kv: -kv[1][0])[:6]
    slowest = sorted(log, key=lambda r: -r[1])[:5]
    size = None
    if isinstance(answer, list):
        size = len(answer)
    elif isinstance(answer, dict):
        size = {k: len(v) for k, v in answer.items() if isinstance(v, list)} or answer.get("total")
    counted = timings[1:] or timings
    return {"method": method.rsplit(".", 1)[-1], "ms": max(counted), "timings": timings, "queries": counts[-1],
            "sql_ms": round(sum(ms for _, ms in log)), "size": size,
            "repeated": [{"n": n, "ms": round(ms), "sql": text} for text, (n, ms) in repeated],
            "slowest": [{"ms": round(ms, 1), "sql": text} for text, ms in slowest]}


def main():
    site, params = sys.argv[1], json.loads(sys.argv[2])
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    user = params.get("user") or "Administrator"
    previous = None
    try:
        if params.get("company"):
            previous = frappe.defaults.get_user_default("company", user) or ""
            frappe.defaults.set_user_default("company", params["company"], user)
            frappe.db.commit()
        frappe.set_user(user)
        print("PERF " + json.dumps(measure(params["method"], params.get("kwargs") or {}, int(params.get("runs", 3))),
                                   ensure_ascii=False, default=str))
    finally:
        if previous is not None:
            frappe.set_user("Administrator")
            frappe.defaults.set_user_default("company", previous or None, user)
            frappe.db.commit()
        frappe.destroy()


if __name__ == "__main__":
    main()
