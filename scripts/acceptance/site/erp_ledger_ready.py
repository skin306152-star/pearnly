"""验收场景的站点侧准备（在实验栈的 ERP 网页容器里跑）：看验收账套在这家公司名下是不是唯一报上来的一本、录入人与主档齐不齐。
末行打印 ACC {json}。

    env/bin/python /tmp/smoke-erp_ledger_ready.py <站点> '<JSON 参数>'

参数：user（测试机 CC 桌面版上登录的员工）、company_tax / branch、ledger（验收账套路径）、
drop_stale（真：把这个税号下路径不是 ledger 的账套行删掉——只给实验栈用，那是导入线上数据带来的旧行或别的电脑报的测试套）、
sync（真：对选中的公司跑一次主档同步，最多等 wait 秒）。
不带 drop_stale / sync 时只读。只在测试租户跑。
"""
import base64
import json
import sys

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]


def norm(text) -> str:
    return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.cc_desktop import books, contracts, devices
    from pearnly_core.execution.replay import TEST_SITES

    if not TEST_SITES.fullmatch(site):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    tax, ledger = params["company_tax"], params["ledger"]
    now = devices.now()
    result["devices"] = [{"name": d.name, "label": d.device_label, "signed_in_user": d.signed_in_user, "users": d.signed_in_users,
                          "seen": str(d.last_seen_at), "online": bool(contracts.online(d.last_seen_at, now)), "version": d.companion_version,
                          "retired": str(d.retired_at or "")}
                         for d in frappe.get_all("Pearnly CC Device", fields=["name", "device_label", "signed_in_user", "signed_in_users",
                                                                               "last_seen_at", "companion_version", "retired_at"])]
    rows = frappe.get_all(books.BOOK, filters={"tax_id": tax}, fields=["name", "device", "path", "root", "branch", "writable", "seen_at", "book_key"])
    result["books"] = [{**row, "seen_at": str(row.seen_at)} for row in rows]
    stale = [row for row in rows if norm(row.path) != norm(ledger)]
    if params.get("drop_stale") and stale:
        for row in stale:
            frappe.delete_doc(books.BOOK, row.name, ignore_permissions=True, force=True)
        frappe.db.commit()
        result["dropped"] = [row.path for row in stale]
    filters = {"tax_id": tax}
    if params.get("branch"):
        filters["branch"] = params["branch"]
    fields = ["name", "company_name", "branch", "is_active", "express_enabled", "express_linked_at", "express_sync_state", "express_sync_note"]
    companies = frappe.get_all("ERP Company", filters=filters, fields=fields, order_by="name")
    result["companies"] = [{**c, "express_linked_at": str(c.express_linked_at or "")} for c in companies]
    for c in companies:
        c_books = [item.get("path") for item in books.for_company(tax, c.branch)]
        by_code = {"express_company": c.name, "is_active": 1, "express_code": ["is", "set"]}
        result.setdefault("per_company", {})[c.name] = {
            "reported": c_books,
            "products": frappe.db.count("ERP Product", by_code), "suppliers": frappe.db.count("ERP Supplier", by_code),
            "warehouses": frappe.get_all("ERP Warehouse", filters={"company": c.name}, fields=["name", "express_code"], limit=20)}
        if params.get("sync") and c.is_active and c_books and all(norm(p) == norm(ledger) for p in c_books):
            from pearnly_erp_portal.express import masters

            result["per_company"][c.name]["sync"] = str(masters.sync(c.name, wait=int(params.get("wait", 180))))
            frappe.db.commit()
            result["per_company"][c.name].update(products=frappe.db.count("ERP Product", by_code), suppliers=frappe.db.count("ERP Supplier", by_code))
    user = params.get("user")
    if user:
        result["user"] = {"exists": bool(frappe.db.exists("User", user)), "enabled": frappe.db.get_value("User", user, "enabled"),
                          "type": frappe.db.get_value("User", user, "user_type"), "roles": frappe.get_roles(user),
                          "companies": frappe.get_all("User Permission", filters={"user": user, "allow": "ERP Company"}, pluck="for_value")}
    result["ok"] = True
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    result["why"] = frappe.get_traceback()[-1200:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
