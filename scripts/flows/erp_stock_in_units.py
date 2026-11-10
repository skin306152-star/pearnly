"""流程用例：ERP 入库按台登记车辆（盘点菜单做完整 S0 按台按公司类型；10-29 按台登记扫码 / 留空缺陷）。

公司设成「汽车」类型。入库页新建一张入库单：单头选仓，明细按码填按台商品（扫码枪回车那条路，认出后带出内部编码一格），数量 1，
提交 → 弹「登记车辆」：先留空保存要提示台数不符、不提交；填车架号再保存 → 入库成功。库里：这台车登记在这个仓、在库，
入库单已提交。跑完公司类型还原成普通。老数据没有按台商品，先建一件本地的按台商品（没有库存，直接开按台）。
手机宽度另用假摄像头跑一遍：「登记车辆」弹窗里不手填，点弹窗的「扫码」由相机读出号码填进第一格空行（产品地图 erp.scan.serials.dialog，11-05）。
号码用合法的 EAN-13 数字码（假摄像头画面只画得出 EAN-13；号码格不限字符）。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 入库：汽车公司按台登记车辆"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}"
           "window.__msgs=window.__msgs||[];if(!frappe.__msgcap){const m=frappe.msgprint;frappe.msgprint=function(x){window.__msgs.push(typeof x==='string'?x:(x&&x.message)||'');return m.apply(this,arguments)};frappe.__msgcap=1;}")


def _ean13() -> str:
    first12 = "8856" + str(uuid.uuid4().int)[:8]
    return first12 + str((10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12)) % 10) % 10)


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    products = manifest.get("products")
    unit = products.get("by_unit", {}) if isinstance(products, dict) else {}  # 老数据的清单是列表，没有按台商品
    # 这一格把公司临时改成按台（汽车）类型；格结束时 run_flow 还原原值（`Lab.set_fields`，成功、失败、超时都还原）
    lab.set_fields(manifest["site"], "ERP Company", company, {"company_type": "vehicle"})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import serials
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service as products
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
product = {unit.get('product')!r}
code = {unit.get('scan')!r}
if not product:
    code = "FLOW-CAR-01"
    product = frappe.db.get_value("ERP Product", {{"code": code}}) or products.create_product(product_name="รถทดสอบ flow", code=code, unit="คัน",
                                                                                             unit_price=500000, default_cost=400000)["name"]
    if not frappe.db.get_value("ERP Product", product, "track_serial"):
        serials.turn_on(product, kind="vehicle")
wh = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
frappe.db.commit()
print(json.dumps({{"product": product, "code": code, "warehouse": wh}}))
""")
    vin = _ean13()
    return {**out, "company": company, "vin": vin, "cases": [{"key": "by_barcode", "scan": vin}]}


def steps(params, camera_code=None):
    code, wh, vin = json.dumps(params["code"]), json.dumps(params["warehouse"]), json.dumps(params["vin"])
    return [
        {"name": "新建入库单", "js": IDLE + CAPTURE +
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no new button'}; const n=window.__dlgs.length; b.click();"
         "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const d=window.__dlgs[window.__dlgs.length-1]; if(!d||!d.fields_dict.warehouse) return {error:'no stock entry dialog'};"
         f"await d.set_value('warehouse', {wh}); await idle();"
         "const i=d.$wrapper.find('.pearnly-line-editor [data-fieldname=\"product\"] input')[0]; if(!i) return {error:'no line product box'};"
         f"i.focus(); i.value={code}; i.dispatchEvent(new Event('input',{{bubbles:true}}));"
         "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
         "for(let k=0;k<40&&!d.$wrapper.find('.pearnly-line-editor [data-fieldname=\"description\"] input, .pearnly-line-editor [data-fieldname=\"description\"] textarea').val();k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); return {line: d.$wrapper.find('.pearnly-line-editor [data-fieldname=\"description\"] input, .pearnly-line-editor [data-fieldname=\"description\"] textarea').val()||''};"},
        {"name": "提交弹出登记车辆", "js": IDLE +
         "const d=window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.warehouse&&x.fields_dict.lines); d.get_primary_btn().trigger('click');"
         "const unitDialog=()=>window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.units&&x.$wrapper.is(':visible'));"
         "for(let k=0;k<80&&!unitDialog();k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const u=unitDialog(); if(!u) return {error:'no unit dialog', msgs: window.__msgs};"
         "const before=window.__msgs.length; u.get_primary_btn().trigger('click'); await new Promise(r=>setTimeout(r,500));"
         "return {rows: (u.fields_dict.units.grid.get_data()||[]).length, blank_refused: window.__msgs.length>before && u.$wrapper.is(':visible')};"},
        {"name": "填车架号保存", "js": IDLE +
         "const u=window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.units&&x.$wrapper.is(':visible')); if(!u) return {error:'unit dialog closed'};"
         "document.querySelectorAll('.msgprint-dialog .btn-modal-close').forEach(b=>b.click());"
         + (
         # 相机那一遍：点弹窗的「扫码」（secondary），由假摄像头读出号码填进第一格空行
         "const scan=u.$wrapper.find('.modal-footer .btn-secondary:visible')[0]; if(!scan) return {error:'no scan button on the unit dialog'}; scan.click();"
         f"for(let k=0;k<100&&((u.fields_dict.units.grid.get_data()||[])[0]||{{}}).serial_no!=={vin};k++) await new Promise(r=>setTimeout(r,250));"
         "if(window.cur_dialog&&cur_dialog!==u&&cur_dialog.$wrapper.is(':visible')) cur_dialog.hide();"
         if camera_code else
         f"const row=(u.fields_dict.units.grid.get_data()||[])[0]; row.serial_no={vin}; u.fields_dict.units.grid.refresh();") +
         "await new Promise(r=>setTimeout(r,300)); const filled=((u.fields_dict.units.grid.get_data()||[])[0]||{}).serial_no||'';"
         " u.get_primary_btn().trigger('click');"
         "const d=window.__dlgs.find(x=>x.fields_dict&&x.fields_dict.warehouse&&x.fields_dict.lines);"
         "for(let k=0;k<60&&d.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {submitted: !d.$wrapper.is(':visible'), filled};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    return [
        {"case": "product filled by code", "seen": by.get("新建入库单"), "ok": bool((by.get("新建入库单") or {}).get("line"))},
        {"case": "unit dialog refuses a blank row", "seen": by.get("提交弹出登记车辆"),
         "ok": (by.get("提交弹出登记车辆") or {}).get("rows") == 1 and (by.get("提交弹出登记车辆") or {}).get("blank_refused") is True},
        {"case": "submitted after the number is filled" + (" (scanned by camera)" if camera_code else ""), "seen": by.get("填车架号保存"),
         "ok": (by.get("填车架号保存") or {}).get("submitted") is True and (by.get("填车架号保存") or {}).get("filled") == params["vin"]},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
unit = frappe.db.get_value("ERP Serial No", {{"serial_no": {params['vin']!r}}}, ["product", "warehouse", "status"], as_dict=True) or {{}}
print(json.dumps(unit))
""")
    return [{"case": "unit registered in the warehouse", "saved": got,
             "ok": got.get("product") == params["product"] and got.get("warehouse") == params["warehouse"] and got.get("status") == "in_stock"}]
