"""流程用例：接 Express 的公司，出入库单上有 Express 里没有的物料（多仓闭环 P3x）。

实验栈没有真电脑读账套（`lab_agents` 只是智能体接口的替身），所以这一条在站点里临时放一份账套资料（`express.facts`），跑完在
``finally`` 里还原，不影响同一套数据上的其他流程；记住的科目、排上的写账件、主档同步状态同样还原 / 撤掉。

- new（接了 Express）：库存岗提交前问（`api.preflight`）→ 问「物料」一项、选项里有 Express 的现有存货和「新建」；选新建后再问 → 不再问、
  记在这家公司「stock|<物料>」名下；建单提交、交进写账队列 → 载荷盖 15、那一行 ``new_ok`` 且不带 ERP 物料；照电脑写好的样子回执
  （逐行回报建出的码）→ 物料挂上这家公司与这个码。Express 有同名存货的物料不问。
- old（没接 Express）：提交前问什么都不问。
"""
from __future__ import annotations

import uuid

TITLE = "ERP 出入库单遇 Express 里没有的物料：问一次选新建、载荷 15、写完回填编码"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    return lab.python(manifest["site"], f"""
import os
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.express import api, choices, facts, push, stock_entry, target
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service as products

frappe.set_user("admin@erp.regression")
company = companies.active_company("stock@erp.regression")
companies.set_active_company(company)
new = products.create_product(product_name="ของใหม่ {tag}", unit="ชิ้น", unit_price=100, default_cost=10)["name"]
same = products.create_product(product_name="สินค้าเดิม {tag}", unit="ชิ้น", unit_price=100, default_cost=10)["name"]
frappe.db.commit()
lines = [{{"product": new, "qty": 2, "unit": "ชิ้น", "unit_cost": 5}}, {{"product": same, "qty": 1, "unit": "ชิ้น", "unit_cost": 5}}]
out = {{"linked": push.linked(company), "new": new}}
frappe.set_user("stock@erp.regression")
if not out["linked"]:
    out["asked"] = api.preflight("ERP Stock Entry", json.dumps({{"direction": "in", "lines": lines}}))["needed"]
else:
    picked = target.pick(push._company(company), frappe.utils.nowdate())
    key = picked["book"]["key"]
    path = facts._path(key)
    kept = open(path, "rb").read() if os.path.exists(path) else None
    sync = frappe.db.get_value("ERP Company", company, "express_sync_state")
    account = choices._name(company, "account", stock_entry.account_subject("JU"))
    had_account = frappe.db.exists(choices.CHOICE, account)
    job = None
    try:
        facts.save(key, {{"version": 2, "customers": [], "suppliers": [], "departments": [],
                         "products": [{{"code": "EXP-OLD-{tag}", "name": "สินค้าเดิม {tag}", "kind": "stock"}},
                                      {{"code": "EXP-ANY-{tag}", "name": "อื่น ๆ", "kind": "stock"}}],
                         "facts": {{"accounts": [{{"code": "51-05-00-00", "name": "ปรับปรุงสินค้า", "type": "5"}}], "mapping": {{}},
                                   "catalog": {{"isacc_candidates": []}}}}}})
        if not had_account:
            choices.remember(company, "account", stock_entry.account_subject("JU"), "51-05-00-00")
        payload = json.dumps({{"direction": "in", "lines": lines}})
        asked = api.preflight("ERP Stock Entry", payload)["needed"]
        out["asked"] = asked
        item = next((row for row in asked if row["kind"] == "item"), None)
        if item:
            again = api.preflight("ERP Stock Entry", payload, json.dumps([{{"kind": "item", "subject": item["subject"], "value": "__new__"}}]))
            out["after"] = again["needed"]
        out["remembered"] = choices.chosen(company, "item", stock_entry.item_subject(new))
        house = frappe.get_all("ERP Warehouse", filters={{"company": company, "is_active": 1, "express_code": ["!=", ""]}},
                               pluck="name", order_by="express_code asc", limit_page_length=1)[0]
        entry = documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=house, submit=True, lines=lines)
        frappe.db.commit()
        name = entry["name"]
        push.send("ERP Stock Entry", name)
        row = frappe.db.get_value("ERP Stock Entry", name, ["express_state", "express_job", "express_reason"], as_dict=True)
        job = row.express_job
        sent = json.loads(frappe.db.get_value("Pearnly CC Job", job, "payload") or "{{}}") if job else {{}}
        out["entry"] = {{"state": row.express_state, "reason": row.express_reason, "version": sent.get("payload_version"),
                        "items": sent.get("items")}}
        # 照电脑写好的样子回执：逐行回报实际用的码（第 1 行是建出来的，第 2 行按同名认到的）
        push.job_changed({{"source_doctype": "ERP Stock Entry", "source_name": name, "status": "success", "job": job,
                          "docnum": "JU-FLOW-{tag}", "request_key": "erp:stock:flow-{tag}",
                          "line_codes": [{{"seq": 1, "name": "ของใหม่ {tag}", "code": "EXP-NEW-{tag}"}},
                                         {{"seq": 2, "name": "สินค้าเดิม {tag}", "code": "EXP-OLD-{tag}"}}]}})
        frappe.db.commit()
        out["linked_codes"] = {{product: frappe.db.get_value("ERP Product", product, ["express_company", "express_code"], as_dict=True)
                               for product in (new, same)}}
        out["company"] = company
    finally:
        if kept is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "wb") as handle:
                handle.write(kept)
        if not had_account and frappe.db.exists(choices.CHOICE, account):
            frappe.delete_doc(choices.CHOICE, account, ignore_permissions=True, force=True)
        if job and frappe.db.get_value("Pearnly CC Job", job, "status") in ("queued", "leased"):
            jobs.cancel(job, "replaced")
        frappe.db.set_value("ERP Company", company, "express_sync_state", sync, update_modified=False)
        frappe.db.commit()
print(json.dumps(out, default=str))
""")


