"""流程用例：ERP 收银台页头四颗按钮逐个实点 —— 开班、新单、最近订单、交班（清产品地图「没测试」存量基线，11-07）。

`erp_pos_shift.py` 是调接口开班 / 卖 / 退 / 交班，不算点按钮；这一条只走页头：
收银岗打开收银台（先把本人开着的班收掉，页面是「尚未开班」）：
- 页头主按钮「开班」→ 原生开班弹窗，现金那行备用金填 500 → 开班 → 页面出商品区，库里这一班开着、备用金 500。
- 搜索框打条码，这件进购物车 → 页头主按钮「新单」→ 购物车清空（官方 `load_new_invoice_on_pos`）。
- 用收银接口在这一班卖 1 件现金（结账那几步不是这四颗按钮的入口，同 `erp_cashier_camera_scan.py`），再照页面成交后的做法刷新当班汇总。
- 页头次按钮「最近订单」→ 官方「最近订单」面板打开，列表里有刚才那张小票。
- 「…」菜单「交班」→ 原生交班弹窗（实点默认 = 应有）→ 交班 → 库里这一班已交、实点现金 = 500 + 这张小票的钱、差额 0。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, codes, company, js_value  # noqa: E402

TITLE = "ERP 收银台页头：开班、新单、最近订单、交班"
CASHIER = "cashier@erp.regression"
API = "'pearnly_erp_portal.cashier.api."
SEARCH = "const search=()=>all('.point-of-sale-app .search-field, .item-selector-wrapper .search-field')[0];"
CART = "const cart=()=>all('.cart-items-section .cart-item-wrapper, .cart-container .cart-item-wrapper').map(e=>e.textContent.replace(/\\s+/g,' ').trim());"


def prepare(lab, manifest):
    co = company(manifest)
    item = codes(manifest)["by_barcode"]
    # 这一格开着收银；格结束时 run_flow 还原原值（`Lab.set_fields`，成功、失败、超时都还原）
    lab.set_fields(manifest["site"], "ERP Company", co, {"pos_enabled": 1})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.cashier import shifts, terminals
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({co!r})
till = terminals.ensure_default(company={co!r})
if not frappe.db.get_value("ERP POS Terminal", till, "warehouse"):  # 接 Express 的公司默认那台不带仓：同 erp_pos_shift 挂第一个仓
    first = frappe.get_all("ERP Warehouse", filters={{"company": {co!r}, "is_active": 1}}, pluck="name", order_by="express_code asc, creation asc")[0]
    terminals.update_terminal(till, warehouse=first)
wh = frappe.db.get_value("ERP POS Terminal", till, "warehouse")
documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=wh, reason="flow pos buttons", submit=True,
                       lines=[{{"product": {item['product']!r}, "qty": 2, "unit_cost": 10}}])
frappe.db.commit()
# 本人开着的班按正式口径交掉（实点 = 应有），页面才是「尚未开班」
frappe.set_user({CASHIER!r})
for shift in frappe.get_all("ERP Shift", filters={{"status": "open", "cashier": {CASHIER!r}}}, pluck="name"):
    shifts.close_shift(shift, counted={{"cash": shifts.summary(shift, own_only=False)["expected_cash"]}})
frappe.db.commit()
p = frappe.db.get_value("ERP Product", {item['product']!r}, ["product_name", "unit_price"], as_dict=True)
print(json.dumps({{"terminal": till, "name": p.product_name, "price": float(p.unit_price or 100), "since": str(frappe.utils.now_datetime())}}))
""")
    return {**out, "company": co, "product": item["product"], "scan": item["scan"], "uuid": uuid.uuid4().hex, "cases": []}


