"""接口打开时限冒烟（在实验栈站点网页容器里跑）：以某个员工身份调一个白名单读接口，量服务端用时，超过时限算不通过。
页面探针只量默认打开；按数量排序这类要点列头才走的慢路径用它拦（验收 10-25：收发存按期末数量排序 4.8 秒，页面冒烟拦不住）。

    env/bin/python /tmp/smoke-api_time.py <站点> '<JSON 参数>'   # 末行打印 SMOKE {"ok": ..., ...}

参数（登记表入口的 args）：method = 白名单读接口的点分路径；kwargs = 参数；user = 用谁的身份（默认冒烟账号）；
company = ERP 先把这个员工的当前公司切过去（大账套），跑完还原；limit_ms = 时限（默认 3000）；runs = 跑几次取最慢（默认 2，第一次算预热不计）。
只读：只调读接口；切公司只改这个员工的用户默认值并在收尾还原。只给测试租户用。
"""
import base64
import json
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]  # run.py 传参用 base64

site = sys.argv[1]
params = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
previous = None
try:
    from pearnly_core.execution.replay import TEST_SITES

    if not TEST_SITES.fullmatch(site):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    user = params.get("user") or "Administrator"
    company = params.get("company")
    if company:
        previous = frappe.defaults.get_user_default("company", user) or ""
        frappe.defaults.set_user_default("company", company, user)
        frappe.db.commit()
    frappe.set_user(user)
    method = frappe.get_attr(params["method"])
    kwargs = params.get("kwargs") or {}
    runs, timings, answer = max(int(params.get("runs", 2)), 1), [], None
    for _ in range(runs):
        started = time.perf_counter()
        answer = method(**kwargs)
        timings.append(round((time.perf_counter() - started) * 1000))
        frappe.db.rollback()
    counted = timings[1:] or timings
    limit = int(params.get("limit_ms", 3000))
    total = answer.get("total") if isinstance(answer, dict) else (len(answer) if isinstance(answer, list) else None)
    result.update(ms=max(counted), timings=timings, total=total, limit_ms=limit)
    result["summary"] = f"{params['method'].rsplit('.', 1)[-1]} {max(counted)} 毫秒（时限 {limit}，共 {total} 行）"
    result["ok"] = max(counted) <= limit
except SystemExit as stop:
    result["why"] = str(stop)
finally:
    if previous is not None:
        frappe.set_user("Administrator")
        frappe.defaults.set_user_default("company", previous or None, params.get("user") or "Administrator")
        frappe.db.commit()
    frappe.destroy()
print("SMOKE " + json.dumps(result, ensure_ascii=False, default=str))