def steps(params, camera_code=None):
    return []  # 全在站点里核（提交前问的弹窗是采购 / 销售同一个组件，由 erp.document.express 等流程覆盖）


def judge(params, results, camera_code=None):
    return []


def verify(lab, manifest, params, camera_code=None):
    asked = params.get("asked") or []
    if not params.get("linked"):
        return [{"case": "a company without Express is not asked anything", "seen": asked, "ok": asked == []}]
    item = [row for row in asked if row.get("kind") == "item"]
    options = [option.get("code") for option in (item[0].get("options") if item else []) or []]
    entry = params.get("entry") or {}
    items = entry.get("items") or []
    codes = params.get("linked_codes") or {}
    new, company = params["new"], params.get("company")
    return [
        {"case": "only the product Express does not have is asked, once", "seen": asked,
         "ok": len(item) == 1 and item[0].get("subject") == f"stock|{new}" and len(asked) == 1},
        {"case": "the choices are the account set's items and new", "seen": options,
         "ok": "__new__" in options and any(code.startswith("EXP-OLD-") for code in options)},
        {"case": "after choosing new nothing more is asked and it is remembered", "seen": [params.get("after"), params.get("remembered")],
         "ok": params.get("after") == [] and params.get("remembered") == "__new__"},
        {"case": "the job carries payload 15 with the new line marked and no ERP product", "seen": entry,
         "ok": entry.get("version") == 15 and len(items) == 2 and items[0].get("new_ok") is True
         and "new_ok" not in items[1] and all("product" not in line for line in items)},
        {"case": "the codes the computer used are linked back to both products", "seen": codes,
         "ok": all((codes.get(product) or {}).get("express_company") == company for product in codes)
         and (codes.get(new) or {}).get("express_code", "").startswith("EXP-NEW-")},
    ]
