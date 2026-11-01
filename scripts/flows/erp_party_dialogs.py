"""流程用例：ERP 供应商 / 客户弹窗（表单排版整改 11-01）—— 字段先是这家是谁（名称 / 类型 / 税号 / 分支），再是联系
（电话 / 地址 / 备注），电话不夹在税号那组中间；新建一家供应商、一家客户，库里落成填的值。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 供应商 / 客户弹窗：字段分组与新建落库"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")
SUPPLIER = ["supplier_name", "tax_id", "lookup", "branch_type", "branch_no", "phone", "address", "note"]
CLIENT = ["customer_name", "party_type", "tax_id", "lookup", "contact_phone", "address", "note"]


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    return {"supplier": f"Flow supplier {tag}", "client": f"Flow client {tag}", "phone": "0812345678", "cases": []}


def _create(route, key, name_field, phone_field, name, phone):
    return (IDLE + CAPTURE + f"frappe.set_route({json.dumps(route)}); await idle(); await new Promise(r=>setTimeout(r,800));"
            "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
            "if(!b) return {error:'no new button'}; const n=window.__dlgs.length; b.click();"
            "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250)); await idle();"
            "const d=window.__dlgs[window.__dlgs.length-1];"
            f"if(!d||!d.fields_dict.{name_field}) return {{error:'no {key} dialog'}};"
            "const order=d.fields.filter(f=>f.fieldname && f.fieldtype!=='Section Break' && f.fieldtype!=='Column Break').map(f=>f.fieldname);"
            f"await d.set_value({json.dumps(name_field)}, {json.dumps(name)}); await d.set_value({json.dumps(phone_field)}, {json.dumps(phone)});"
            "await new Promise(r=>setTimeout(r,400)); d.get_primary_btn().trigger('click');"
            "for(let k=0;k<40&&d.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250)); await idle();"
            "return {order, closed: !d.$wrapper.is(':visible')};")


def steps(params, camera_code=None):
    return [{"name": "新建供应商", "js": _create("pearnly-suppliers", "supplier", "supplier_name", "phone", params["supplier"], params["phone"])},
            {"name": "新建客户", "js": _create("pearnly-clients", "client", "customer_name", "contact_phone", params["client"], params["phone"])}]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    supplier, client = by.get("新建供应商") or {}, by.get("新建客户") or {}
    return [
        {"case": "supplier dialog: who the supplier is, then contact", "seen": supplier.get("order"),
         "ok": [f for f in supplier.get("order") or [] if f in SUPPLIER] == SUPPLIER},
        {"case": "customer dialog: who the customer is, then contact", "seen": client.get("order"),
         "ok": [f for f in client.get("order") or [] if f in CLIENT] == CLIENT},
        {"case": "both dialogs closed after create", "seen": [supplier.get("closed"), client.get("closed")],
         "ok": supplier.get("closed") is True and client.get("closed") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
print(json.dumps({{"supplier": frappe.db.get_value("ERP Supplier", {{"supplier_name": {params['supplier']!r}}}, "phone"),
                   "client": frappe.db.get_value("ERP Customer", {{"customer_name": {params['client']!r}}}, "contact_phone")}}))
""")
    return [{"case": "supplier saved with its phone", "saved": now.get("supplier"), "ok": now.get("supplier") == params["phone"]},
            {"case": "customer saved with its phone", "saved": now.get("client"), "ok": now.get("client") == params["phone"]}]
