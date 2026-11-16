"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：给接了验收账套的那家公司开一张**赊销单**（未付款、只记账的一行）→
进写账队列 → 等 CC 桌面版写成 IV 回执 → 把单据、写账状态、客户挂上的 Express 编码、落点账套交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_sales_post.py <站点> '<JSON 参数>'

参数：user、company_tax / branch、ledger（验收账套路径，只认它）、run（本次运行标记）、price（不含税单价，默认 1000）、
buyer（手打的新客户名，例如「ร้านเอซีซี 1234」；与 customer_code 二选一）、
customer_code（账本里已有客户的 Express 编码，原样，含不换行空格；nbsp = 真时不给编码，自己挑一位编码带不换行空格的客户）、
wait（最多等几秒，默认 300）。

走的是网页「提交」背后的同一个入口 `sales.entry.post`，登记里标 via = "api"。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger；写完再按单据记下的账套核一次实际路径。
"""
import base64
import json
import secrets
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DOCTYPE = "ERP Sales Document"


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
    from pearnly_core.express_rules.push.common import detect_prename
    from pearnly_erp_portal.sales import entry

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
    # 场景开头刚把验收账套还原到基线（发票号、客户编码会被重新用一遍）：站点上这本账套残留的未收快照、读好还没落的按需读回、
    # 「已收清」记号都是上一格那份账本的，清掉（只在测试站点；正式账本不回滚，没有这回事。11-16 首跑 line.refused：
    # 上一格写后排的按客户重读读好没落，这一格开收时被当成「已读好」落下，发票带着上一格的已收 500）
    from pearnly_core.cc_desktop import requests as cc_requests
    from pearnly_erp_portal.collection import snapshot
    from pearnly_erp_portal.express import target
    picked = target.pick(frappe.db.get_value("ERP Company", company, ["name", "company_name", "tax_id", "branch"], as_dict=True),
                         frappe.utils.today(), None)
    book_key = (picked.get("book") or {}).get("key")
    if book_key:
        result["cleared"] = {"snapshot": frappe.db.count(snapshot.DOCTYPE, {"book_key": book_key}),
                             "reads": frappe.db.count(cc_requests.REQUEST, {"kind": snapshot.KIND, "book_key": book_key})}
        frappe.db.delete(snapshot.DOCTYPE, {"book_key": book_key})
        frappe.db.delete(cc_requests.REQUEST, {"kind": snapshot.KIND, "book_key": book_key})
        frappe.cache.delete_value(snapshot.GONE + str(book_key))
        frappe.db.commit()
    frappe.set_user(params["user"])
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    price = params.get("price", 1000)
    payload = {"doc_kind": "receipt", "company": company, "doc_date": frappe.utils.today(), "source": "web", "has_vat": 1, "vat_rate": 7,
               "price_includes_vat": 0, "header_discount": 0, "is_paid": 0,
               "lines": [{"description": f"ACC service {params['run']}", "qty": 1, "unit_price": price, "item_type": "service"}]}
    customer, buyer = None, str(params.get("buyer") or "").strip()
    if not buyer:
        code = params.get("customer_code")
        if not code and params.get("nbsp"):
            code = next((item for item in frappe.get_all("ERP Customer", filters=by_code, pluck="express_code", order_by="express_code asc",
                                                         limit_page_length=20000) if "\xa0" in str(item)), None)
            if not code:
                raise SystemExit("no customer whose ledger code carries a no-break space in this ledger")
        customer = frappe.db.get_value("ERP Customer", {**by_code, "express_code": code}, ["name", "customer_name", "express_code"], as_dict=True) \
            if code else None
        # 库的排序规则把不换行空格与空格当相等：取回来的那一行要逐字符就是这个编码
        if not customer or customer.express_code != code:
            raise SystemExit(f"customer with ledger code {code!r} not found in {company}")
        payload.update(customer=customer.name, party_type="person")
    else:
        payload.update(buyer_name=buyer, party_type="person")
    posted = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")
    frappe.db.commit()
    try:
        from pearnly_erp_portal.express import push

        push.send(DOCTYPE, posted["name"])   # 提交时已经排过；这里只是不等后台任务（同冒烟），重复推由服务端自己挡
        frappe.db.commit()
    except Exception:  # noqa: BLE001
        frappe.db.rollback()
    fields = ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book", "ledger_needed"]
    deadline, state = time.monotonic() + int(params.get("wait", 300)), {}
    while time.monotonic() < deadline:
        frappe.db.rollback()
        state = frappe.db.get_value(DOCTYPE, posted["name"], fields, as_dict=True) or {}
        if state.get("ledger_state") in ("written", "mismatch", "held", "failed"):
            break
        time.sleep(5)
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["status", "verify_state", "reason"], as_dict=True) \
        if state.get("ledger_job") else None
    written = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
        if state.get("ledger_book") else []
    doc = frappe.get_doc(DOCTYPE, posted["name"])
    party = frappe.db.get_value("ERP Customer", doc.customer, ["name", "customer_name", "express_code", "express_company", "is_active"],
                                as_dict=True) if doc.get("customer") else None
    prename = detect_prename(buyer) if buyer else ""
    result.update(ok=True, company=company, doc=posted["name"], system_no=posted.get("system_no"), state=state, job=job, book_paths=written,
                  party=party,
                  sale={key: doc.get(key) for key in ("payment_status", "paid_amount", "payment_method", "payment_date", "status")},
                  expected={"customer_code": (party or {}).get("express_code") or "", "new_customer": bool(buyer), "buyer": buyer,
                            "prename": prename, "core_name": buyer[len(prename):].strip() if buyer else "",
                            "totals": {key: float(doc.get(key) or 0) for key in ("subtotal", "vat_amount", "grand_total")}})
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
