"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：给接了验收账套的那家公司开一张采购 / 销售单，让它**停在没写进 Express**
（写账件刚排上就撤掉，单据的账本栏落成「失败」）——留给 CC 对话里「推送 / 再写一次」。把单据、它此刻的状态、期望值交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_push_held.py <站点> '<JSON 参数>'

参数：user、company_tax / branch、ledger（验收账套路径，只认它）、kind（purchase / sales）、run、price。

ERP 单据一提交就自动排写账，直写对话里的「推送」只等于单据页的「再写一次」：要验那条路，先得有一张停下的单。
这里只在本进程里不排提交后的那次后台直写，自己交一次写账、紧接着撤掉那一件（CC 桌面版每隔十几秒领一次单，来不及领）；
要是那一件还是被电脑领走了，如实交回 `taken`，场景记前置条件不满足，不硬撤。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger。
"""
import base64
import json
import secrets
import sys
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
    from pearnly_core.cc_desktop import books, jobs
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_core.express_rules.push.common import detect_prename
    from pearnly_erp_portal.express import push

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
    kind, today = params["kind"], frappe.utils.today()
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    expected = {}
    if kind == "purchase":
        from pearnly_erp_portal.purchasing import entry

        doctype, prefix, price = "Purchase Document", "P", params.get("price", 10)
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

        doctype, prefix, price, buyer = "ERP Sales Document", "S", params.get("price", 1000), f"ร้านเอซีซี {params['run']}"
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
    name = posted["name"]
    push.send(doctype, name)
    job = frappe.db.get_value(doctype, name, "ledger_job")
    taken = False
    if job:
        try:
            jobs.cancel(job, "acceptance: left for the chat to send again")
            frappe.db.commit()
        except Exception:  # noqa: BLE001 —— 电脑已经领走：不硬撤
            frappe.db.rollback()
            taken = True
    fields = ["status", "system_no", "ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "grand_total", "vat_amount", "subtotal"]
    frappe.db.rollback()
    state = frappe.db.get_value(doctype, name, fields, as_dict=True)
    expected["totals"] = {key: float(state.get(key) or 0) for key in ("subtotal", "vat_amount", "grand_total")}
    result.update(ok=True, company=company, doctype=doctype, doc=name, doc_id=f"{prefix}:{name}", number=state.system_no or name, state=state,
                  taken=taken, expected=expected, jobs=frappe.db.count("Pearnly CC Job", {"source_doctype": doctype, "source_name": name}))
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
