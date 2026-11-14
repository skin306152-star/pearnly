"""性能体检用的网页进程（只在任务自有的回归实验室里跑）：同 `pearnly_core.web.wsgi <端口>`，另外按请求记下用时与 SQL 条数。

每个请求一行 JSON 追加到 `PERF_TRACE`（默认 /tmp/perf-trace.jsonl）：路径、接口名、状态、毫秒、查询条数、SQL 毫秒、重复最多的语句形状
及次数（逐行查询的特征）、请求头 `X-Perf-Page`（`pages.mjs` 打开每一页前设成该页路径，用来把接口归到页）。

    cd sites && PEARNLY_REGRESSION_LAB=1 ../env/bin/python /workspace/scripts/perf/trace_server.py 8000
"""
import json
import os
import sys
import threading
import time
from collections import Counter

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import frappe.app  # noqa: E402
from frappe.database.database import Database  # noqa: E402

from measure import shape  # noqa: E402
from pearnly_core.web import wsgi  # noqa: E402,F401  装好 multipart，同生产入口

TRACE = os.environ.get("PERF_TRACE", "/tmp/perf-trace.jsonl")
state = threading.local()
lock = threading.Lock()
original_sql = Database.sql


def traced_sql(self, query, *args, **kwargs):
    log = getattr(state, "log", None)
    if log is None:
        return original_sql(self, query, *args, **kwargs)
    started = time.perf_counter()
    try:
        return original_sql(self, query, *args, **kwargs)
    finally:
        log.append((shape(query), (time.perf_counter() - started) * 1000))


Database.sql = traced_sql
inner = frappe.app.application


def application(environ, start_response):
    path = environ.get("PATH_INFO", "")
    if path.startswith(("/assets", "/files", "/socket.io")):
        return inner(environ, start_response)
    state.log = []
    status = {}

    def capture(code, headers, *rest):
        status["code"] = code.split()[0]
        return start_response(code, headers, *rest)

    started = time.perf_counter()
    try:
        body = list(inner(environ, capture))
    finally:
        ms = (time.perf_counter() - started) * 1000
        log, state.log = state.log, None
        shapes = Counter(text for text, _ in log)
        top = shapes.most_common(1)
        row = {"t": time.time(), "page": environ.get("HTTP_X_PERF_PAGE", ""), "host": environ.get("HTTP_HOST", "").split(":")[0],
               "path": path, "cmd": path.rsplit("/", 1)[-1] if path.startswith("/api/method/") else path,
               "status": status.get("code"), "ms": round(ms), "queries": len(log), "sql_ms": round(sum(m for _, m in log)),
               "top_n": top[0][1] if top else 0, "top_sql": top[0][0][:160] if top else ""}
        with lock, open(TRACE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return body


frappe.app.application = application

if __name__ == "__main__":
    frappe.app.serve(port=int(sys.argv[1]) if len(sys.argv) > 1 else 8000, no_reload=True, sites_path=".")
