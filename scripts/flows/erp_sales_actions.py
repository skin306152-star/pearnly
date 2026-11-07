"""流程用例：ERP 销售单页头动钱 / 改账的按钮逐个实点（清产品地图「没测试」存量基线，11-07）。

销售岗打开销售记录：
- 列表页头「新建」：点按钮（不是 `set_route`），进到新建页。
- 草稿「删除」：搜出 prepare 建的报价草稿、勾选它，页头变成「删除 (1)」，点下去、原生确认框点是 → 库里这张草稿没了。
- 已提交未收的销售单：页头「收款」开原生弹窗，金额 50、转账提交 → 库里已收 50、方式转账、收款状态部分收。
- 另一张已提交、出了库的销售单：「…」菜单「作废」开弹窗填原因确认 → 库里作废、原因是填的那句、出的那 1 件回到账面。
- 「Express」：新开户（公司接了 Express）打开一张排进账本的销售单点「Express」→ 出弹窗、取状态走 POST、库里写账状态不变；
  老数据（没接 Express）没有这颗按钮、不请求这个接口（同 `erp_document_express.py` 的采购版）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, codes, company, js_value, stock_of, warehouses  # noqa: E402

TITLE = "ERP 销售单页头动作：新建、删草稿、收款、作废、Express 弹窗"
USER = "sales@erp.regression"
API = "pearnly_erp_portal.express.api.document_express"

OPEN = ("const open=async(name,no)=>{frappe.set_route('pearnly-sales', name);"
        "const t=await until(()=>(frappe.pages['pearnly-sales']?.page?.title||'').trim()===no&&$.active===0, 25000); await idle(); await sleep(500);"
        "window.__opened=t?'':JSON.stringify({route: frappe.get_route(), title: frappe.pages['pearnly-sales']?.page?.title}); return !!t;};")
DIALOG = ("const dialog=async(values)=>{const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000);"
          "if(!d) return null; await d.set_values(values); await sleep(300); d.get_primary_btn().click();"
          "await until(()=>!d.$wrapper.is(':visible'), 20000); await idle(); return {open: d.$wrapper.is(':visible'), msg: msg()};};")


def prepare(lab, manifest):
    co = company(manifest)
    wh = warehouses(manifest)[0]["name"]
    product = codes(manifest)["by_code"]["product"]
    tag = uuid.uuid4().hex[:6]
    out = lab.python(manifest["site"], f"""
import time
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies, customers
from pearnly_erp_portal.sales import entry
co, wh, product = {co!r}, {wh!r}, {product!r}
# 先入 1 件到这个仓的不分库位（作废要把卖出去的那件还回来，不靠别的流程留下的库存）
frappe.set_user("admin@erp.regression")
companies.set_active_company(co)
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=wh, reason="flow seed {tag}", submit=True,
                       lines=[{{"product": product, "qty": 1, "unit_cost": 10, "location": ""}}])
frappe.db.commit()
frappe.set_user({USER!r})
companies.set_active_company(co)
customer = frappe.db.get_value("ERP Customer", {{"customer_name": "ลูกค้าโฟลว์ปุ่ม"}}) or customers.create_customer(customer_name="ลูกค้าโฟลว์ปุ่ม")["name"]
frappe.db.commit()
head = {{"company": co, "customer": customer, "doc_date": frappe.utils.nowdate(), "has_vat": 1, "vat_rate": 7, "source": "manual",
         "doc_kind": "receipt", "warehouse": wh}}
service = {{"item_type": "service", "description": "flow service {tag}", "qty": 1, "unit": "งาน", "unit_price": 200, "vat_applicable": 1}}
collecting = entry.post(dict(head, is_paid=0, paid_amount=0, lines=[service]), request_id="flow-collect-{tag}")
viewing = entry.post(dict(head, is_paid=1, payment_method="cash", lines=[service]), request_id="flow-view-{tag}")
voiding = entry.post(dict(head, is_paid=1, payment_method="cash",
                          lines=[{{"product": product, "item_type": "goods", "description": "flow goods {tag}", "qty": 1, "unit": "ชิ้น",
                                  "unit_price": 100, "vat_applicable": 1, "warehouse": wh, "location": ""}}]), request_id="flow-void-{tag}")
