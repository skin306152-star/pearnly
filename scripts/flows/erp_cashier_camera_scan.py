"""流程用例：ERP 收银台 —— 商品区搜索框按码进购物车（手机宽度另用假摄像头点搜索框的扫码按钮）→ 成交 → 账面少 1。

产品地图 erp.scan.cashier.camera（11-04 相机入口逐个成行后补的测试）。
收银台是 ERPNext 原生 POS：搜索框旁的扫码按钮是框架自己的那颗（`setup_barcode_field`），我们只把动作换成共用读码器
（`pearnly-pos-adapter.js` 的 `make_search_bar`）；读到的码写进搜索框、发 `input`，唯一命中由官方「auto add」进购物车。
收银岗：页面里先开班 → 打开收银台 → 手输那遍把码打进搜索框，相机那遍点扫码按钮由假摄像头读条码 → 购物车里有这件；
成交用收银接口卖 1 件（页面结账那几步不在这个入口里），库里：小票已成交、主仓这件少 1（按小票自己的库存流水核）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, js_value  # noqa: E402

TITLE = "ERP 收银台：搜索框按码 / 相机进购物车、成交"


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    item = codes(manifest)["by_barcode"]
    # 这一格开着收银；格结束时 run_flow 还原原值（`Lab.set_fields`，成功、失败、超时都还原）
    lab.set_fields(manifest["site"], "ERP Company", company, {"pos_enabled": 1})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents, warehouses
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
for shift in frappe.get_all("ERP Shift", filters={{"status": "open", "cashier": "cashier@erp.regression"}}, pluck="name"):
    frappe.db.set_value("ERP Shift", shift, "status", "closed", update_modified=False)
main = warehouses.main_of({company!r})
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=main, submit=True,
                       lines=[{{"product": {item['product']!r}, "qty": 3, "unit_cost": 10}}])
p = frappe.db.get_value("ERP Product", {item['product']!r}, ["product_name", "unit_price"], as_dict=True)
frappe.db.commit()
print(json.dumps({{"main": main, "name": p.product_name, "price": float(p.unit_price or 100)}}))
""")
    return {**out, "company": company, "product": item["product"], "scan": item["scan"], "uuid": uuid.uuid4().hex,
            "cases": [{"key": "by_barcode", "scan": item["scan"]}]}


def steps(params, camera_code=None):
    api = "'pearnly_erp_portal.cashier.api."
    add = ("const btn=field.querySelector('.link-btn a'); if(!btn) return {error:'no scan button by the search field'}; btn.click();"
           if camera_code else
           f"input.focus(); input.value={js_value(params['scan'])}; input.dispatchEvent(new Event('input',{{bubbles:true}}));")
    product, price, uid = (json.dumps(params[k]) for k in ("product", "price", "uuid"))
    return [
        {"name": "开班进收银台", "js": JS + """
frappe.set_route('pearnly-cashier');
const search=()=>all('.point-of-sale-app .search-field, .item-selector-wrapper .search-field')[0];
// 照人的做法：没开班时页面给「开班」按钮 → 开班框 → 确认（备用金留默认）
const open=await until(()=>search()||all('.pearnly-pos-empty-btn')[0], 30000);
if(open&&!search()){ open.click();
  const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000);
  if(d){ await idle(); d.get_primary_btn().click(); } }
const field=await until(search, 30000);
if(!field) return {error:'no item search field: '+((document.querySelector('.page-container[data-page-route="pearnly-cashier"]')||document.body).innerText.slice(0,200))};
await idle();
return {ready: true};"""},
        {"name": "按码进车", "js": JS + f"""
const field=all('.point-of-sale-app .search-field, .item-selector-wrapper .search-field')[0]; if(!field) return {{error:'search field lost'}};
const input=field.querySelector('input'); if(!input) return {{error:'no search input'}};
{add}
const names=()=>all('.cart-items-section .cart-item-wrapper, .cart-container .cart-item-wrapper').map(e=>e.textContent.replace(/\\s+/g,' ').trim());
const added=await until(()=>names().find(t=>t.includes({js_value(params['name'])})), 25000); await idle();
return {{cart: names(), added: !!added, typed: input.value}};"""},
        {"name": "成交", "js": JS + f"""
const r=await call({api}api_create_sale', {{lines:[{{product:{product}, qty:1, unit_price:{price}}}],
  payments:[{{method:'cash', amount:{price}}}], client_uuid:{uid}}});
return {{sale: (r||{{}}).name||'', status: (r||{{}}).status||''}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    cart, sale = by.get("按码进车", {}), by.get("成交", {})
    return [{"case": "按码进购物车" + ("（相机扫码）" if camera_code else ""), "seen": cart, "ok": bool(cart.get("added"))},
            {"case": "成交", "seen": sale, "ok": bool(sale.get("sale"))}]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
sale = frappe.db.get_value("ERP Sale", {{"client_uuid": {params['uuid']!r}}}, ["name", "status", "warehouse"], as_dict=True) or {{}}
moved = sum(r.qty_base or 0 for r in frappe.get_all("ERP Stock Movement", filters={{"ref_id": sale.get("name") or "-",
            "product": {params['product']!r}}}, fields=["qty_base"]))
for shift in frappe.get_all("ERP Shift", filters={{"status": "open", "cashier": "cashier@erp.regression"}}, pluck="name"):
    frappe.db.set_value("ERP Shift", shift, "status", "closed", update_modified=False)  # 收尾：别留开着的班
frappe.db.commit()
print(json.dumps({{"sale": sale, "moved": float(moved)}}, default=str))
""")
    return [{"case": "小票成交、从主仓出 1 件", "saved": got,
             "ok": bool(got["sale"].get("name")) and got["sale"].get("warehouse") == params["main"] and got["moved"] == -1.0}]
