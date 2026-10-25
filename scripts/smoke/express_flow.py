"""ERP 写 Express 冒烟（在实验栈 ERP 站点网页容器里跑）：开一张单 → 进写账队列 → 测试机 CC 写进 C 盘 T 账套 → 等回执与 CC 自己的读回核对。

    env/bin/python /tmp/smoke-express_flow.py <站点> '<JSON 参数>'   # 末行打印 SMOKE {"ok": ..., ...}

参数（登记表入口的 args）：user = 测试机 CC 上登录的员工；company_tax = 公司税号（或 company = 公司编号；显式给，不改员工的当前公司）；
party = 对方的 Express 编码；item = 物料的 Express 编码；loccod = 仓库码；kind = purchase | sales | stock_out | stock_in
（出入库单，写成 Express 领用 OU / 调整 JU，出入库第一期 B 段）；wait = 最多等几秒（默认 240）。
写入只到测试机 C:\\PearnlyExpress 的 T 账套（这家公司对应的账套由 CC 报来）；不碰 S 盘 / accserver。只给测试租户用。
防护（同 Cowork 版 `cowork_express_flow.py`，验收 10-25 建议）：写到哪份账套由 CC 给这家公司报的账套决定，测试机白天连上 accserver 时
会把 S 盘同税号的真账也报上来。所以建单前这家公司报上来的账套必须**全部**是 C:\\PearnlyExpress\\ 下以 T 开头的，否则不建单；
写完再按单据记下的账套核一次实际路径。
"""
import json
import secrets
import base64
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]  # run.py 传参用 base64

ROOT = "c:\\pearnlyexpress\\"


def c_drive_t_book(path) -> bool:
    text = str(path or "").strip().replace("/", "\\").casefold()
    return text.startswith(ROOT) and text.rstrip("\\").rsplit("\\", 1)[-1].startswith("t")


site = sys.argv[1]
params = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    user = params["user"]
    # 公司按税号找（测试租户重新导入后公司编号会变，税号不变）；也可以直接给 company
    company = params.get("company") or frappe.db.get_value("ERP Company", {"tax_id": params["company_tax"], "is_active": 1}, "name")
    if not company:
        raise SystemExit(f"test company missing: {params.get('company_tax')}")
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES

    if not TEST_SITES.fullmatch(site):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    tax_id, branch = frappe.db.get_value("ERP Company", company, ["tax_id", "branch"]) or ("", "")
    candidates = books.for_company(tax_id, branch)
    outside = [item.get("path") for item in candidates if not c_drive_t_book(item.get("path"))]
    if not candidates or outside:
        raise SystemExit(f"refuse: {company} has books outside C:\\PearnlyExpress T books: {outside[:5] or 'none reported'}")
    kind = params.get("kind", "purchase")
    frappe.set_user(user)
    product = frappe.db.get_value("ERP Product", {"express_code": params["item"], "express_company": company}, "name")
    warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": params.get("loccod", "01")}, "name")
    if not product or not warehouse:
        raise SystemExit(f"test data missing: product {params['item']} / warehouse {params.get('loccod')} in {company}")
    line = {"product": product, "description": frappe.db.get_value("ERP Product", product, "product_name"), "qty": 1,
            "unit_price": params.get("price", 10), "warehouse": warehouse}
    if kind in ("stock_out", "stock_in"):
        from pearnly_erp_portal.inventory import documents

        doctype = "ERP Stock Entry"
        direction = "out" if kind == "stock_out" else "in"
        posted = documents.create_entry(direction=direction, posting_date=frappe.utils.today(), warehouse=warehouse,
                                        lines=[{"product": product, "qty": 1, "unit_cost": params.get("price", 10)}],
                                        purpose="internal_use" if direction == "out" else "other",
                                        reason=f"SMOKE-{int(time.time())}", submit=True)
    elif kind == "sales":
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
    if kind not in ("stock_out", "stock_in"):
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
    book_key = frappe.db.get_value(doctype, posted["name"], "express_book")
    written = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": book_key}, fields=["path"])] if book_key else []
    result["book_paths"] = written
    result.update(state=state, job=job)
    result["summary"] = f"{posted['name']} → {state.get('express_docnum') or state.get('express_state')}（CC 读回 {(job or {}).get('verify_state') or '-'}）"
    result["ok"] = (state.get("express_state") == "written" and bool(state.get("express_docnum"))
                    and (job or {}).get("verify_state") in ("ok", "") and bool(written) and all(map(c_drive_t_book, written)))
except SystemExit as stop:
    result["why"] = str(stop)
finally:
    frappe.destroy()
print("SMOKE " + json.dumps(result, ensure_ascii=False, default=str))
