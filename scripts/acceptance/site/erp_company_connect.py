"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：「从连接添加」把验收账套建成 / 接成公司，整条走一遍并把每一步
看到的交回——刷新让电脑重报、添加（新建 / 接管空默认公司 / 已添加不重复建）、主档同步落库、停用后重新启用、切换当前公司。
末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_company_connect.py <站点> '<JSON 参数>'

参数：user（能管公司的员工；CC 桌面版上登录的也是他）、company_tax / branch、ledger（验收账套路径，只认它）、
wait（主档同步最多等几秒，默认 300）、rescan_wait（重报最多等几秒，默认 40）、
reactivate（真：停用后从连接再添加一次，看是不是重新启用而不是另建；做完保持启用）、
require_fresh（真：开跑时这一行必须是「待添加」且站点里有空的默认公司，否则什么都不做就拒绝——守「首次添加接管空默认公司」用）、
switch（真：切换当前公司来回几次，每次核「库里已提交的值 / 生效的值 / 默认值缓存」三处一致；只有一家公司时先建一家不接账本的陪跑公司）。

走的是页面背后的同一组服务端函数（`masters.cc_service`：connection_list / rescan / rescan_pending / create_many，
`masters.company.set_active_company`），登记里标 via = "api"：证明服务端这条路对，弹窗排布、提示文字、按钮在真机行上验。
防护同别的验收站点脚本：只在测试租户跑；这个税号下 CC 报上来的账套必须全部就是 ledger，多报一本就拒绝、什么都不建。
"""
import base64
import json
import sys
import time

import frappe
from frappe.utils import nowdate

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
PROBE_COMPANY = "ACC switch probe"   # 切换当前公司要两家：只有一家时建的陪跑公司（不接账本、没有税号）


def norm(text) -> str:
    return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.account import user_defaults
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.express import contracts as express_contracts
    from pearnly_erp_portal.express import facts
    from pearnly_erp_portal.masters import cc_service
    from pearnly_erp_portal.masters import company as companies

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    COMPANY, user = "ERP Company", params["user"]
    tax, branch = express_contracts.tax13(params["company_tax"]), express_contracts.branch5(params.get("branch"))
    key = f"{tax}:{branch}"
    reported = [item.get("path") for item in books.for_company(tax, branch)]
    result["reported"] = reported
    if not reported or not all(norm(path) == norm(params["ledger"]) for path in reported):
        raise SystemExit("refuse: the books reported for this tax id are not exactly the acceptance ledger")
    frappe.set_user(user)

    def listed():
        frappe.db.rollback()
        found = cc_service.connection_list(search=tax)
        row = next((item for item in found["rows"] if item.get("account_set_key") == key and item.get("adapter") == "express"), None)
        return {"connected": found["connected"], "total": found["total"],
                "row": {name: row.get(name) for name in ("identity", "title", "tax_id", "branch", "state", "company", "linkable",
                                                         "years", "folders")} if row else None}

    def total():
        return frappe.db.count(COMPANY)

    result["before"] = {**listed(), "companies": total()}
    if params.get("require_fresh") and not ((result["before"]["row"] or {}).get("state") == "new" and cc_service._blank_default()):
        raise SystemExit("not a fresh site: the ledger row is not waiting to be added, or there is no blank default company")

    # ── 刷新：请在线电脑立刻整份重报，等它回执
    started = time.monotonic()
    asked = cc_service.rescan()
    frappe.db.commit()
    polls, state = [], {}
    while asked["devices"] and time.monotonic() - started < int(params.get("rescan_wait", 40)):
        time.sleep(2)
        frappe.db.rollback()
        # 回执记在站点缓存里：同一进程里 frappe.cache 先读进程内那份（frappe.local.cache），不清就一直是下发时的旧值
        # （11-16 首跑：电脑 7 秒就回执了，这里 40 秒都看成没回）；页面每次轮询是新请求，没有这层
        frappe.local.cache = {}
        state = cc_service.rescan_pending(asked["devices"])
        polls.append({"at": round(time.monotonic() - started, 1), **state})
        if not state["pending"] or not state["online"]:
            break
    result["rescan"] = {"devices": len(asked["devices"]), "seconds": round(time.monotonic() - started, 1), "last": state, "polls": polls,
                        "row_after": listed()["row"]}

    # ── 添加（同一份再添加一次：连点两次）
    blank = cc_service._blank_default()
    first_state = (listed()["row"] or {}).get("state")
    made = cc_service.create_many([{"connection": "", "account_set_key": key}])
    frappe.db.commit()
    after_first = total()
    again = cc_service.create_many([{"connection": "", "account_set_key": key}])
    frappe.db.commit()
    name = made[0]["id"]
    result["add"] = {"state_before": first_state, "blank_default": blank, "first": made[0], "again": again[0],
                     "companies_after_first": after_first, "companies_after_again": total(), "row_after": listed()["row"]}

    def company_view():
        frappe.db.rollback()
        doc = frappe.db.get_value(COMPANY, name, ["name", "company_name", "tax_id", "branch", "is_active", "is_default", "express_enabled",
                                                  "express_sync_state", "express_sync_note", "express_book", "express_linked_at",
                                                  "express_synced_at"], as_dict=True)
        return {**doc, "express_linked_at": str(doc.express_linked_at or ""), "express_synced_at": str(doc.express_synced_at or ""),
                "warehouses": frappe.db.count("ERP Warehouse", {"company": name})}

    # ── 主档同步：添加后自己排队、每分钟发起读取、读好落库（不替它点「同步 Express」）
    deadline, seen = time.monotonic() + int(params.get("wait", 300)), []
    while True:
        view = company_view()
        if not seen or seen[-1] != view["express_sync_state"]:
            seen.append(view["express_sync_state"])
        if view["express_sync_state"] in ("ready", "failed") or time.monotonic() > deadline:
            break
        time.sleep(5)
    by_code = {"express_company": name, "is_active": 1, "express_code": ["is", "set"]}
    # 资料按 40 位账套键存（express.facts），公司上的 express_book 只是账套显示名：按产品写账同一条规则（express.target.pick）取这家今天的账套
    from pearnly_erp_portal.express import target
    picked = target.pick(frappe.db.get_value(COMPANY, name, ["name", "company_name", "tax_id", "branch"], as_dict=True), nowdate(), None)
    book_key = (picked.get("book") or {}).get("key")
    data = facts.load(book_key) if book_key else None
    result["company"] = view
    result["sync"] = {"states": seen,
                      "customers": frappe.db.count("ERP Customer", by_code), "suppliers": frappe.db.count("ERP Supplier", by_code),
                      "products": frappe.db.count("ERP Product", by_code),
                      "nbsp_customers": len([code for code in frappe.get_all("ERP Customer", filters=by_code, pluck="express_code",
                                                                              limit_page_length=20000) if "\xa0" in str(code)]),
                      "book_key": book_key, "pick_reason": picked.get("reason"), "facts": bool(data), "stale_for_collection": bool(facts.stale_for_collection(data)) if data else None,
                      "receipt_types": len((data or {}).get("receipt_types") or []), "cash_account": bool((data or {}).get("cash_account"))}

    # ── 停用后从连接再添加：重新启用同一家，不另建
    if params.get("reactivate"):
        frappe.set_user("Administrator")
        doc = frappe.get_doc(COMPANY, name)
        doc.is_active = 0
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        frappe.set_user(user)
        before, inactive_row = total(), listed()["row"]
        back = cc_service.create_many([{"connection": "", "account_set_key": key}])
        frappe.db.commit()
        result["reactivate"] = {"row_inactive": inactive_row, "result": back[0], "companies_before": before, "companies_after": total(),
                                "row_after": listed()["row"], "is_active": company_view()["is_active"]}

    # ── 切换当前公司：每次切完三处一致（11-15：默认值缓存被并发回填旧值，点 3 次只第一次生效）
    if params.get("switch"):
        allowed = companies.allowed_companies(user)
        if len(allowed) < 2:
            frappe.set_user("Administrator")
            if not frappe.db.exists(COMPANY, {"company_name": PROBE_COMPANY}):
                frappe.get_doc({"doctype": COMPANY, "company_name": PROBE_COMPANY, "is_active": 1, "pos_enabled": 0,
                                "express_enabled": 0, "company_type": "general"}).insert(ignore_permissions=True)
                frappe.db.commit()
            frappe.set_user(user)
            allowed = companies.allowed_companies(user)
        other = next((item for item in allowed if item != name), None)
        rounds = []
        for target in ([other, name, other, name] if other and name in allowed else []):
            companies.set_active_company(target)
            frappe.db.commit()
            rounds.append({"target": target, "committed": user_defaults.current("company", user),
                           "cached": frappe.defaults.get_user_default("company", user), "effective": companies.active_company(user)})
        result["switch"] = {"allowed": len(allowed), "rounds": rounds}
    result.update(ok=True, name=name)
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
