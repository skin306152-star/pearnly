"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：给接了验收账套的那家公司开班、卖几张现金小票，把当天的小票合成一张
收银日汇总 → 进写账队列 → 等 CC 桌面版写成现销单（HS）回执 → 把日汇总、写账状态、落点账套、期望值交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_pos_day.py <站点> '<JSON 参数>'

参数：user（有收银权限的员工）、company_tax / branch、ledger（验收账套路径，只认它）、run、receipts（卖几张，默认 2）、
qty（每张几件，默认 1）、price（单价，默认 50）、wait（最多等几秒，默认 300）。

走的是收银台背后的同一组服务端函数（`cashier.shifts.open_shift`、`cashier.sales.create_sale`）与每晚任务用的 `express.pos_day.summarize`
（每晚任务只处理今天以前的，这里对今天直接调），登记里标 via = "api"。只收现金（非现金方式各要问一次收款科目）。
这家公司没开收银时在实验栈里替它打开（`pos_enabled`，只动测试租户的站点数据）；当前公司只在这个进程里指向这家，不改员工设置。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger；写完按单据记下的账套核实际路径。
"""
import base64
import json
import secrets
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DOCTYPE = "ERP POS Day Summary"


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
    from pearnly_erp_portal.cashier import sales, shifts
    from pearnly_erp_portal.express import pos_day, push
    from pearnly_erp_portal.masters import company as companies

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
    if not frappe.db.get_value("ERP Company", company, "express_linked_at"):
        raise SystemExit("this company has not finished its first masters sync (no link time): receipts would not be summarised")
    if not frappe.db.get_value("ERP Company", company, "pos_enabled"):
        frappe.db.set_value("ERP Company", company, "pos_enabled", 1)
        frappe.db.commit()
        result["pos_enabled_here"] = True
    frappe.set_user(params["user"])
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    product = frappe.db.get_value("ERP Product", by_code, ["name", "express_code"], order_by="express_code asc", as_dict=True)
    if not product:
        raise SystemExit(f"test data missing in {company}: no ledger product")
    count, qty, price = int(params.get("receipts", 2)), float(params.get("qty", 1)), float(params.get("price", 50))
    today = frappe.utils.today()
    if params.get("forget_vat"):
        # 「问一次 → 记住」那一格：先把公司上记的「是不是增值税登记户」清空，造出第一次写日汇总的现场（只动测试租户的站点数据）
        result["vat_before"] = frappe.db.get_value("ERP Company", company, "vat_registered") or ""
        frappe.db.set_value("ERP Company", company, "vat_registered", "", update_modified=False)
        frappe.db.commit()
    active = companies.active_company
    companies.active_company = lambda user=None: company
    try:
        if not shifts.current_shift():
            shifts.open_shift(opening_float=0, note=f"ACC {params['run']}")
            frappe.db.commit()
        sold = []
        for _index in range(count):
            made = sales.create_sale(lines=[{"product": product.name, "qty": qty, "unit_price": price}],
                                     payments=[{"method": "cash", "amount": qty * price}],
                                     client_uuid=f"acc-{params['run']}-{secrets.token_hex(4)}")
            frappe.db.commit()
            sold.append(made["name"])
        frappe.set_user("Administrator")   # 日汇总是后台任务的活（每晚、不带员工身份）
        name = pos_day.summarize(company, today)
        frappe.db.commit()
        again = pos_day.summarize(company, today)   # 再合一次：小票都已进了那张，没有第二张
        frappe.db.commit()
    finally:
        companies.active_company = active
    if not name:
        raise SystemExit("no receipt was summarised (sold before the company was linked?)")
    try:
        push.send(DOCTYPE, name)
        frappe.db.commit()
    except Exception:  # noqa: BLE001 —— 提交时已经排过；重复推由服务端自己挡
        frappe.db.rollback()
    fields = ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book", "ledger_needed", "doc_kind", "sales_count",
              "grand_total", "day"]
    def wait():
        deadline, seen = time.monotonic() + int(params.get("wait", 300)), {}
        while time.monotonic() < deadline:
            frappe.db.rollback()
            seen = frappe.db.get_value(DOCTYPE, name, fields, as_dict=True) or {}
            if seen.get("ledger_state") in ("written", "mismatch", "held", "failed"):
                break
            time.sleep(5)
        return seen

    state = wait()
    # 这家公司第一次写日汇总时，产品在单据上问一次「是不是增值税登记户」（express.pos_day.VAT_REGISTERED，记在公司上，之后不再问）：
    # 场景给了答案（params.vat_registered）就照用户的正路在单据上回答（express.api.answer），再等写入
    needed = frappe.parse_json(state.get("ledger_needed")) if isinstance(state.get("ledger_needed"), str) else state.get("ledger_needed")
    asked = [item.get("kind") for item in needed or [] if isinstance(item, dict)]
    if state.get("ledger_state") == "held" and asked == ["vat_registered"] and params.get("vat_registered"):
        from pearnly_erp_portal.express import api as express_api
        frappe.set_user(params["user"])
        express_api.answer(DOCTYPE, name, [{"kind": "vat_registered", "subject": "company", "value": params["vat_registered"]}])
        frappe.db.commit()
        frappe.set_user("Administrator")
        result["answered"] = {"asked": asked, "vat_registered": params["vat_registered"], "held": dict(state)}
        state = wait()
    job = frappe.db.get_value("Pearnly CC Job", state.get("ledger_job"), ["status", "verify_state", "reason"], as_dict=True) \
        if state.get("ledger_job") else None
    written = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
        if state.get("ledger_book") else []
    in_summary = frappe.db.count("ERP Sale", {"name": ["in", sold], "express_summary": name})
    result.update(ok=True, company=company, doc=name, again=again, state=state, job=job, book_paths=written, receipts=sold,
                  expected={"item_code": product.express_code, "receipts": count, "in_summary": in_summary, "qty": qty * count,
                            "grand_total": float(state.get("grand_total") or 0), "sold_total": qty * price * count})
    if params.get("second_round") and state.get("ledger_state") == "written":
        # 第二张日汇总（同一天再卖、再合）：公司上已记了答案，这一张不该再问，直接写成
        companies.active_company = lambda user=None: company
        try:
            frappe.set_user(params["user"])
            sold_two = []
            for _index in range(count):
                made = sales.create_sale(lines=[{"product": product.name, "qty": qty, "unit_price": price}],
                                         payments=[{"method": "cash", "amount": qty * price}],
                                         client_uuid=f"acc-{params['run']}-b-{secrets.token_hex(4)}")
                frappe.db.commit()
                sold_two.append(made["name"])
            frappe.set_user("Administrator")
            second = pos_day.summarize(company, today)
            frappe.db.commit()
        finally:
            companies.active_company = active
        if second:
            first_name, name = name, second
            try:
                push.send(DOCTYPE, name)
                frappe.db.commit()
            except Exception:  # noqa: BLE001
                frappe.db.rollback()
            seen = wait()
            need = frappe.parse_json(seen.get("ledger_needed")) if isinstance(seen.get("ledger_needed"), str) else seen.get("ledger_needed")
            result["second"] = {"doc": name, "state": seen, "asked": [item.get("kind") for item in need or [] if isinstance(item, dict)],
                                "receipts": sold_two, "sold_total": qty * price * count,
                                "job": frappe.db.get_value("Pearnly CC Job", seen.get("ledger_job"), ["status", "verify_state", "reason"],
                                                           as_dict=True) if seen.get("ledger_job") else None}
            name = first_name
        else:
            result["second"] = {"doc": None}
    result["vat_after"] = frappe.db.get_value("ERP Company", company, "vat_registered") or ""
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
