"""流程用例：手机盘点页 `/stocktake` 按码记数 → 抬头「已盘点」加 1 → 撤销回退；查码只收 POST
（10-29 手机盘点空仓保存 404 热修 15ffa12d / b7f49675，fcb65cca 逐入口实测问题 1）。

LINE 里的验签走不了实验室，走同一页「本站点已登录账号自己的绑定」回落：实验室给库存岗账号建一条合成绑定（只在实验室）。
每套数据开一份盘点（新开户是零库存的空仓，就是当时出 404 的场景），数据清单里每一种认法的码：输码 → 出录入卡 → 记 2 →
出这一条记录（带撤销）、实盘 2、抬头加 1 → 撤销 → 抬头回到原数。最后用 GET 直接调查码接口要被拒。
库里：每件一条记录、数量 2、已撤销。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import codes, company, js_value, warehouses  # noqa: E402

TITLE = "手机盘点页：记数、抬头计数、撤销、查码只收 POST"
USER = "stock@erp.regression"


def prepare(lab, manifest):
    co = company(manifest)
    warehouse = warehouses(manifest)[-1]["name"]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
from pearnly_erp_portal.masters import company as companies
user = {USER!r}
if not frappe.db.exists("Pearnly LINE Binding", {{"user": user, "status": "active"}}):
    frappe.get_doc({{"doctype": "Pearnly LINE Binding", "user": user, "line_user_id": "Uflowlab" + frappe.generate_hash(length=16),
                    "status": "active", "display_name": "flow lab", "bound_at": frappe.utils.now()}}).insert(ignore_permissions=True)
frappe.set_user(user)
companies.set_active_company({co!r})
doc = count.create_count(title="flow phone " + {uuid.uuid4().hex[:6]!r}, warehouse={warehouse!r})
frappe.db.commit()
print(json.dumps({{"count": doc["name"]}}))
""")
    found = codes(manifest)
    return {"count": out["count"], "cases": [{"key": key, "scan": item["scan"], "product": item["product"]} for key, item in found.items()]}


HELP = """
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const until=async(fn,ms=15000)=>{const end=Date.now()+ms;let v;while(Date.now()<end){try{v=await fn();}catch(e){v=null;}if(v)return v;await sleep(200);}return v;};
const counted=()=>{const m=((document.querySelector('.card .muted')||{}).textContent||'').match(/(\\d+)\\s*\\/\\s*(\\d+)/);return m?Number(m[1]):null;};
"""


def steps(params, camera_code=None):
    count = js_value(params["count"])
    out = [{"name": "打开这份盘点", "js": HELP + f"""
await until(()=>document.querySelector('[data-task], #code, .card.error'), 20000);
if (document.querySelector('.card.error')) return {{error: document.querySelector('.card.error').innerText.slice(0,200)}};
const open=document.querySelector('[data-task="'+{count}+'"]'); if (open) open.click();
return {{opened: !!await until(()=>document.getElementById('code'), 15000)}};"""}]
    for case in params["cases"]:
        out.append({"name": f"记数撤销 {case['key']}", "js": HELP + f"""
const input=document.getElementById('code'); if(!input) return {{error:'no code input'}};
const stale=document.getElementById('qty');  // 上一件的录入卡：要等它换成这一件的新卡再填
input.value={js_value(case['scan'])}; document.getElementById('find').click();
const qty=await until(()=>{{const q=document.getElementById('qty');return q&&q!==stale?q:null;}}, 20000);
if(!qty) return {{entry:false, error_text:(document.getElementById('error')||{{}}).textContent||''}};
const before=counted(); qty.value='2'; document.getElementById('save').click();
await until(()=>document.querySelector('[data-undo]') && counted()===before+1, 20000);
const after=counted(); const card=document.getElementById('qty')&&document.getElementById('qty').closest('.card');
const shown=card?[...card.querySelectorAll('.big')].map(e=>e.textContent.trim()):[];
const undo=document.querySelector('[data-undo]'); if(undo) undo.click();
await until(()=>!document.querySelector('[data-undo]') && counted()===before, 20000);
return {{entry:true, before, after, undone: counted(), counted_qty: shown[1]||null}};"""})
    out.append({"name": "GET 调查码", "js": f"""
const r=await fetch('/api/method/pearnly_erp_portal.inventory.line_mobile.api_line_stocktake_lookup?name='+encodeURIComponent({count})+'&code=x', {{credentials:'include'}});
return {{status: r.status}};"""})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    checks = [{"case": "打开盘点", "seen": by.get("打开这份盘点"), "ok": bool(by.get("打开这份盘点", {}).get("opened"))}]
    for case in params["cases"]:
        v = by.get(f"记数撤销 {case['key']}", {})
        checks.append({"case": case["key"], "seen": v, "ok": v.get("entry") and v.get("before") is not None
                       and v.get("after") == v["before"] + 1 and v.get("undone") == v["before"] and v.get("counted_qty") == "2"})
    status = by.get("GET 调查码", {}).get("status")
    checks.append({"case": "GET 被拒", "seen": status, "ok": status in (403, 405)})
    return checks


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}}}, fields=["product", "qty", "voided", "source"],
                      limit_page_length=50)
print(json.dumps([dict(r, qty=float(r.qty)) for r in rows]))
""")
    checks = []
    for case in params["cases"]:
        hit = [r for r in rows if r["product"] == case["product"]]
        checks.append({"case": f"记录 {case['key']}", "seen": hit, "ok": len(hit) == 1 and hit[0]["qty"] == 2 and bool(hit[0]["voided"])})
    return checks
