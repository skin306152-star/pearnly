"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：一个自己一台 CC 桌面版电脑都没有、公司里也没有别人的电脑兜底的员工，
开一张现购单交代录 → 交单后单据页、代录对话进度、给模型的任务状态如实说「CC 桌面版未连接」（不是只写「排队 / 等待电脑」、不说
已交给电脑）→ 撤掉这一件、作废这张单。不写任何账套。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_operator_no_computer.py <站点> '<JSON 参数>'

参数：user（没有电脑的员工）、company_tax / branch（他所在的公司：要有 CC 报上来的账套，但够得到这本账套的电脑上登录过 CC 的人里
没有一个是这家公司的员工）、run、price。

开跑前核两样现场，不满足就拒跑（场景记前置条件不满足）：这个员工一台登录过 CC 的电脑都没有（`jobs.writers` = missing）；这家公司
没有办公室电脑兜底（`push.office_actor` 回他本人——兜底找到人时正确行为是那台电脑写，见 erp_office_actor.py，不是「未连接」）。
走的是代录背后的同一组服务端函数：建任务（`tasks.create`）→ 交单（写入计划「开始」用的 `express_operator.api.hand_in`）；
读的是单据页的原因（`push._queued_note`，每分钟记到单据上）、代录钩子 `computer_state`、对话投影（`feed.project`）与模型的
`express_operator_status`（`tools.status`）。登记里标 via = "api"。
"""
import base64
import json
import secrets
import sys
from unittest.mock import patch

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DIRECT_SEND = "pearnly_erp_portal.express.push.send"

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
made = {}
try:
    from pearnly_core.cc_desktop import books, jobs
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_core.express_operator import api as operator_api
    from pearnly_core.express_operator import feed, tasks, tools
    from pearnly_erp_portal.express import contracts, pending, push, target
    from pearnly_erp_portal.purchasing import entry

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    filters = {"tax_id": params["company_tax"], "is_active": 1}
    if params.get("branch"):
        filters["branch"] = params["branch"]
    company = frappe.db.get_value("ERP Company", filters, "name", order_by="name")
    if not company:
        raise SystemExit("unmet: no ERP company with that tax id on this site")
    tax, branch = frappe.db.get_value("ERP Company", company, ["tax_id", "branch"])
    result.update(company=company, reported=[item.get("path") for item in books.for_company(tax, branch)])
    if not result["reported"]:
        raise SystemExit("unmet: this company has no book reported by any CC Desktop")
    user, today = params["user"], frappe.utils.today()
    key = target.pick(push._company(company), today)["book"]["key"]
    mine = jobs.writers(user, key)["state"]
    office = push.office_actor(user, company, key)
    result.update(book=key, writers=mine, office_actor=office)
    if mine != "missing":
        raise SystemExit(f"unmet: {user} has signed in to CC Desktop on a computer ({mine}); the scenario needs an employee with none")
    if office != user:
        raise SystemExit(f"unmet: an office computer covers this company ({office}); with that the right outcome is that computer writing it")
    frappe.set_user(user)
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    product = frappe.db.get_value("ERP Product", by_code, ["name", "product_name"], order_by="express_code asc", as_dict=True)
    supplier = frappe.db.get_value("ERP Supplier", by_code, "name", order_by="express_code asc")
    warehouse = frappe.db.get_value("ERP Warehouse", {"company": company, "express_code": ["is", "set"]}, "name", order_by="express_code asc")
    if not (product and supplier and warehouse):
        raise SystemExit(f"unmet: test data missing in {company}: product={bool(product)} supplier={bool(supplier)} warehouse={bool(warehouse)}")
    price = float(params.get("price", 10))
    payload = {"doc_kind": "purchase_invoice", "company": company, "supplier": supplier, "doc_no": f"ACC-N{params['run']}",
               "doc_date": today, "has_vat": 1, "price_mode": "exclusive", "is_paid": 1,
               "lines": [{"product": product.name, "description": product.product_name, "qty": 1, "unit_price": price,
                          "warehouse": warehouse, "item_type": "goods"}]}
    real = frappe.enqueue

    def without_direct_send(method, *args, **kwargs):
        return None if method == DIRECT_SEND else real(method, *args, **kwargs)

    with patch.object(frappe, "enqueue", without_direct_send):
        posted = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")
        frappe.db.commit()
    doctype, name = "Purchase Document", posted["name"]
    made["doc"] = name
    task = tasks.create(company, today[:7])
    frappe.db.commit()
    made["task"] = task["name"]
    handed = operator_api.hand_in(company, task["name"], [f"P:{name}"])
    frappe.db.commit()
    row = frappe.db.get_value(doctype, name, ["name", "owner", "creation"] + ["ledger_state", "ledger_reason", "ledger_job", "ledger_book"],
                              as_dict=True)
    job = frappe.db.get_value("Pearnly CC Job", row.ledger_job, ["name", "status", "actor", "request_key"], as_dict=True) if row.ledger_job else None
    made["job"] = job.name if job else ""
    words = pending.reason_label("queued", contracts.NOT_CONNECTED)
    try:
        from pearnly_core.express_operator import products as operator_products

        hook = operator_products.computer_state(company, user) if hasattr(operator_products, "computer_state") else "<no computer_state hook>"
    except Exception as error:  # noqa: BLE001
        hook = f"<{type(error).__name__}>"
    status = json.loads(tools.status({"task": task["name"]}))
    events = feed.project(frappe.get_doc(tasks.DOCTYPE, task["name"]))
    shown = json.dumps(events, ensure_ascii=False, default=str)
    result.update(ok=True, doc=name, handed=handed, state={key_: row.get(key_) for key_ in ("ledger_state", "ledger_reason", "ledger_job")},
                  job=job, words=words, doc_note=push._queued_note(doctype, row) if row.ledger_job else "", computer_state=hook,
                  status={key_: status.get(key_) for key_ in ("status", "no_computer", "counts")},
                  feed={"says_not_connected": words in shown, "waiting_computer": '"waiting_computer"' in shown})
except SystemExit as stop:
    if str(stop) not in ("0", "None"):
        result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    # 收尾：撤掉排着的这一件、作废这张单、任务标停止（没有电脑会来领，也不留给以后登录的电脑）
    try:
        frappe.db.rollback()
        frappe.set_user("Administrator")
        if made.get("job") and frappe.db.get_value("Pearnly CC Job", made["job"], "status") in ("queued", "manual"):
            jobs.cancel(made["job"], reason="ACC no-computer scenario")
        if made.get("doc"):
            frappe.set_user(params["user"])
            entry.cancel(made["doc"], reason="ACC no-computer scenario")
        if made.get("task") and frappe.db.get_value(tasks.DOCTYPE, made["task"], "status") in ("queued", "running", "preparing"):
            frappe.db.set_value(tasks.DOCTYPE, made["task"], "status", "stopped", update_modified=False)
        frappe.db.commit()
        result["cleaned"] = made
    except Exception:
        frappe.db.rollback()
        result["cleanup_error"] = frappe.get_traceback()[-500:]
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
