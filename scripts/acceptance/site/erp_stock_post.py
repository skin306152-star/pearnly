"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：给接了验收账套的那家公司按顺序做几步库存单据（入库 / 出库 / 调拨 /
物料调整 / 盘点生成调整单 / 作废前面某一张），每一步等 CC 桌面版写完回执再做下一步，把每张单的写账状态、落点账套、期望值交回。
末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_stock_post.py <站点> '<JSON 参数>'

参数：user、company_tax / branch、ledger（验收账套路径，只认它）、run（本次运行标记）、wait（每一步最多等几秒，默认 300）、
steps：按顺序的几步，每步 ``{"kind": …}``：
  in / out      ``qty``、``unit_cost``（入库用）                 → Express 调整 JU / 领用 OU
  adjust        ``qty``（带正负）                                → Express 盘点调整 TK
  transfer      ``qty``（调出仓 = 主仓，调入仓 = 另一个接了 Express 仓库码的仓；没有第二个仓就拒绝）→ Express 调拨 RL
  count         ``delta``（实盘比账面多几件）：新建盘点 → 录实盘数 → 结束 → 按盘点结果生成调整单草稿 → 提交 → TK
  void          ``of``（作废第几步建的那张，0 起）：已写进 Express 的库存单作废，等原单在账本里改成作废

走的是页面背后的同一组服务端函数（`inventory.documents.create_entry / submit_entry / void_entry`、`inventory.count`），登记里标
via = "api"。出入库单按员工的当前公司认仓：只在这个进程里把当前公司指向要写的这家，不改员工设置（同 `scripts/smoke/express_flow.py`）。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger；写完按单据记下的账套核实际路径。
"""
import base64
import json
import secrets
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DOCTYPE = "ERP Stock Entry"
FIELDS = ["ledger_state", "ledger_docnum", "ledger_reason", "ledger_job", "ledger_book", "ledger_needed", "ledger_void_state",
          "ledger_void_job", "ledger_void_reason", "status", "direction", "doc_no"]


def same_path(left, right) -> bool:
    def norm(text):
        return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()
    return norm(left) == norm(right)


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False, "docs": []}
try:
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.express import push
    from pearnly_erp_portal.inventory import count as counts
    from pearnly_erp_portal.inventory import documents
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
    frappe.set_user(params["user"])
    by_code = {"express_company": company, "is_active": 1, "express_code": ["is", "set"]}
    product = frappe.db.get_value("ERP Product", by_code, ["name", "product_name", "express_code"], order_by="express_code asc", as_dict=True)
    places = frappe.get_all("ERP Warehouse", filters={"company": company, "is_active": 1, "express_code": ["is", "set"]},
                            fields=["name", "express_code"], order_by="express_code asc", limit_page_length=20)
    if not (product and places):
        raise SystemExit(f"test data missing in {company}: product={bool(product)} warehouses={len(places)}")
    main = places[0]
    result.update(company=company, item_code=product.express_code, warehouse_code=main.express_code,
                  warehouses=[item.express_code for item in places])
    wait = int(params.get("wait", 300))

    def settled(name, void=False):
        """等这张单的写账（或作废）有结果，回它此刻的样子、写账件的读回、落点账套。"""
        deadline, done = time.monotonic() + wait, ("voided", "held", "failed") if void else ("written", "mismatch", "held", "failed")
        while True:
            frappe.db.rollback()
            state = frappe.db.get_value(DOCTYPE, name, FIELDS, as_dict=True) or {}
            if state.get("ledger_void_state" if void else "ledger_state") in done or time.monotonic() > deadline:
                break
            time.sleep(5)
        job_name = state.get("ledger_void_job" if void else "ledger_job")
        job = frappe.db.get_value("Pearnly CC Job", job_name, ["status", "verify_state", "reason"], as_dict=True) if job_name else None
        paths = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": state.get("ledger_book")}, fields=["path"])] \
            if state.get("ledger_book") else []
        return {"name": name, "state": state, "job": job, "book_paths": paths}

    def entry(direction, lines, **more):
        # 出入库单按员工的当前公司认仓（单据上没有公司栏）：只在这个进程里把当前公司指向要写的这家，不改员工设置
        active = companies.active_company
        companies.active_company = lambda user=None: company
        try:
            posted = documents.create_entry(direction=direction, posting_date=frappe.utils.today(), warehouse=main.name, lines=lines,
                                            reason=f"ACC-{params['run']}-{secrets.token_hex(2)}", submit=True, **more)
        finally:
            companies.active_company = active
        frappe.db.commit()
        return posted["name"]

    def send(name):
        try:
            push.send(DOCTYPE, name)   # 提交时已经排过；这里只是不等后台任务（同冒烟），重复推由服务端自己挡
            frappe.db.commit()
        except Exception:  # noqa: BLE001
            frappe.db.rollback()

    for index, step in enumerate(params["steps"]):
        kind, qty = step["kind"], step.get("qty", 1)
        out = {"kind": kind, "qty": qty}
        if kind in ("in", "out"):
            line = {"product": product.name, "qty": qty, **({"unit_cost": step.get("unit_cost", 10)} if kind == "in" else {})}
            name = entry(kind, [line], purpose="other")
        elif kind == "adjust":
            name = entry("adjust", [{"product": product.name, "qty": qty}])
        elif kind == "transfer":
            if len(places) < 2:
                raise SystemExit("this ledger has only one Express warehouse: a transfer needs two")
            out["to_warehouse_code"] = places[1].express_code
            name = entry("transfer", [{"product": product.name, "qty": qty}], to_warehouse=places[1].name)
        elif kind == "count":
            active = companies.active_company
            companies.active_company = lambda user=None: company
            try:
                made = counts.create_count(title=f"ACC {params['run']} {secrets.token_hex(2)}", warehouse=main.name)
                frappe.db.commit()
                book_qty = float(frappe.db.get_value(counts.BALANCE, {"warehouse": main.name, "product": product.name}, "qty_on_hand") or 0)
                counted = book_qty + float(step.get("delta", 1))
                counts.add_entry(count=made["name"], qty=counted, product=product.name, warehouse=main.name,
                                 request_id=f"acc-{secrets.token_hex(6)}")
                frappe.db.commit()
                closed = counts.close_count(made["name"])
                frappe.db.commit()
                draft = counts.adjustment_draft(made["name"])
                frappe.db.commit()
                again = counts.adjustment_draft(made["name"])   # 再点一次「按盘点结果调整物料」：回同一张，不生成第二张
                frappe.db.commit()
                documents.submit_entry(draft["name"])
            finally:
                companies.active_company = active
            name, qty = draft["name"], float(step.get("delta", 1))
            out.update(count=made["name"], book_qty=book_qty, counted=counted, closed=closed.get("status"), draft_created=draft.get("created"),
                       draft_again=[again.get("name") == draft["name"], bool(again.get("created"))], qty=qty)
        elif kind == "void":
            target = result["docs"][int(step["of"])]
            name = target["name"]
            voided = documents.void_entry(name)
            out.update(of=int(step["of"]), status=voided.get("status"), docnum=target["state"].get("ledger_docnum"), **settled(name, void=True))
            result["docs"].append(out)
            continue
        else:
            raise SystemExit(f"unknown step kind {kind}")
        send(name)
        out.update(settled(name))
        if kind == "count":
            out["count_status"] = frappe.db.get_value("ERP Stock Count", out["count"], "status")
        result["docs"].append(out)
        if out["state"].get("ledger_state") != "written":
            break   # 这一步没写成：后面的步骤依赖它，不再往下做
    result["ok"] = True
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
