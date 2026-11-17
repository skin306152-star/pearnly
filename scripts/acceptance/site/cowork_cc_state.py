"""验收场景的站点侧脚本（只读，在测试租户的 Cowork 网页容器里跑）：给「Cowork 的 CC 对话直写」场景看站点上的两样事实。末行打印 ACC {json}。

    env/bin/python /tmp/acc-cowork_cc_state.py <站点> '<JSON 参数>'

参数：mode、user（对话里登录的 Cowork 员工）、company（对话建在哪家公司，Cowork Company 的编号）。
- mode = pending：开对话之前，这家公司在 ``period``（YYYY-MM）里还等着录入的单（与 CC 的 `cowork_list_documents` 按公司、
  ``push_state = waiting`` 读到的同一份），外加整个站点里还没推成的验收样票（票号 ``ACC-`` 开头，前几轮没收尾留下的）。
  场景要求两样都为空：否则 CC 的写入计划会把它们一起带上（11-16 ERP 代录对话首跑的教训），记前置条件不满足。另交回站点此刻的时间
  ``since``（站点时钟，给 pushes 用）。
- mode = pushes：``since`` 之后识别出来、票号是 ``number`` 或对方税号是 ``party_tax`` 的单，与它们的推送记录（状态、单号、写后读回、
  实际写到的账套、真写了还是认作重复跳过）；等到每条推送记录都有结论或 ``wait`` 秒到了（默认 420）。

不建、不改、不推任何东西。只在测试租户跑（产品的测试站点名单或本机 Mac 实验栈）。
"""
import base64
import json
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
PUSH, HISTORY = "Cowork Push Record", "Cowork OCR History"
DONE = ("success", "manual", "cancelled")

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    import calendar

    from pearnly_core.execution.replay import TEST_SITES

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    if "pearnly_cowork" not in frappe.get_installed_apps():
        raise SystemExit(f"refuse: {site} is not a Cowork site")
    mode, company = params["mode"], params["company"]
    if not frappe.db.exists("Cowork Company", company):
        raise SystemExit(f"no Cowork Company {company} on this site")
    frappe.set_user(params["user"])

    if mode == "pending":
        from pearnly_cowork.assistant import tools

        year, month = (int(part) for part in params["period"].split("-")[:2])
        first, last = f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        seen = json.loads(tools.list_documents({"company": company, "push_state": "waiting", "invoice_date_from": first,
                                                "invoice_date_to": last, "limit": 50}))
        if seen.get("status") == "failed":
            raise SystemExit(f"listing refused: {seen.get('reason')}")
        frappe.set_user("Administrator")
        leftovers = frappe.get_all(HISTORY, filters={"invoice_no": ["like", "ACC-%"], "last_push_status": ["not in", ["success"]]},
                                   fields=["name", "invoice_no", "invoice_date", "last_push_status"], order_by="creation desc",
                                   limit_page_length=50)
        result.update(ok=True, since=frappe.utils.now(), leftovers=leftovers,
                      waiting=[{key: row.get(key) for key in ("name", "invoice_no", "invoice_date", "seller_name", "buyer_name",
                                                              "total_amount", "last_push_status")} for row in seen.get("documents") or []])
    elif mode == "pushes":
        frappe.set_user("Administrator")

        def record_of(row):
            meta = (json.loads(row.get("response_json") or "{}") or {}).get("meta") or {}
            return {"name": row["name"], "history": row["history"], "status": row.get("status"), "docnum": row.get("external_docnum"),
                    "reason": row.get("reason"), "verify": row.get("verify_state"), "written_to": meta.get("account_dir"),
                    "written": bool(meta.get("written")), "duplicate_skipped": bool(meta.get("duplicate_skipped"))}

        deadline, docs, pushes = time.monotonic() + int(params.get("wait", 420)), [], []
        while True:
            frappe.db.rollback()
            found = frappe.get_all(HISTORY, filters={"creation": [">=", params["since"]]},
                                   fields=["name", "invoice_no", "invoice_date", "seller_tax", "buyer_tax", "last_push_status",
                                           "last_push_record", "push_account_set"], order_by="creation asc", limit_page_length=50)
            docs = [row for row in found if row.get("invoice_no") == params.get("number")
                    or params.get("party_tax") in (row.get("seller_tax"), row.get("buyer_tax"))]
            pushes = [record_of(row) for row in frappe.get_all(
                PUSH, filters={"history": ["in", [row.name for row in docs] or ["-"]]},
                fields=["name", "history", "status", "external_docnum", "reason", "verify_state", "response_json"],
                order_by="creation asc", limit_page_length=50)]
            if (pushes and all(item["status"] in DONE for item in pushes)) or time.monotonic() > deadline:
                break
            time.sleep(5)
        result.update(ok=True, documents=docs, pushes=pushes)
    else:
        raise SystemExit(f"unknown mode {mode}")
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
