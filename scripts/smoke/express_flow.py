"""ERP 写 Express 冒烟（在实验栈 ERP 站点网页容器里跑）：开一张单 → 进写账队列 → 测试机 CC 写进 C 盘 T 账套 → 等回执与 CC 自己的读回核对。

    env/bin/python /tmp/smoke-express_flow.py <站点> '<JSON 参数>'   # 末行打印 SMOKE {"ok": ..., ...}

参数（登记表入口的 args）：user = 测试机 CC 上登录的员工；company_tax = 公司税号、branch = 分店号（或 company = 公司编号；显式给，
不改员工的当前公司）；party = 对方的 Express 编码、item = 物料的 Express 编码（不给就取这家公司按 Express 编码排第一个的启用档）；
loccod = 仓库码；kind = purchase | sales | stock_out | stock_in（出入库单，写成 Express 领用 OU / 调整 JU，出入库第一期 B 段）；
wait = 最多等几秒（默认 240）。
写入只到测试机 C:\\PearnlyExpress 的 T 账套（这家公司对应的账套由 CC 报来）；不碰 S 盘 / accserver。只给测试租户用。
防护（同 Cowork 版 `cowork_express_flow.py`，验收 10-25 建议）：写到哪份账套由 CC 给这家公司报的账套决定，测试机白天连上 accserver 时
会把 S 盘同税号的真账也报上来。所以选公司时只留报上来的账套**全部**是 C:\\PearnlyExpress\\ 下以 T 开头的那家（同税号几家分店、
混报 S 盘账套的自动跳过，一家都没有就不建单）；写完再按单据记下的账套核一次实际路径。
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


def pick_company(params, books) -> str:
    """要写的公司：给了 company 就只看它，否则按税号（+ 分店）找启用的公司（测试租户重新导入后公司编号会变，税号不变）。
    只留 CC 报来的账套全部在 C 盘 T 账套的那家；同税号混报 S 盘 / accserver 账套的跳过，一家都没有就拒绝。"""
    if params.get("company"):
        names = [params["company"]]
    else:
        filters = {"tax_id": params["company_tax"], "is_active": 1}
        if params.get("branch"):
            filters["branch"] = params["branch"]
        names = frappe.get_all("ERP Company", filters=filters, pluck="name", order_by="name")
    skipped = {}
    for name in names:
        tax_id, branch = frappe.db.get_value("ERP Company", name, ["tax_id", "branch"]) or ("", "")
        reported = books.for_company(tax_id, branch)
        outside = [item.get("path") for item in reported if not c_drive_t_book(item.get("path"))]
        if reported and not outside:
            return name
        skipped[name] = outside[:5] or "none reported"
    raise SystemExit(f"refuse: no company with only C:\\PearnlyExpress T books "
                     f"({params.get('company') or params.get('company_tax')}): {skipped or 'company missing'}")


def express_row(doctype, company, code=None):
    """这家公司 Express 编码为 code 的启用档；不给 code 就取按 Express 编码排第一个的。"""
    filters = {"express_company": company, "is_active": 1}
    filters["express_code"] = code if code else ["is", "set"]
    return frappe.db.get_value(doctype, filters, "name", order_by="express_code asc")


site = sys.argv[1]
params = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    user = params["user"]
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES

    if not TEST_SITES.fullmatch(site):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    company = pick_company(params, books)
    kind = params.get("kind", "purchase")
    frappe.set_user(user)
    product = express_row("ERP Product", company, params.get("item"))
    warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": params.get("loccod", "01")}, "name")
    if not product or not warehouse:
        raise SystemExit(f"test data missing: product {params.get('item') or '(first)'} / warehouse {params.get('loccod')} in {company}")
    line = {"product": product, "description": frappe.db.get_value("ERP Product", product, "product_name"), "qty": 1,
            "unit_price": params.get("price", 10), "warehouse": warehouse}
    if kind in ("stock_out", "stock_in"):
        from pearnly_erp_portal.inventory import documents
        from pearnly_erp_portal.masters import company as companies

        doctype = "ERP Stock Entry"
        direction = "out" if kind == "stock_out" else "in"
        # 出入库单按员工的当前公司认仓（单据上没有公司栏）：只在这个进程里把当前公司指向要写的这家，不改员工设置
        active = companies.active_company
        companies.active_company = lambda user=None: company
        try:
            posted = documents.create_entry(direction=direction, posting_date=frappe.utils.today(), warehouse=warehouse,
                                            lines=[{"product": product, "qty": 1, "unit_cost": params.get("price", 10)}],
                                            purpose="internal_use" if direction == "out" else "other",
                                            reason=f"SMOKE-{int(time.time())}", submit=True)
        finally:
            companies.active_company = active
    elif kind == "sales":
        from pearnly_erp_portal.sales import entry

        doctype = "ERP Sales Document"
        party = express_row("ERP Customer", company, params.get("party"))
        payload = {"doc_kind": "receipt", "company": company, "customer": party, "doc_date": frappe.utils.today(), "has_vat": 1,
                   "price_mode": "exclusive", "is_paid": 1, "lines": [line]}
    else:
        from pearnly_erp_portal.purchasing import entry

        doctype = "Purchase Document"
        party = express_row("ERP Supplier", company, params.get("party"))
        payload = {"doc_kind": "purchase_invoice", "company": company, "supplier": party, "doc_no": f"SMOKE-{int(time.time())}",
                   "doc_date": frappe.utils.today(), "has_vat": 1, "price_mode": "exclusive", "is_paid": 1,
                   "lines": [{**line, "item_type": "goods"}]}
    if kind not in ("stock_out", "stock_in"):
        if not party:
            raise SystemExit(f"test data missing: party {params.get('party') or '(first)'} in {company}")
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
