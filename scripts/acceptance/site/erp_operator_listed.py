"""验收场景的站点侧脚本（在测试租户 / Mac 实验栈的 ERP 网页容器里跑）：代录任务列出一张「排队中、还没交进写账」的单 → 等每分钟那一轮
（`express.push.poll`）把它直写进账本 → 再看代录这边怎么说：资料清单（`express_operator.api.documents(task=…)`）、给模型的准备中清单
（`express_operator.tools._preparing`）、写入计划取单（`express_operator.api.waiting(task=…)`）、交单（`hand_in`）。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_operator_listed.py <站点> '<JSON 参数>'

参数：user（代录任务的主人）、company、doc_id（``S:<单据>`` / ``P:<单据>``）、doctype、doc、wait（等直写最多几秒，默认 240）。
主会话 11-24 口径：列出阶段不锁；列出过的单被直写写掉后如实显示「已入账 · 单号」、开始时跳过，不凭空消失、不重复入账。
任务用完删掉（连它的对话）。只在测试租户 / Mac 栈跑。
"""
import base64
import json
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
task = ""
try:
    from pearnly_core.execution.replay import TEST_SITES

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    frappe.set_user(params["user"])
    from pearnly_core.express_operator import api, tasks, tools

    company, doc_id, doctype, doc = params["company"], params["doc_id"], params["doctype"], params["doc"]
    period = time.strftime("%Y-%m")
    task = tasks.create(company, period)["name"]
    frappe.db.commit()

    # 列出（资料清单第一页）：这张在现查里、记进任务的快照
    first = api.documents(company, period, task=task)
    result["listed"] = [row.get("doc_id") for row in first.get("documents") or []]
    result["listed_entered"] = first.get("entered") or []

    # 等每分钟那一轮把它直写掉（不替产品去写：这里只读）
    deadline, state = time.monotonic() + int(params.get("wait", 240)), {}
    while True:
        frappe.db.rollback()
        state = frappe.db.get_value(doctype, doc, ["ledger_state", "ledger_docnum", "ledger_job"], as_dict=True) or {}
        if state.get("ledger_state") in ("written", "mismatch") or time.monotonic() > deadline:
            break
        time.sleep(5)
    result["state"] = state
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["request_key", "status"], as_dict=True) if state.get("ledger_job") else None
    result["job"] = job

    # 写掉之后代录这边怎么说
    after = api.documents(company, period, task=task)
    result["after_listed"] = [row.get("doc_id") for row in after.get("documents") or []]
    result["after_entered"] = after.get("entered") or []
    view = tasks.read(task)
    prep = tools._preparing(view)
    result["model_waiting"] = [row.get("doc_id") for row in prep.get("waiting") or []]
    result["model_entered"] = prep.get("entered") or []
    planned, _groups, skipped = api.waiting(company, period, [], task=task)
    result["planned"], result["skipped"] = planned, skipped
    # 交单：写入计划要是还带着它，开始时也不交（服务端按清单定）
    try:
        started = api.hand_in(company, task, [doc_id])
        result["hand_in"] = started
    except Exception as refused:  # noqa: BLE001 —— 没有可交的单就是被拒（同「没有要处理的单据」）
        frappe.db.rollback()
        result["hand_in_refused"] = str(refused)[:200]
    result["jobs"] = frappe.db.count("Pearnly CC Job", {"source_doctype": doctype, "source_name": doc})
    result["ok"] = True
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    if task:
        try:
            from pearnly_core.express_operator import tasks as _tasks

            _tasks.delete(task)
            frappe.db.commit()
        except Exception:  # noqa: BLE001
            frappe.db.rollback()
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