draft = frappe.get_doc({{"doctype": "ERP Sales Document", "company": co, "status": "draft", "doc_kind": "quotation",
                        "doc_date": frappe.utils.nowdate(), "buyer_name": "flow draft {tag}", "has_vat": 0,
                        "lines": [{{"description": "flow draft line", "qty": 1, "unit": "pcs", "unit_price": "10", "vat_applicable": 0}}]}})
draft.flags.ignore_permissions = True
draft.insert()
frappe.db.commit()
# 接了 Express 的公司：建单排进去的推送任务会把状态从 queued 推到下一步，等它稳定再记「看之前」的状态（同 erp_document_express）
for _ in range(60):
    frappe.db.rollback()
    if frappe.db.get_value("ERP Sales Document", viewing["name"], "ledger_state") != "queued":
        break
    time.sleep(1)
rows = {{key: frappe.db.get_value("ERP Sales Document", name, ["name", "system_no", "ledger_state", "ledger_job"], as_dict=True)
        for key, name in (("collecting", collecting["name"]), ("viewing", viewing["name"]), ("voiding", voiding["name"]))}}
print(json.dumps({{"rows": rows, "draft": draft.name}}, default=str))
""")
    rows = out["rows"]
    return {"company": co, "warehouse": wh, "product": product, "tag": tag,
            "before": stock_of(lab, manifest["site"], product, wh) + 1,  # 作废后应回到「卖出前」= 现在 + 1
            "linked": bool(manifest.get("express_linked")),
            "collecting": rows["collecting"]["name"], "collecting_no": rows["collecting"]["system_no"],
            "viewing": rows["viewing"]["name"], "viewing_no": rows["viewing"]["system_no"],
            "state": rows["viewing"].get("ledger_state") or "", "job": rows["viewing"].get("ledger_job") or "",
            "voiding": rows["voiding"]["name"], "voiding_no": rows["voiding"]["system_no"],
            "draft": out["draft"], "draft_buyer": f"flow draft {tag}", "reason": f"flow void {tag}", "cases": []}


def steps(params, camera_code=None):
    v = {k: js_value(params[k]) for k in ("collecting", "collecting_no", "viewing", "viewing_no", "voiding", "voiding_no",
                                          "draft", "draft_buyer", "reason")}
    hook = ("window.__calls=[]; const _ajax=$.ajax; $.ajax=function(o){ if(o&&String(o.url||'').includes(API)) "
            "window.__calls.push(String(o.type||o.method||'GET').toUpperCase()); return _ajax.apply(this, arguments); };").replace(
        "API", json.dumps(API))
    return [
        {"name": "列表新建", "js": JS + ACTION + """
await until(()=>pageOf('pearnly-sales')&&document.querySelector('.result'), 25000); await idle();
const via=await action(__('New')); if(!via) return {error:'no New button on the list'};
const moved=await until(()=>frappe.get_route()[1]==='new', 15000); await idle();
const route=frappe.get_route().join('/'); frappe.set_route('pearnly-sales'); await sleep(800); await idle();
return {via, route, moved: !!moved};"""},
        {"name": "删除草稿", "js": JS + f"""
await until(()=>document.querySelector('.result'), 25000); await idle();
setInput(document.querySelector('.page-form [data-fieldname="q"] input'), {v['draft_buyer']}); await sleep(600); await idle();
const sel='.list-row-checkbox[data-name='+JSON.stringify({v['draft']})+']';
const box=await until(()=>document.querySelector(sel), 20000); if(!box) return {{error:'draft not in the list'}};
box.click(); await sleep(400);
const btn=await until(()=>all('.page-head .primary-action, .page-head .btn-primary').find(b=>b.innerText.trim().startsWith(__('Delete'))), 8000);
if(!btn) return {{error:'no Delete button after selecting the draft'}}; const label=btn.innerText.trim(); btn.click();
const yes=await until(()=>all('.modal.show .btn-primary')[0], 8000); if(!yes) return {{error:'no confirm dialog'}}; yes.click();
const gone=await until(()=>!document.querySelector(sel), 20000); await idle();
return {{label, gone: !!gone}};"""},
        {"name": "收款", "js": JS + ACTION + OPEN + DIALOG + f"""
