"""流程用例：ERP 盘点扫到系统没有的码 → 当场新建商品 → 接着记数 → 落库（盘点菜单做完整 S3，用户 10-29 批复）。

每套数据：开一份新盘点，录入框输一个系统里没有的码（手机宽度另用假摄像头扫一遍），要出「新建物料」；填品名、单位提交后
录入框认出这件（品名、编码都有），上一件「查不到」的提示清掉，填数量保存。库里：新商品来由是「盘点新建 · 这份盘点」、
扫到的码记在条码上、编码系统发；这份盘点有这一条记录、数量对，行来源「新发现」。

岳父 11-01 手机上扫到没有的码：原生提示框挡着、「新建物料」在相机画面下面看不到。所以查不到之后要断言：没有提示框、
相机已停（画面收起）、按钮在可视区域里且没被盖住，点的是按钮所在位置上真正的那个元素（不是直接调按钮的函数）；
记完这一件，相机那一遍要自己再开相机。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 盘点：扫到系统没有的码当场新建商品并记数"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
# 截住页面里打开的对话框（框架没有全局清单可取）
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    warehouse = next(w["name"] for w in manifest["warehouses"] if w.get("company", company) == company)
    first12 = "8859" + str(uuid.uuid4().int)[:8]
    # 系统里没有的码：合法的 EAN-13（带校验位），假摄像头画面才读得出
    code = first12 + str((10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12)) % 10) % 10)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
from pearnly_erp_portal.masters import company as companies
frappe.set_user("stock@erp.regression")
companies.set_active_company({company!r})
doc = count.create_count(title="flow new product " + {uuid.uuid4().hex[:6]!r}, warehouse={warehouse!r})
frappe.db.commit()
print(json.dumps({{"count": doc["name"]}}))
""")
    return {"count": out["count"], "company": company, "warehouse": warehouse, "code": code,
            "name": "flow new " + code[-4:], "cases": [{"key": "by_barcode", "scan": code}]}


# 「新建物料」在不在眼前：在可视区域里、按钮中心点上就是它（没被提示框或相机画面盖住）
SEEN = ("const seen=(el)=>{if(!el||!el.offsetParent) return {shown:false};const r=el.getBoundingClientRect();"
        "const cx=r.left+r.width/2, cy=r.top+r.height/2, top=document.elementFromPoint(cx,cy);"
        "return {shown:true, in_view: r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth,"
        " on_top: !!top&&(top===el||el.contains(top)), cx, cy};};")


def steps(params, camera_code=None):
    code, name = json.dumps(params["code"]), json.dumps(params["name"])
    if camera_code:
        find = "d.fields_dict.camera.$input.trigger('click');"
    else:
        find = (f"d.set_value('code', {code}); await new Promise(r=>setTimeout(r,300)); d.fields_dict.find.$input.trigger('click');")
    return [
        {"name": "开始盘点", "js": IDLE + CAPTURE +
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no start button'}; b.click();"
         "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); return b.innerText.trim();"},
        {"name": "查找没有的码", "js": IDLE + SEEN + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'};"
         + find +
         "const btn=d.fields_dict.new_product;"
         "for(let k=0;k<80&&!btn.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250));"
         "await new Promise(r=>setTimeout(r,500)); await idle();"
         "return {new_button: seen(btn.$input[0]), prompt: $('.msgprint-dialog:visible, .modal.show .msgprint').length,"
         " camera_off: !d.fields_dict.video.$wrapper.is(':visible'),"
         " message: (d.fields_dict.message.$wrapper.text()||'').trim().slice(0,200), entry: d.fields_dict.qty.$wrapper.is(':visible')};"},
        {"name": "新建物料", "js": IDLE + SEEN + "const d=cur_dialog&&cur_dialog.fields_dict.code?cur_dialog:window.__dlgs.find(x=>x.fields_dict.code);"
         "const s=seen(d.fields_dict.new_product.$input[0]); if(!s.shown) return {error:'no new product button'};"
         "const n=window.__dlgs.length; document.elementFromPoint(s.cx,s.cy).click();"
         "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250));"
         "const f=window.__dlgs[window.__dlgs.length-1]; if(!f||!f.fields_dict.product_name) return {error:'no new product form'};"
         f"await f.set_value('product_name', {name}); await f.set_value('unit', 'ชิ้น'); f.get_primary_btn().trigger('click');"
         "for(let k=0;k<60&&!d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {form_code: f.get_value('code')||'', entry: d.fields_dict.qty.$wrapper.is(':visible'), product_name: d.get_value('product_name')||'',"
         " product_code: d.get_value('product_code')||'', message: (d.fields_dict.message.$wrapper.text()||'').trim()};"},
        {"name": "保存", "js": IDLE + "const d=window.__dlgs.find(x=>x.fields_dict.code); if(!d||!d.fields_dict.qty.$wrapper.is(':visible')) return {error:'no entry'};"
         "d.set_value('qty', 3); await new Promise(r=>setTimeout(r,300)); d.get_primary_btn().trigger('click');"
         "await new Promise(r=>setTimeout(r,300)); await idle();"
         "for(let k=0;k<20&&!d.fields_dict.video.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250));"
         "return {saved: true, camera_on: d.fields_dict.video.$wrapper.is(':visible')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    found, made, saved = by.get("查找没有的码", {}), by.get("新建物料", {}), by.get("保存", {})
    button = found.get("new_button") or {}
    return [
        {"case": "unknown code offers new product", "seen": found,
         "ok": bool(button.get("shown")) and not found.get("entry") and not found.get("prompt") and bool(found.get("message"))},
        {"case": "new product button in view and not covered", "seen": button,
         "ok": bool(button.get("in_view")) and bool(button.get("on_top"))},
        {"case": "camera stopped while offering new product", "seen": found, "ok": not camera_code or bool(found.get("camera_off"))},
        {"case": "new product uses the scanned code", "seen": made, "ok": made.get("form_code") == params["code"]},
        {"case": "camera back on for the next item", "seen": saved, "ok": not camera_code or saved.get("camera_on") is True},
        {"case": "created product is recognised", "seen": made,
         "ok": made.get("entry") is True and made.get("product_name") == params["name"] and bool(made.get("product_code"))
         and not made.get("message")},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
p = frappe.db.get_value("ERP Product", {{"barcode": {params['code']!r}}}, ["name", "product_name", "code", "origin", "origin_doctype", "origin_name"], as_dict=True) or {{}}
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}, "voided": 0}}, fields=["product", "qty"], limit_page_length=20)
item = frappe.db.get_value("ERP Stock Count Item", {{"count": {params['count']!r}, "product": p.get("name")}}, "row_origin") if p else None
print(json.dumps({{"product": p, "entries": [dict(r, qty=float(r.qty)) for r in rows], "row_origin": item}}, default=str))
""")
    p = got["product"]
    return [
        {"case": "product origin is this count", "saved": p,
         "ok": p.get("product_name") == params["name"] and p.get("origin") == "stocktake" and p.get("origin_name") == params["count"]
         and str(p.get("code") or "").startswith("P-")},
        {"case": "entry saved on the new row", "saved": got,
         "ok": got["entries"] == [{"product": p.get("name"), "qty": 3.0}] and got["row_origin"] == "new"},
    ]
