"""流程用例：ERP 新建物料（盘点菜单做完整 S1 批次 / 有效期删除、S0 按台按公司类型、S3 来由）。

两套数据：物料页「新建」弹窗里没有批次 / 有效期、没有按台两格（普通类型的公司）；填品名、单位、条码提交，库里来由「手建」、
批次 / 有效期标志都是 0；按条码找商品（扫码、查重共用的接口）认得出这件。新开户另外核对 Express 同步来的那件来由是「Express 同步」。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 物料：新建弹窗的字段、来由与按码认出"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")


def ean13(first12: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12))
    return first12 + str((10 - total % 10) % 10)


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    synced = (manifest.get("products") or {}).get("by_express", {}).get("product") if manifest["dataset"] == "new" else None
    lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
frappe.set_user("products@erp.regression")
companies.set_active_company({company!r})
frappe.db.commit()
print(json.dumps({{}}))
""")
    tag = str(uuid.uuid4().int)[:8]
    return {"company": company, "synced": synced, "barcode": ean13("88591" + tag[:7]),
            "name": "flow product " + tag[:4], "cases": []}


def steps(params, camera_code=None):
    name, barcode = json.dumps(params["name"]), json.dumps(params["barcode"])
    out = [
        {"name": "新建弹窗", "js": IDLE + CAPTURE +
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no new button'}; const n=window.__dlgs.length; b.click();"
         "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const d=window.__dlgs[window.__dlgs.length-1]; if(!d||!d.fields_dict.product_name) return {error:'no product dialog'};"
         "return {fields: Object.keys(d.fields_dict)};"},
        {"name": "填写提交", "js": IDLE + "const d=window.__dlgs[window.__dlgs.length-1];"
         f"await d.set_value('product_name', {name}); await d.set_value('unit', 'ชิ้น'); await d.set_value('code_value', {barcode});"
         "await new Promise(r=>setTimeout(r,500)); await idle(); d.get_primary_btn().trigger('click');"
         "for(let k=0;k<40&&d.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {closed: !d.$wrapper.is(':visible')};"},
        {"name": "按条码找商品", "js": IDLE +
         f"const r=await frappe.call({{method:'pearnly_erp_portal.products.service.api_find_product', args:{{value:{barcode}}}}});"
         "return {found: (r.message||{}).product_name||''};"},
    ]
    if params["synced"]:
        out.append({"name": "同步来的物料带来由", "js": IDLE +
                    f"const r=await frappe.call({{method:'pearnly_erp_portal.products.service.api_get_product', args:{{name:{json.dumps(params['synced'])}}}}});"
                    "return {origin: (r.message||{}).origin||'', has_batch_key: 'track_batch' in (r.message||{})};"})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    fields = (by.get("新建弹窗") or {}).get("fields") or []
    checks = [
        {"case": "dialog has no batch, expiry or per unit fields", "seen": fields,
         "ok": "product_name" in fields and not {"track_batch", "track_expiry", "track_serial", "serial_kind"} & set(fields)},
        {"case": "dialog closed after submit", "seen": by.get("填写提交"), "ok": (by.get("填写提交") or {}).get("closed") is True},
        {"case": "found by barcode", "seen": by.get("按条码找商品"), "ok": (by.get("按条码找商品") or {}).get("found") == params["name"]},
    ]
    if params["synced"]:
        seen = by.get("同步来的物料带来由") or {}
        checks.append({"case": "synced product shows its origin", "seen": seen,
                       "ok": seen.get("origin") == "express" and not seen.get("has_batch_key")})
    return checks


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("ERP Product", {{"barcode": {params['barcode']!r}}}, ["product_name", "origin", "track_batch", "track_expiry", "code"], as_dict=True) or {{}}))
""")
    return [{"case": "product saved as created manually", "saved": got,
             "ok": got.get("product_name") == params["name"] and got.get("origin") == "manual" and not got.get("track_batch")
             and not got.get("track_expiry")}]
