"""验收场景的站点侧脚本（只读，在测试租户的 ERP 网页容器里跑）：等一张采购 / 销售单的写账有结果，把它此刻的账本栏、最后一件写账、
落点账套、客户挂上的 Express 编码交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_doc_state.py <站点> '<JSON 参数>'

参数：doctype（Purchase Document / ERP Sales Document）、doc（单据名）、wait（最多等几秒，默认 300；0 = 只看一眼）。
给对话场景用：对话里点了「写入」之后，场景靠它知道这张单写成了没有、单号是什么。只在测试租户跑。
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
try:
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    doctype, name = params["doctype"], params["doc"]
    if doctype not in ("Purchase Document", "ERP Sales Document"):
        raise SystemExit(f"unknown doctype {doctype}")
    frappe.set_user("Administrator")
    fields = ["status", "ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book", "ledger_needed"]
    deadline, state = time.monotonic() + int(params.get("wait", 300)), {}
    while True:
        frappe.db.rollback()
        state = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
        if state.get("ledger_state") in ("written", "mismatch") or time.monotonic() > deadline:
            break
        time.sleep(3)
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["status", "verify_state", "reason", "request_key"], as_dict=True) \
        if state.get("ledger_job") else None
    written = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
        if state.get("ledger_book") else []
    party = None
    if doctype == "ERP Sales Document":
        customer = frappe.db.get_value(doctype, name, "customer")
        party = frappe.db.get_value("ERP Customer", customer, ["express_code", "express_company"], as_dict=True) if customer else None
    result.update(ok=True, state=state, job=job, book_paths=written, party=party,
                  jobs=frappe.db.count("Pearnly CC Job", {"source_doctype": doctype, "source_name": name}))
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