def steps(params, camera_code=None):
    product, price, uid, scan, name = (js_value(params[k]) for k in ("product", "price", "uuid", "scan", "name"))
    head = "const t=await frappe.pearnly.texts('trade.');"
    return [
        {"name": "开班", "js": JS + ACTION + SEARCH + head + """
frappe.set_route('pearnly-cashier');
const empty=await until(()=>all('.pearnly-pos-empty-btn')[0], 30000); if(!empty) return {error:'cashier is not in the no-shift state'};
await idle();
const via=await action(t('trade.openShift')); if(!via) return {error:'no Open Shift head button'};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000); if(!d) return {error:'no open shift dialog'};
await idle();
const grid=d.fields_dict.balances.grid; const row=(grid.data||[]).find(r=>r.method==='cash'); if(!row) return {error:'no cash row'};
row.opening_amount=500; grid.refresh(); await sleep(300);
d.get_primary_btn().click();
const ready=await until(search, 30000); await idle();
return {via, ready: !!ready};"""},
        {"name": "新单", "js": JS + ACTION + SEARCH + CART + head + f"""
const field=await until(search, 15000); if(!field) return {{error:'no item search field'}};
const input=field.querySelector('input'); input.focus(); input.value={scan}; input.dispatchEvent(new Event('input',{{bubbles:true}}));
const added=await until(()=>cart().find(x=>x.includes({name})), 25000); await idle(); if(!added) return {{error:'item did not reach the cart', cart: cart()}};
const via=await action(t('trade.newInvoice')); if(!via) return {{error:'no New Invoice head button'}};
const cleared=await until(()=>cart().length===0, 15000); await idle();
return {{via, before: 1, after: cart().length, cleared: !!cleared}};"""},
        {"name": "卖一张", "js": JS + f"""
const r=await call({API}api_create_sale', {{lines:[{{product:{product}, qty:1, unit_price:{price}}}],
  payments:[{{method:'cash', amount:{price}}}], client_uuid:{uid}}});
// 页面上成交后收银台自己会刷新当班汇总（`refresh_shift_summary`，交班弹窗的「应有」取它）；接口卖的这张照样刷一次
if(window.cur_pos&&cur_pos.refresh_shift_summary) await cur_pos.refresh_shift_summary(); await idle();
return {{sale: (r||{{}}).name||'', expected: (cur_pos&&cur_pos.pearnly_shift||{{}}).expected_cash}};"""},
        {"name": "最近订单", "js": JS + ACTION + head + f"""
const sale=await call({API}api_current_sales', {{limit: 5}}, 'GET'); const mine=(sale||[]).find(x=>x.client_uuid==={uid})||(sale||[])[0]||{{}};
const via=await action(t('trade.recentOrders')); if(!via) return {{error:'no Recent Orders head button'}};
const shown=await until(()=>all('.past-order-list, .past-order-list-container, .invoices-container')[0], 15000); await idle();
const rows=()=>[...document.querySelectorAll('.invoice-wrapper')].map(e=>e.getAttribute('data-invoice-name'));
const found=await until(()=>rows().length&&rows(), 15000);
return {{via, shown: !!shown, rows: rows(), latest: mine.name||''}};"""},
        {"name": "交班", "js": JS + ACTION + head + """
const via=await action(t('trade.closeShift')); if(!via) return {error:'no Close Shift menu item'};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000); if(!d) return {error:'no close shift dialog'};
await idle(); await sleep(400); d.get_primary_btn().click();
const back=await until(()=>all('.pearnly-pos-empty-btn')[0], 30000); await idle();
return {via, no_shift_again: !!back, msg: msg()};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opened, new, sold, recent, closed = (by.get(k, {}) for k in ("开班", "新单", "卖一张", "最近订单", "交班"))
    return [
        {"case": "Open Shift head button opens the shift and the item area", "seen": opened, "ok": bool(opened.get("via")) and opened.get("ready") is True},
        {"case": "New Invoice head button empties the cart", "seen": new, "ok": bool(new.get("via")) and new.get("cleared") is True},
        {"case": "a sale was made in this shift", "seen": sold, "ok": bool(sold.get("sale"))},
        {"case": "Recent Orders head button lists this shift's receipt", "seen": recent,
         "ok": bool(recent.get("via")) and sold.get("sale") in (recent.get("rows") or [])},
        {"case": "Close Shift menu item closes it and the page is back to no shift", "seen": closed,
         "ok": bool(closed.get("via")) and closed.get("no_shift_again") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Shift", filters={{"cashier": {CASHIER!r}, "terminal": {params['terminal']!r}, "creation": [">=", {params['since']!r}]}},
                      fields=["name", "status", "opening_float", "counted_cash"], order_by="creation asc")
sale = frappe.db.get_value("ERP Sale", {{"client_uuid": {params['uuid']!r}}}, ["name", "shift", "status"], as_dict=True) or {{}}
print(json.dumps({{"shifts": rows, "sale": sale}}, default=str))
""")
    shifts, sale = got["shifts"], got["sale"]
    one = shifts[0] if len(shifts) == 1 else {}
    return [
        {"case": "one shift opened from the page with a 500 float", "saved": shifts, "ok": bool(one) and float(one.get("opening_float") or 0) == 500},
        {"case": "the sale belongs to that shift", "saved": sale, "ok": bool(one) and sale.get("shift") == one.get("name") and sale.get("status") == "completed"},
        {"case": "the shift is closed with counted cash = float + this sale", "saved": one,
         "ok": one.get("status") == "closed" and float(one.get("counted_cash") or 0) == 500 + params["price"]},
    ]
