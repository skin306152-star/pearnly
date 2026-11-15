"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：作废一张已经写进 Express 的采购单 / 销售单，等一段时间看有没有再排出
写账件，把作废后的单据状态、写账栏、这张单名下的写账件交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_doc_cancel.py <站点> '<JSON 参数>'

参数：user、kind（purchase / sales）、doc（单据名，前一步站点脚本交回的）、settle（作废后等几秒，默认 45：够 CC 桌面版领一次单）。

已写进 Express 的采购单 / 销售单作废：ERP 里作废、库存冲回，**Express 里那张不动**（没有原生作废载荷，由财务在 Express 里处理），
也不再排新的写账件。走的是页面「Cancel」背后的同一个函数（`purchasing.entry.cancel` / `sales.entry.cancel`），登记里标 via = "api"。
只在测试租户跑；单据必须是本次运行前一步建的（场景传名字进来），不按条件找单。
"""
import base64
import json
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
KINDS = {"purchase": "Purchase Document", "sales": "ERP Sales Document"}

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.execution.replay import TEST_SITES

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    doctype = KINDS[params["kind"]]
    if params["kind"] == "purchase":
        from pearnly_erp_portal.purchasing import entry
    else:
        from pearnly_erp_portal.sales import entry
    frappe.set_user(params["user"])
    fields = ["status", "ledger_state", "ledger_docnum", "ledger_reason", "ledger_job"]
    before = frappe.db.get_value(doctype, params["doc"], fields, as_dict=True)
    if not before or before.ledger_state != "written":
        raise SystemExit(f"{params['doc']} is not a document written to Express (state: {before and before.ledger_state})")
    jobs = lambda: frappe.get_all("Pearnly CC Job", filters={"source_doctype": doctype, "source_name": params["doc"]},  # noqa: E731
                                  fields=["name", "status"], order_by="creation asc", limit_page_length=50)
    jobs_before = jobs()
    cancelled = entry.cancel(params["doc"], reason="ACC cancel")
    frappe.db.commit()
    time.sleep(int(params.get("settle", 45)))
    frappe.db.rollback()
    after = frappe.db.get_value(doctype, params["doc"], fields, as_dict=True)
    result.update(ok=True, cancelled=cancelled, before=before, after=after, jobs_before=jobs_before, jobs_after=jobs())
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
