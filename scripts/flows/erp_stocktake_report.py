"""流程用例：ERP 盘点报表——差异报表看得到记的数与差异、能只看差异；盘点单能打印点数表与结果表、能导出（演示准备 10-30）。

准备：开一份盘点（库存岗、数据清单那家公司的第一个仓），两件商品由管理员当场新建（账面都是 0，不受同一套数据上别的流程留下的库存影响，
例如收银、销售在零库存时卖成的负数），按正式接口各记一笔：一件记 3（差异 +3）、一件记 0（差异 0）。页面上：
1. 库存岗打开「盘点差异」报表（筛这份盘点）：不能被拒（员工按菜单发 `ERP Menu stock-count`，10-30 发现报表只给了 ERP Stock），
   两件都在，实盘与差异对；再筛「只看差异」，只剩有差异的那件；
2. 回到盘点单点「打印」：点数表、结果表各打一次（打印页地址截下来取回内容），页里都有这两件商品；
3. 点「导出」：导出完成（直接下载或后台导出后下载）。
"""
from __future__ import annotations

import json
import uuid

from desk_list import IDLE, ACTION

TITLE = "ERP 盘点报表：差异报表、只看差异、打印点数表 / 结果表、导出（演示准备）"
REPORT = "ERP Stock Count Variance"


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    warehouse = next(w["name"] for w in manifest["warehouses"] if w.get("company", company) == company)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service
tag = {uuid.uuid4().hex[:6]!r}
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
picks = [service.create_product(product_name=f"สินค้าตรวจนับรายงาน {{tag}} {{n}}", code=f"RPT-{{tag}}-{{n}}")["name"] for n in (1, 2)]
frappe.db.commit()
frappe.set_user("stock@erp.regression")
companies.set_active_company({company!r})
doc = count.create_count(title="report " + tag, warehouse={warehouse!r})
frappe.db.commit()
rows = []
for product, qty in zip(picks, (3, 0)):
    item = count.ensure_item(count=doc["name"], product=product)
    book = float(item.get("book_qty") or 0)
    count.add_entry(count=doc["name"], item=item["name"], qty=qty, request_id="report-" + item["name"])
    frappe.db.commit()
    rows.append({{"product": product, "name": frappe.db.get_value("ERP Product", product, "product_name"), "book": book, "counted": qty,
                 "difference": qty - book}})
print(json.dumps({{"count": doc["name"], "rows": rows}}))
""")
    return {"count": out["count"], "rows": out["rows"], "cases": []}


def steps(params, camera_code=None):
    count = json.dumps(params["count"])
    names = json.dumps([r["name"] for r in params["rows"]], ensure_ascii=False)
    read = ("const data=()=> (frappe.query_report&&frappe.query_report.data||[]).filter(r=>r&&r.product_name)"
            ".map(r=>({name:r.product_name, counted:Number(r.counted_qty||0), difference:Number(r.difference||0)}));"
            "const denied=()=>[...document.querySelectorAll('.msgprint, .modal.show .modal-body')].map(e=>e.innerText.trim()).filter(t=>t.includes('权限'));")
    return [
        {"name": "差异报表", "js": IDLE + read +
         f"frappe.set_route('query-report', {json.dumps(REPORT)}, {{count: {count}}}); "
         "for(let k=0;k<80&&!(frappe.query_report&&frappe.query_report.report_name===" + json.dumps(REPORT) + "&&frappe.query_report.data);k++) await new Promise(r=>setTimeout(r,250)); "
         "await idle(); await new Promise(r=>setTimeout(r,800)); return {rows: data(), denied: denied()};"},
        {"name": "只看差异", "js": IDLE + read +
         "frappe.query_report.set_filter_value('only', 'differences'); await new Promise(r=>setTimeout(r,500)); await idle(); await new Promise(r=>setTimeout(r,800)); "
         "return {rows: data()};"},
        {"name": "打印点数表与结果表", "js": IDLE + ACTION +
         # 打印页是新窗口打开的：截下地址，自己取回内容核对
         "window.__opened=[]; window.open=(u)=>{window.__opened.push(u); return null;}; "
         f"frappe.set_route('pearnly-stock-count', {count}); await idle(); await new Promise(r=>setTimeout(r,1200)); "
         "const out=[]; for (const kind of ['', 'check']) { const how=await action('打印'); "
         "for(let k=0;k<40&&!(window.cur_dialog&&cur_dialog.$wrapper.is(':visible'));k++) await new Promise(r=>setTimeout(r,250)); "
         "const d=cur_dialog; if(!d) {out.push({error:'no print dialog', how}); continue;} "
         "const sel=Object.values(d.fields_dict).find(f=>f.df.fieldtype==='Select'); if(sel) await d.set_value(sel.df.fieldname, kind); "
         "d.get_primary_btn().trigger('click'); await new Promise(r=>setTimeout(r,800)); d.hide(); "
         "const url=window.__opened[window.__opened.length-1]||''; let html=''; let status=0; "
         "if(url){const r=await fetch(url,{credentials:'same-origin'}); status=r.status; html=await r.text();} "
         f"out.push({{kind, how, url: url.slice(0,160), status, has: {names}.map(n=>html.includes(n))}}); }} return {{prints: out}};"},
        {"name": "导出", "js": IDLE +
         "const saved=[]; const o=URL.createObjectURL; URL.createObjectURL=(b)=>{saved.push(b&&b.size||0); return o(b);}; "
         f"const r=await frappe.pearnly.export_file('pearnly_erp_portal.inventory.api.api_stock_count_export', {{name: {count}}}); "
         "return {status: r&&r.status, bytes: saved};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    want = {r["name"]: r for r in params["rows"]}
    report = {r["name"]: r for r in (by.get("差异报表") or {}).get("rows") or []}
    only = (by.get("只看差异") or {}).get("rows") or []
    diff_names = {r["name"] for r in params["rows"] if r["difference"]}
    prints = (by.get("打印点数表与结果表") or {}).get("prints") or []
    exported = by.get("导出") or {}
    return [
        {"case": "库存岗能打开差异报表（不被权限拒）", "seen": (by.get("差异报表") or {}).get("denied"), "ok": not (by.get("差异报表") or {}).get("denied")},
        {"case": "差异报表：两件的实盘与差异对", "seen": report,
         "ok": all(n in report and report[n]["counted"] == w["counted"] and report[n]["difference"] == w["difference"] for n, w in want.items())},
        {"case": "只看差异：只剩有差异的", "seen": only, "ok": bool(only) and {r["name"] for r in only} == diff_names},
        {"case": "打印点数表与结果表：打印页打开、两件都在", "seen": prints,
         "ok": len(prints) == 2 and all(p.get("status") == 200 and p.get("has") and all(p["has"]) for p in prints)},
        {"case": "导出完成", "seen": exported, "ok": exported.get("status") == "done" and any(exported.get("bytes") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
print(json.dumps(frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}, "voided": 0}}, fields=["product", "qty"])))
""")
    return [{"case": "库里两笔记录照记", "saved": rows, "ok": sorted(float(r["qty"]) for r in rows) == sorted(r["counted"] for r in params["rows"])}]