if(!await open({v['collecting']}, {v['collecting_no']})) return {{error:'unpaid sale did not open '+window.__opened}};
const via=await action(__('Collection')); if(!via) return {{error:'no Collection button'}};
const done=await dialog({{amount: 50, method: 'transfer'}}); if(!done) return {{error:'no collection dialog'}};
return {{via, ...done}};"""},
        {"name": "Express 弹窗", "js": JS + ACTION + OPEN + f"""
if(!await open({v['viewing']}, {v['viewing_no']})) return {{error:'sale did not open '+window.__opened}};
{hook}
const via=await action('Express'); if(!via) return {{button:false, calls:window.__calls}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 20000); await idle();
const body=d?(d.$body.text()||'').replace(/\\s+/g,' ').trim():''; if(d) d.hide(); await idle();
return {{button:true, via, dialog: body.slice(0, 300), calls:window.__calls}};"""},
        {"name": "作废", "js": JS + ACTION + OPEN + DIALOG + f"""
if(!await open({v['voiding']}, {v['voiding_no']})) return {{error:'stocked sale did not open '+window.__opened}};
const via=await action(__('Cancel')); if(!via) return {{error:'no Cancel item'}};
const done=await dialog({{reason: {v['reason']}}}); if(!done) return {{error:'no cancel dialog'}};
return {{via, ...done}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    new, drop, express = by.get("列表新建", {}), by.get("删除草稿", {}), by.get("Express 弹窗", {})
    calls = express.get("calls") or []
    checks = [
        {"case": "the list New button opens the new page", "seen": new, "ok": bool(new.get("via")) and new.get("route") == "pearnly-sales/new"},
        {"case": "selecting the draft turns the head button into Delete and the row goes away", "seen": drop,
         "ok": (drop.get("label") or "").endswith("(1)") and drop.get("gone") is True},
        {"case": "collection and cancel dialogs closed without a message", "seen": {k: by.get(k) for k in ("收款", "作废")},
         "ok": all((by.get(k) or {}).get("open") is False and not (by.get(k) or {}).get("msg") for k in ("收款", "作废"))},
    ]
    if params["linked"]:
        checks += [{"case": "the Express dialog opens and asks by POST", "seen": express,
                    "ok": bool(express.get("button")) and bool(express.get("dialog")) and bool(calls) and all(m == "POST" for m in calls)}]
    else:
        checks += [{"case": "no Express button when the company is not linked", "seen": express,
                    "ok": express.get("button") is False and not calls}]
    return checks


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
get = lambda name, fields: frappe.db.get_value("ERP Sales Document", name, fields, as_dict=True) or {{}}
print(json.dumps({{"draft": bool(frappe.db.exists("ERP Sales Document", {params['draft']!r})),
                  "collecting": get({params['collecting']!r}, ["paid_amount", "payment_method", "payment_status"]),
                  "viewing": get({params['viewing']!r}, ["ledger_state", "ledger_job"]),
                  "voiding": get({params['voiding']!r}, ["status", "void_reason"])}}, default=str))
""")
    after = stock_of(lab, manifest["site"], params["product"], params["warehouse"])
    col, view = got["collecting"], got["viewing"]
    return [
        {"case": "the deleted draft is gone", "saved": got["draft"], "ok": got["draft"] is False},
        {"case": "the collection dialog recorded 50 by transfer", "saved": col,
         "ok": float(col.get("paid_amount") or 0) == 50 and col.get("payment_method") == "transfer" and col.get("payment_status") == "partial"},
        {"case": "looking at Express does not change the ledger state", "saved": view,
         "ok": (view.get("ledger_state") or "") == params["state"] and (view.get("ledger_job") or "") == params["job"]},
        {"case": "cancel voided the sale with the typed reason", "saved": got["voiding"],
         "ok": got["voiding"].get("status") == "void" and got["voiding"].get("void_reason") == params["reason"]},
        {"case": "cancel put the sold unit back", "saved": {"before": params["before"], "after": after}, "ok": after == params["before"]},
    ]
