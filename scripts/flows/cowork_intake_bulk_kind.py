"""流程用例：Cowork 复核页「批量编辑」（11-06 按行选物料与库存，评估 2026-11-06 第 15 节 C9）—— 打开一张有两行明细的票，
点明细下面的「批量编辑」，原生弹窗里「全部」选存货、点保存，再点页面的保存：库里这张票两行的过账类型都是存货。

不跑识别（付费）：票由 `history.storage.create_manual` 手工建（同「手工录入」），没有去向（不是 MR.ERP、不是仅识别），过账类型照常显示。
"""
from __future__ import annotations

import json
import uuid

TITLE = "Cowork 复核页批量编辑：全部设成存货、保存后两行都落库"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:8]
    fields = {"document_type": "tax_invoice", "invoice_number": f"FLOW-B{tag}", "date": "2026-10-21", "direction": "purchase",
              "subtotal": "100", "vat": "7", "total_amount": "107", "currency": "THB",
              "seller_name": f"Flow seller {tag}", "seller_tax": "0105561234567", "seller_branch": "00000",
              "buyer_name": "Flow buyer",
              "items": [{"name": f"Ice {tag}", "qty": "1", "price": "60", "subtotal": "60"},
                        {"name": f"Freight {tag}", "qty": "1", "price": "40", "subtotal": "40", "posting_kind": "service"}]}
    made = lab.python(manifest["site"], f"""
from pearnly_cowork.history import storage
frappe.set_user("admin@cowork.regression")
row = storage.create_manual({manifest['workspace']!r}, [{{"page_index": 1, "fields": {json.dumps(fields)}}}], {tag!r})
frappe.db.commit()
print(json.dumps({{"name": row["name"]}}))
""")
    return {"record": made["name"], "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "打开批量编辑", "js": IDLE + "await idle();"
         "for(let k=0;k<40&&!document.querySelector('.frappe-control[data-fieldname=\"bulk_kind\"] button');k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); await new Promise(r=>setTimeout(r,600));"
         "const b=[...document.querySelectorAll('.frappe-control[data-fieldname=\"bulk_kind\"] button')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no bulk edit button'}; b.click(); await new Promise(r=>setTimeout(r,800)); await idle();"
         "const d=[...document.querySelectorAll('.modal.show')].pop();"
         "if(!d) return {error:'no dialog'};"
         "const rows=[...d.querySelectorAll('.grid-body .grid-row')].length;"
         "return {rows};"},
        {"name": "全部设成存货并保存", "js": IDLE +
         "const d=[...document.querySelectorAll('.modal.show')].pop(); if(!d) return {error:'no dialog'};"
         "const s=d.querySelector('.frappe-control[data-fieldname=\"all\"] select'); if(!s) return {error:'no all select'};"
         "s.value='stock'; s.dispatchEvent(new Event('change',{bubbles:true})); await new Promise(r=>setTimeout(r,600));"
         "const ok=d.querySelector('.modal-footer .btn-primary'); if(!ok) return {error:'no primary'}; ok.click();"
         "await new Promise(r=>setTimeout(r,900)); await idle();"
         "const kinds=[...document.querySelectorAll('.frappe-control[data-fieldname=\"posting_kind\"] select')].filter(e=>e.closest('[data-fieldname=\"lines\"]')).map(e=>e.value);"
         "const save=[...document.querySelectorAll('.page-head .btn-secondary, .page-head .secondary-action')].find(e=>e.offsetParent);"
         "if(!save) return {error:'no save button', kinds}; save.click(); await new Promise(r=>setTimeout(r,900)); await idle();"
         "return {kinds, saved:true};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opened, done = by.get("打开批量编辑") or {}, by.get("全部设成存货并保存") or {}
    return [
        {"case": "the bulk edit dialog lists both lines", "seen": opened, "ok": opened.get("rows") == 2},
        {"case": "both lines on the page are stock after the dialog", "seen": done,
         "ok": done.get("kinds") == ["stock", "stock"] and done.get("saved") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
from pearnly_cowork.history import storage
doc = storage.project(storage.read({params['record']!r}))
fields = ((doc.get("pages") or [{{}}])[0] or {{}}).get("fields") or {{}}
print(json.dumps({{"kinds": [row.get("posting_kind") for row in fields.get("items") or []]}}))
""")
    return [{"case": "the record keeps both lines as stock", "saved": now, "ok": now.get("kinds") == ["stock", "stock"]}]
