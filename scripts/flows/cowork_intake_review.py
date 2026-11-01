"""流程用例：Cowork 录入复核页（表单排版整改 11-01）—— 打开一张票，对方一节左名称 / 地址、右税号 / 分店，税费一节左币种 / 净额 /
折扣、右税额与合计；改分店号、点保存，库里这张票的主页面跟着改。

不跑识别（付费）：票由 `history.storage.create_manual` 手工建（同「手工录入」），对方是卖方（进项）。
电脑宽：两栏左右并排、同行同高；手机宽：各栏纵排，按左栏、右栏的顺序上下排。
"""
from __future__ import annotations

import json
import uuid

TITLE = "Cowork 录入复核：对方与税费两节的分栏、改分店号保存落库"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
FIELDS = ["party_name", "party_addr", "party_tax", "party_branch", "currency", "subtotal", "discount", "vat", "total_amount"]


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:8]
    fields = {"document_type": "receipt", "invoice_number": f"FLOW-{tag}", "date": "2026-10-21", "direction": "purchase",
              "subtotal": "100", "vat": "7", "total_amount": "107", "currency": "THB",
              "seller_name": f"Flow seller {tag}", "seller_tax": "0105561234567", "seller_addr": "1 Flow Road",
              "seller_branch": "00000", "buyer_name": "Flow buyer"}
    made = lab.python(manifest["site"], f"""
from pearnly_cowork.history import storage
frappe.set_user("admin@cowork.regression")
row = storage.create_manual({manifest['workspace']!r}, [{{"page_index": 1, "fields": {json.dumps(fields)}}}], {tag!r})
frappe.db.commit()
print(json.dumps({{"name": row["name"]}}))
""")
    return {"record": made["name"], "seller": fields["seller_name"], "branch": "00002", "cases": []}


def steps(params, camera_code=None):
    names = json.dumps(FIELDS)
    return [
        {"name": "复核页", "js": IDLE + "await idle();"
         "for(let k=0;k<40&&!document.querySelector('.frappe-control[data-fieldname=\"party_tax\"] input');k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); await new Promise(r=>setTimeout(r,800));"
         f"const at={{}}; for(const n of {names}){{const e=[...document.querySelectorAll(`.frappe-control[data-fieldname=\"${{n}}\"]`)].find(x=>x.getClientRects().length);"
         "if(e){const r=e.getBoundingClientRect(); at[n]={x:Math.round(r.left), y:Math.round(r.top)};}}"
         "const v=n=>(document.querySelector(`.frappe-control[data-fieldname=\"${n}\"] input, .frappe-control[data-fieldname=\"${n}\"] textarea`)||{}).value||'';"
         "return {at, seller: v('party_name'), tax: v('party_tax'), branch: v('party_branch')};"},
        {"name": "改分店号保存", "js": IDLE +
         "const input=document.querySelector('.frappe-control[data-fieldname=\"party_branch\"] input');"
         "if(!input) return {error:'no branch input'};"
         f"input.value={json.dumps(params['branch'])}; input.dispatchEvent(new Event('input',{{bubbles:true}})); input.dispatchEvent(new Event('change',{{bubbles:true}}));"
         "await new Promise(r=>setTimeout(r,1200)); await idle();"
         "const b=[...document.querySelectorAll('.page-head .btn-secondary, .page-head .secondary-action')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no save button'}; b.click(); await new Promise(r=>setTimeout(r,800)); await idle();"
         "return {saved:true};"},
    ]


def _paired(at, left, right, stacked):
    """电脑宽：左栏 / 右栏同一行；手机宽（各格同一个 x）：左栏的格排在右栏前面。"""
    if not all(key in at for key in (*left, *right)):
        return False
    if stacked:
        tops = [at[key]["y"] for key in (*left, *right)]
        return tops == sorted(tops)
    return (all(at[key]["x"] == at[left[0]]["x"] for key in left) and all(at[key]["x"] == at[right[0]]["x"] for key in right)
            and at[left[0]]["x"] < at[right[0]]["x"] and abs(at[left[0]]["y"] - at[right[0]]["y"]) <= 4)


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    page = by.get("复核页") or {}
    at = page.get("at") or {}
    stacked = len({spot["x"] for spot in at.values()}) == 1
    return [
        {"case": "the review page shows the stored seller", "seen": {k: page.get(k) for k in ("seller", "tax", "branch")},
         "ok": page.get("seller") == params["seller"] and page.get("tax") == "0105561234567"},
        {"case": "party section: name and address left, tax id and branch right", "seen": at,
         "ok": _paired(at, ["party_name", "party_addr"], ["party_tax", "party_branch"], stacked)},
        {"case": "amounts: currency, net and discount left, tax and totals right", "seen": at,
         "ok": _paired(at, ["currency", "subtotal", "discount"], ["vat", "total_amount"], stacked)},
        {"case": "the changed branch was saved", "seen": by.get("改分店号保存"), "ok": (by.get("改分店号保存") or {}).get("saved") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    now = lab.python(manifest["site"], f"""
from pearnly_cowork.history import storage
doc = storage.project(storage.read({params['record']!r}))
fields = ((doc.get("pages") or [{{}}])[0] or {{}}).get("fields") or {{}}
print(json.dumps({{"branch": fields.get("seller_branch"), "seller": fields.get("seller_name")}}))
""")
    return [{"case": "the record keeps the seller and the new branch", "saved": now,
             "ok": now.get("branch") == params["branch"] and now.get("seller") == params["seller"]}]
