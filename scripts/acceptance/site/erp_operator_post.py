"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：给接了验收账套的那家公司开一张采购 / 销售单，**交给代录**（不直写）→
CC 桌面版在那台电脑上打开 Express 界面录入 → 回执 → 把单据、代录任务、写账件、落点账套、期望值交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_operator_post.py <站点> '<JSON 参数>'

参数：user（那台电脑的 CC 桌面版上登录的员工；代录用他存在电脑上的 Express 登录）、company_tax / branch、ledger（验收账套路径，只认它）、
kind（purchase：现购一张，货品入库 / sales：赊销一张，新客户、只记账的一行）、run、price、wait（最多等几秒，默认 900：界面录入比直写慢）。

走的是代录背后的同一组服务端函数：建代录任务（`express_operator.tasks.create`）→ 交单开始（`express_operator.api.start`，写入计划
「开始」走的就是它）→ 产品把单交进写账队列并带代录标记（`express.push.send(operator=…)`）。登记里标 via = "api"：对话、开始前卡片、
中途弹框带回对话不在这条里。
网页提交的单会自动排直写；这里只在本进程里**不排那一次后台直写**（单据停在「排队中、还没交进队列」——这正是代录接的状态），
紧接着交代录；交成之后这张单归代录，直写不再接。要是直写还是先领走了，如实交回 `taken_by_direct`，不硬抢。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger；写完按单据记下的账套核实际路径。
"""
import base64
import json
import secrets
import sys
import time
from unittest.mock import patch

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DIRECT_SEND = "pearnly_erp_portal.express.push.send"


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
    from pearnly_core.express_operator import api as operator_api
    from pearnly_core.express_operator import tasks
    from pearnly_core.express_rules.push.common import detect_prename

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
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
    kind, price, today = params["kind"], params.get("price", 10), frappe.utils.today()
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    expected = {}
    if kind == "purchase":
        from pearnly_erp_portal.purchasing import entry

        doctype, prefix = "Purchase Document", "P"
        product = frappe.db.get_value("ERP Product", by_code, ["name", "product_name", "express_code"], order_by="express_code asc", as_dict=True)
        supplier = frappe.db.get_value("ERP Supplier", by_code, ["name", "express_code"], order_by="express_code asc", as_dict=True)
        warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": ["is", "set"]}, "name", order_by="express_code asc")
        if not (product and supplier and warehouse):
            raise SystemExit(f"test data missing in {company}: product={bool(product)} supplier={bool(supplier)} warehouse={bool(warehouse)}")
        payload = {"doc_kind": "purchase_invoice", "company": company, "supplier": supplier.name, "doc_no": f"ACC-{params['run']}",
                   "doc_date": today, "has_vat": 1, "price_mode": "exclusive", "is_paid": 1,
                   "lines": [{"product": product.name, "description": product.product_name, "qty": 1, "unit_price": price,
                              "warehouse": warehouse, "item_type": "goods"}]}
        expected.update(supplier_code=supplier.express_code, item_code=product.express_code, qty=1, price=price)
    elif kind == "sales":
        from pearnly_erp_portal.sales import entry

        doctype, prefix, buyer = "ERP Sales Document", "S", f"ร้านเอซีซี {params['run']}"
        payload = {"doc_kind": "receipt", "company": company, "doc_date": today, "source": "web", "has_vat": 1, "vat_rate": 7,
                   "price_includes_vat": 0, "header_discount": 0, "is_paid": 0, "buyer_name": buyer, "party_type": "person",
                   "lines": [{"description": f"ACC service {params['run']}", "qty": 1, "unit_price": price, "item_type": "service"}]}
        named = detect_prename(buyer)
        expected.update(new_customer=True, buyer=buyer, prename=named, core_name=buyer[len(named):].strip())
    else:
        raise SystemExit(f"unknown kind {kind}")

    real = frappe.enqueue

    def without_direct_send(method, *args, **kwargs):
        return None if method == DIRECT_SEND else real(method, *args, **kwargs)

    with patch.object(frappe, "enqueue", without_direct_send):
        posted = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")
        frappe.db.commit()
    name, doc_id = posted["name"], f"{prefix}:{posted['name']}"
    fields = ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book", "ledger_needed"]
    before = frappe.db.get_value(doctype, name, fields, as_dict=True)
    result.update(company=company, doc=name, doc_id=doc_id, before=before)
    if before.ledger_job:
        result.update(ok=True, taken_by_direct=True)
        raise SystemExit(0)
    task = tasks.create(company, today[:7])
    frappe.db.commit()
    started = operator_api.start(company, [doc_id], task=task["name"])
    frappe.db.commit()
    result.update(task=task["name"], started=started)

    deadline, state = time.monotonic() + int(params.get("wait", 900)), {}
    while time.monotonic() < deadline:
        frappe.db.rollback()
        state = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
        if state.get("ledger_state") in ("written", "mismatch", "failed") or (state.get("ledger_state") == "held" and state.get("ledger_job")
                                                                             and frappe.db.get_value("Pearnly CC Job", state["ledger_job"], "status") == "manual"):
            break
        time.sleep(5)
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["status", "verify_state", "reason", "request_key"], as_dict=True) \
        if state.get("ledger_job") else None
    written = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
        if state.get("ledger_book") else []
    view = tasks.read(task["name"])
    doc = frappe.get_doc(doctype, name)
    party = frappe.db.get_value("ERP Customer", doc.customer, ["express_code", "express_company"], as_dict=True) \
        if kind == "sales" and doc.get("customer") else None
    if party:
        expected["customer_code"] = party.express_code or ""
    expected["totals"] = {key: float(doc.get(key) or 0) for key in ("subtotal", "vat_amount", "grand_total")}
    result.update(ok=True, state=state, job=job, book_paths=written, party=party, expected=expected,
                  task_view={"status": view.get("status"), "documents": [{key: row.get(key) for key in ("doc_id", "state", "docnum", "reason")}
                                                                        for row in view.get("documents") or []]},
                  jobs=frappe.db.count("Pearnly CC Job", {"source_doctype": doctype, "source_name": name}))
except SystemExit as stop:
    if str(stop) not in ("0", "None"):
        result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
