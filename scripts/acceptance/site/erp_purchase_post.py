"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：给接了验收账套的那家公司开一张采购单 → 进写账队列 →
等 CC 桌面版写完回执 → 把单据、写账状态、CC 读回、落点账套交回。末行打印 ACC {json}。

    env/bin/python /tmp/smoke-erp_purchase_post.py <站点> '<JSON 参数>'

参数：user（测试机 CC 桌面版上登录的员工，CC 只领他的单）、company_tax / branch（公司）、ledger（验收账套路径，只认它）、
run（本次运行编号，写进单号便于按号读回）、price、wait（最多等几秒，默认 300）。
防护（同 `scripts/smoke/express_flow.py`，落点换成验收账套）：只在测试租户跑；这家公司由 CC 报上来的账套必须**全部**就是 ledger，
多报一本（同税号的真账套、别的测试套）就拒绝、不建单；写完再按单据记下的账套核一次实际路径。
这一版从服务端提交（与现有冒烟同一个入口 purchasing.entry.post），登记里标 via = "api"：它证明「提交之后写进账本并且写对」，
不算采购页界面的覆盖；界面那一段在首次实跑时对着测试机实验栈补上。
"""
import base64
import json
import secrets
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]


def same_path(left, right) -> bool:
    def norm(text):
        return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()
    return norm(left) == norm(right)


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.express import push
    from pearnly_erp_portal.purchasing import entry

    if not TEST_SITES.fullmatch(site):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    filters = {"tax_id": params["company_tax"], "is_active": 1}
    if params.get("branch"):
        filters["branch"] = params["branch"]
    company = None
    for name in frappe.get_all("ERP Company", filters=filters, pluck="name", order_by="name"):
        tax_id, branch = frappe.db.get_value("ERP Company", name, ["tax_id", "branch"])
        reported = [item.get("path") for item in books.for_company(tax_id, branch)]
        result.setdefault("reported", {})[name] = reported
        if reported and all(same_path(path, params["ledger"]) for path in reported):
            company = name
            break
    if not company:
        raise SystemExit("refuse: no company whose reported books are exactly the acceptance ledger")
    frappe.set_user(params["user"])
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    product = frappe.db.get_value("ERP Product", by_code, ["name", "product_name", "express_code"], order_by="express_code asc", as_dict=True)
    supplier = frappe.db.get_value("ERP Supplier", by_code, ["name", "express_code"], order_by="express_code asc", as_dict=True)
    warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": params.get("loccod", "01")}, "name")
    if not (product and supplier and warehouse):
        raise SystemExit(f"test data missing in {company}: product={bool(product)} supplier={bool(supplier)} warehouse={bool(warehouse)}")
    price = params.get("price", 10)
    payload = {"doc_kind": "purchase_invoice", "company": company, "supplier": supplier.name, "doc_no": f"ACC-{params['run']}",
               "doc_date": frappe.utils.today(), "has_vat": 1, "price_mode": "exclusive", "is_paid": 1,
               "lines": [{"product": product.name, "description": product.product_name, "qty": 1, "unit_price": price,
                          "warehouse": warehouse, "item_type": "goods"}]}
    posted = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")
    frappe.db.commit()
    push.send("Purchase Document", posted["name"])
    frappe.db.commit()
    fields = ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book"]
    deadline, state = time.monotonic() + int(params.get("wait", 300)), {}
    while time.monotonic() < deadline:
        frappe.db.rollback()
        state = frappe.db.get_value("Purchase Document", posted["name"], fields, as_dict=True) or {}
        if state.get("ledger_state") in ("written", "mismatch", "held", "failed"):
            break
        time.sleep(5)
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["status", "verify_state", "reason"], as_dict=True) \
        if state.get("ledger_job") else None
    written = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
        if state.get("ledger_book") else []
    doc = frappe.get_doc("Purchase Document", posted["name"])
    result.update(ok=True, company=company, doc=posted["name"], doc_no=payload["doc_no"], state=state, job=job, book_paths=written,
                  expected={"supplier_code": supplier.express_code, "item_code": product.express_code, "qty": 1, "price": price,
                            "doc_date": str(doc.get("doc_date")),
                            "totals": {key: float(doc.get(key) or 0) for key in ("subtotal", "vat_amount", "grand_total") if doc.get(key) is not None}})
except SystemExit as stop:
    result["why"] = str(stop)
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
