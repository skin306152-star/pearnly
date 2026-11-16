"""流程用例：ERP 盘点列表「新建盘点」与旧盘点的「Express」（清存量基线 11-08）。

库管打开盘点：
- 页头「新建盘点」→ 原生弹窗填名称、选仓（盘点在库）→ 弹窗「新建盘点」→ 打开这份盘点；库里多一份这个名称、这个仓的盘点，快照行就是这个仓的在库物料。
- 以前的盘点（整份轧差写进 Express 的盘点调整，`ledger_state` 有值才出这颗）：prepare 按正式接口建一份，再在实验室把它标成
  「已结束、已写入 Express、单号 TK-<标记>」模拟这类旧盘点；打开它点页头「Express」→ 原生弹窗显示的单号就是库里那个，取状态走 POST；库里写账状态不变（只看）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, codes, company, js_value, warehouses  # noqa: E402

TITLE = "ERP 盘点：列表新建盘点、旧盘点的 Express 弹窗"
USER = "stock@erp.regression"
API = "pearnly_erp_portal.express.api.document_express"


def prepare(lab, manifest):
    wh = warehouses(manifest)[0]["name"]
    tag = uuid.uuid4().hex[:6]
    product = codes(manifest)["by_code"]["product"]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import api, documents
from pearnly_erp_portal.masters import company as companies
# 快照要有在库物料：仓里一件都没有时先按正式入库记一笔（新开户数据本来没货，以前靠前面别的流程留下的入库；11-16 GitHub 分份后暴露，
# 每格收尾后也一样），收尾时连流水一起删掉
if not frappe.db.count("ERP Stock Balance", {{"warehouse": {wh!r}, "qty_on_hand": ["!=", 0]}}):
    frappe.set_user("admin@erp.regression")
    companies.set_active_company({company(manifest)!r})
    shelf = (frappe.get_all("ERP Warehouse Location", filters={{"parent": {wh!r}, "parenttype": "ERP Warehouse"}}, pluck="location_name",
                            order_by="idx asc", limit=1) or [""])[0]
    documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse={wh!r}, reason="flow seed", submit=True,
                           lines=[{{"product": {product!r}, "qty": 5, "unit_cost": 10, "location": shelf}}])
    frappe.db.commit()
frappe.set_user({USER!r})
made = api.api_create_stock_count(title="flow legacy {tag}", warehouse={wh!r})
frappe.db.commit()
# 只在实验室：把它标成以前那种已结束、整份写进 Express 的盘点（现在差异改走物料调整单，新盘点不再有写账状态）
frappe.db.set_value("ERP Stock Count", made["name"], {{"status": "Closed", "ledger_state": "written", "ledger_docnum": "TK-{tag}"}},
                    update_modified=False)
frappe.db.commit()
stocked = frappe.db.count("ERP Stock Balance", {{"warehouse": {wh!r}, "qty_on_hand": ["!=", 0]}})
print(json.dumps({{"legacy": made["name"], "stocked": stocked}}))
""")
    return {**out, "warehouse": wh, "title": f"flow count {tag}", "docnum": f"TK-{tag}", "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "新建盘点", "js": JS + ACTION + f"""
const t=await frappe.pearnly.texts('stock.');
await until(()=>pageOf('pearnly-stock-count')&&frappe.get_route().length===1&&document.querySelector('.result, .no-result'), 25000); await idle();
const via=await action(t('stock.count.new')); if(!via) return {{error:'no new count button'}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog.fields_dict.title&&cur_dialog, 15000);
if(!d) return {{error:'no new count dialog'}}; await idle(); await sleep(500);
await d.set_value('title', {js_value(params['title'])}); await d.set_value('warehouse', {js_value(params['warehouse'])}); await sleep(600); await idle();
d.get_primary_btn().click();
const opened=await until(()=>!d.$wrapper.is(':visible')&&frappe.get_route()[1], 25000); await idle();
return {{via, opened: !!opened, route: frappe.get_route().join('/'), msg: msg()}};"""},
        {"name": "Express", "js": JS + ACTION + f"""
window.__posts=[]; if(!window.__phook){{window.__phook=1; const _a=$.ajax; $.ajax=function(o){{if(o&&String(o.url||'').includes({js_value(API)})) window.__posts.push(String(o.type||o.method||'GET').toUpperCase()); return _a.apply(this, arguments);}};}}
frappe.set_route('pearnly-stock-count', {js_value(params['legacy'])});
await until(()=>frappe.get_route()[1]==={js_value(params['legacy'])}&&all('.page-head button').length, 20000); await idle(); await sleep(500);
const via=await action('Express'); if(!via) return {{error:'no Express button'}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000); if(!d) return {{error:'no Express dialog'}};
await idle(); const docnum=d.fields_dict.docnum?d.get_value('docnum'):''; d.hide();
return {{via, docnum, posts: window.__posts}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    new, express = by.get("新建盘点", {}), by.get("Express", {})
    return [
        {"case": "the New count dialog creates the count and opens it", "seen": new,
         "ok": bool(new.get("via")) and new.get("opened") is True and not new.get("msg")},
        {"case": "the Express dialog shows the stored Express number and asks by POST", "seen": express,
         "ok": bool(express.get("via")) and express.get("docnum") == params["docnum"] and express.get("posts") == ["POST"]},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Count", filters={{"title": {params['title']!r}}}, fields=["name", "warehouse", "status"])
items = frappe.db.count("ERP Stock Count Item", {{"count": rows[0]["name"]}}) if rows else 0
legacy = frappe.db.get_value("ERP Stock Count", {params['legacy']!r}, ["ledger_state", "ledger_docnum"], as_dict=True)
print(json.dumps({{"rows": rows, "items": items, "legacy": legacy}}, default=str))
""")
    rows = got["rows"]
    return [
        {"case": "one count with this name in this warehouse, its snapshot holds the stocked products", "saved": got,
         "ok": len(rows) == 1 and rows[0]["warehouse"] == params["warehouse"] and got["items"] >= 1},
        {"case": "opening the Express dialog left the legacy count's ledger state alone", "saved": got["legacy"],
         "ok": got["legacy"] == {"ledger_state": "written", "ledger_docnum": params["docnum"]}},
    ]
