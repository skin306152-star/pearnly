"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：只用手机（一台登录过 CC 的电脑都没有）的员工开一张现购单提交 →
写账件归他本人，办公室那台（够得到这本账套、登录人对这家公司有权限）在领单那一刻替他写（11-17 定稿，`cc_desktop.jobs`）→ 写进 Express →
单据仍是发单人的。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_office_actor.py <站点> '<JSON 参数>'

参数：user（测试机那台 CC 桌面版上登录的员工，办公室电脑的人）、phone_user（只用手机的员工：同一家公司、有开采购单权限、
从没在任何电脑上登录过 CC）、company_tax / branch、ledger（验收账套路径，只认它）、run、price、wait（默认 300）。

开跑前核现场，不满足就拒跑（场景记前置条件不满足）：phone_user 一台登录过 CC 的电脑都没有（`jobs.writers` = missing）、这家公司
在他的可见公司里；办公室那台此刻在线（`jobs.writers(user)` = ok）。走网页提交背后的同一组服务端函数（`purchasing.entry.post` →
`express.push.send`），登记里标 via = "api"。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger；写完按单据记下的账套核实际路径。
"""
import base64
import json
import secrets
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
FIELDS = ["owner", "ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book"]


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
    from pearnly_erp_portal.express import push, target
    from pearnly_erp_portal.masters import company as companies
    from pearnly_erp_portal.purchasing import entry

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    filters = {"tax_id": params["company_tax"], "is_active": 1}
    if params.get("branch"):
        filters["branch"] = params["branch"]
    company = None
    for name in frappe.get_all("ERP Company", filters=filters, pluck="name", order_by="name"):
        tax, branch = frappe.db.get_value("ERP Company", name, ["tax_id", "branch"])
        reported = [item.get("path") for item in books.for_company(tax, branch)]
        result.setdefault("reported", {})[name] = reported
        if reported and all(same_path(path, params["ledger"]) for path in reported):
            company = name
            break
    if not company:
        raise SystemExit("refuse: no company whose reported books are exactly the acceptance ledger")
    office, phone, today = params["user"], params["phone_user"], frappe.utils.today()
    if not frappe.db.get_value("User", phone, "enabled"):
        raise SystemExit(f"unmet: {phone} is not an enabled user on this site")
    key = target.pick(push._company(company), today)["book"]["key"]
    result.update(company=company, book=key, phone_writers=jobs.writers(phone, key)["state"], office_writers=jobs.writers(office, key)["state"],
                  phone_companies=companies.allowed_companies(phone))
    if result["phone_writers"] != "missing":
        raise SystemExit(f"unmet: {phone} has signed in to CC Desktop on a computer ({result['phone_writers']}); the scenario needs a phone-only employee")
    if company not in result["phone_companies"]:
        raise SystemExit(f"unmet: {company} is not among {phone}'s companies")
    if result["office_writers"] != "ok":
        raise SystemExit(f"unmet: the office computer of {office} is not online for this book ({result['office_writers']})")
    frappe.set_user(phone)
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    product = frappe.db.get_value("ERP Product", by_code, ["name", "product_name", "express_code"], order_by="express_code asc", as_dict=True)
    supplier = frappe.db.get_value("ERP Supplier", by_code, ["name", "express_code"], order_by="express_code asc", as_dict=True)
    warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": ["is", "set"]}, "name", order_by="express_code asc")
    if not (product and supplier and warehouse):
        raise SystemExit(f"test data missing in {company}: product={bool(product)} supplier={bool(supplier)} warehouse={bool(warehouse)}")
    price = float(params.get("price", 10))
    payload = {"doc_kind": "purchase_invoice", "company": company, "supplier": supplier.name, "doc_no": f"ACC-M{params['run']}",
               "doc_date": today, "has_vat": 1, "price_mode": "exclusive", "is_paid": 1,
               "lines": [{"product": product.name, "description": product.product_name, "qty": 1, "unit_price": price,
                          "warehouse": warehouse, "item_type": "goods"}]}
    posted = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")
    frappe.db.commit()
    name = posted["name"]
    push.send("Purchase Document", name)
    frappe.db.commit()
    first = frappe.db.get_value("Purchase Document", name, FIELDS, as_dict=True) or {}
    job = frappe.db.get_value("Pearnly CC Job", first.get("ledger_job"), ["name", "status", "actor", "request_key"], as_dict=True) \
        if first.get("ledger_job") else None
    row = frappe.db.get_value("Purchase Document", name, ["name", "owner", "ledger_book"], as_dict=True)
    result.update(doc=name, first=first, first_job=job, expected_actor=phone,
                  note=push._queued_note("Purchase Document", row) if first.get("ledger_job") else "")
    state = first
    deadline = time.monotonic() + int(params.get("wait", 300))
    while time.monotonic() < deadline:
        frappe.db.rollback()
        state = frappe.db.get_value("Purchase Document", name, FIELDS, as_dict=True) or {}
        if state.get("ledger_state") in ("written", "mismatch", "held", "failed"):
            break
        time.sleep(5)
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["name", "status", "verify_state", "reason", "actor", "writer", "request_key"],
                              as_dict=True) if state.get("ledger_job") else None
    written = [r.path for r in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
        if state.get("ledger_book") else []
    doc = frappe.get_doc("Purchase Document", name)
    result.update(ok=True, state=state, job=job, book_paths=written, owner=doc.owner,
                  expected={"supplier_code": supplier.express_code, "item_code": product.express_code, "qty": 1, "price": price,
                            "totals": {key_: float(doc.get(key_) or 0) for key_ in ("subtotal", "vat_amount", "grand_total")}})
except SystemExit as stop:
    if str(stop) not in ("0", "None"):
        result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
