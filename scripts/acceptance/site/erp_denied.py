"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：用一个什么岗位都没有的员工去碰收款与「从连接添加」的每个写入口，
每一处都应被服务端拒绝、什么都不留。把每一处的结果与前后记录数交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_denied.py <站点> '<JSON 参数>'

参数：company_tax / branch（接了验收账套的那家公司，只用来指明「拒的是哪家」）、ledger。

没岗位的员工 `acc.denied@<站点>` 不存在就在实验栈里建一个（启用、不发任何角色，只动测试租户的站点数据）。
走的是页面 / LINE / CC 背后的同一组服务端函数；权限判断在服务端第一步，所以不需要真的发票与客户。只在测试租户跑。
"""
import base64
import json
import sys

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.collection import service
    from pearnly_erp_portal.express import contracts as express_contracts
    from pearnly_erp_portal.masters import cc_service

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    company = frappe.db.get_value("ERP Company", {"tax_id": params["company_tax"], "is_active": 1}, "name")
    if not company:
        raise SystemExit("no active company with this tax id")
    user = f"acc.denied@{site}"
    frappe.set_user("Administrator")
    if not frappe.db.exists("User", user):
        frappe.get_doc({"doctype": "User", "email": user, "first_name": "ACC denied", "enabled": 1, "send_welcome_email": 0,
                        "user_type": "System User"}).insert(ignore_permissions=True)
        frappe.db.commit()
    result["roles"] = sorted(set(frappe.get_roles(user)) - {"All", "Guest", "Desk User"})
    key = f"{express_contracts.tax13(params['company_tax'])}:{express_contracts.branch5(params.get('branch'))}"
    counts = lambda: {"collections": frappe.db.count(service.DOCTYPE), "companies": frappe.db.count("ERP Company"),  # noqa: E731
                      "jobs": frappe.db.count("Pearnly CC Job")}
    before = counts()
    frappe.set_user(user)
    invoice = [{"docnum": "IV-ACC-NONE", "amount": "1"}]
    tries = {
        "web.submit": lambda: service.submit_web(company, "ACC-NONE", invoice, cash="1", request="acc-denied"),
        "line.draft": lambda: service.create_draft(company, "ACC-NONE", invoice, cash="1", source="line"),
        "cc.draft": lambda: service.create_draft(company, "ACC-NONE", invoice, cash="1", source="cc"),
        "collection.invoices": lambda: service.open_invoices(company, "ACC-NONE"),
        "collection.methods": lambda: service.methods(company),
        "connect.list": lambda: cc_service.connection_list(),
        "connect.rescan": lambda: cc_service.rescan(),
        "connect.add": lambda: cc_service.create_many([{"connection": "", "account_set_key": key}]),
    }
    outcome = {}
    for name, attempt in tries.items():
        try:
            attempt()
            outcome[name] = "allowed"
        except frappe.PermissionError:
            outcome[name] = "denied"
        except Exception as error:  # noqa: BLE001 —— 别的拒法也记下来给场景判
            outcome[name] = f"other: {type(error).__name__}: {str(error)[:120]}"
        frappe.db.rollback()
    frappe.set_user("Administrator")
    result.update(ok=True, user=user, outcome=outcome, before=before, after=counts())
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
