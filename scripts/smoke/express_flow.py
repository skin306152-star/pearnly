"""ERP 写 Express 冒烟（在实验栈 ERP 站点网页容器里跑）：开一张单 → 进写账队列 → 测试机 CC 写进 C 盘 T 账套 → 等回执与 CC 自己的读回核对。

    env/bin/python /tmp/smoke-express_flow.py <站点> '<JSON 参数>'   # 末行打印 SMOKE {"ok": ..., ...}

参数（登记表入口的 args）：user = 测试机 CC 上登录的员工；company = 公司（显式给，不改员工的当前公司）；
party = 对方的 Express 编码；item = 物料的 Express 编码；loccod = 仓库码；kind = purchase | sales；wait = 最多等几秒（默认 240）。
写入只到测试机 C:\\PearnlyExpress 的 T 账套（这家公司对应的账套由 CC 报来）；不碰 S 盘 / accserver。只给测试租户用。
"""
import json
import secrets
import sys
import time

import frappe

site = sys.argv[1]
params = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    user, company = params["user"], params["company"]
    kind = params.get("kind", "purchase")
    frappe.set_user(user)
    product = frappe.db.get_value("ERP Product", {"express_code": params["item"], "express_company": company}, "name")
    warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": params.get("loccod", "01")}, "name")
    if not product or not warehouse:
        raise SystemExit(f"test data missing: product {params['item']} / warehouse {params.get('loccod')} in {company}")
    line = {"product": product, "description": frappe.db.get_value("ERP Product", product, "product_name"), "qty": 1,
            "unit_price": params.get("price", 10), "warehouse": warehouse}
    if kind == "sales":
        from pearnly_erp_portal.sales import entry

        doctype = "ERP Sales Document"
        party = frappe.db.get_value("ERP Customer", {"express_code": params["party"], "express_company": company}, "name")
        payload = {"doc_kind": "receipt", "company": company, "customer": party, "doc_date": frappe.utils.today(), "has_vat": 1,
                   "price_mode": "exclusive", "is_paid": 1, "lines": [line]}
    else:
        from pearnly_erp_portal.purchasing import entry

        doctype = "Purchase Document"
        party = frappe.db.get_value("ERP Supplier", {"express_code": params["party"], "express_company": company}, "name")
        payload = {"doc_kind": "purchase_invoice", "company": company, "supplier": party, "doc_no": f"SMOKE-{int(time.time())}",
                   "doc_date": frappe.utils.today(), "has_vat": 1, "price_mode": "exclusive", "is_paid": 1,
                   "lines": [{**line, "item_type": "goods"}]}
    posted = entry.post(payload, request_id=f"smoke-{secrets.token_hex(6)}")
    frappe.db.commit()
    result["doc"] = posted.get("name")
    from pearnly_erp_portal.express import push

    push.send(doctype, posted["name"])
    frappe.db.commit()
    deadline = time.monotonic() + int(params.get("wait", 240))
    state = {}
    while time.monotonic() < deadline:
        frappe.db.rollback()  # 读别的进程（回执）提交的新状态
        state = frappe.db.get_value(doctype, posted["name"], ["express_state", "express_docnum", "express_reason", "express_job"], as_dict=True) or {}
        if state.get("express_state") in ("written", "mismatch", "held", "failed"):
            break
        time.sleep(5)
    job = frappe.db.get_value("Pearnly CC Job", state.get("express_job"), ["status", "verify_state", "reason"], as_dict=True) if state.get("express_job") else None
    result.update(state=state, job=job)
    result["ok"] = state.get("express_state") == "written" and bool(state.get("express_docnum")) and (job or {}).get("verify_state") in ("ok", "")
finally:
    frappe.destroy()
print("SMOKE " + json.dumps(result, ensure_ascii=False, default=str))
