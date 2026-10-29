"""流程用例：LINE 手机盘点页 —— 扫到系统没有的码当场新建商品、选库位记数（盘点菜单做完整 S3 / S5，用户 10-29 批复）。

LINE 里的 LIFF 验签走不了实验室，这里走同一个页面的「本站点已登录账号自己的绑定」回落路径：实验室给盘点岗账号建一条合成绑定
（只在实验室）。打开 `/stocktake` → 进这份盘点 → 输一个系统里没有的码 → 出新建表单，填品名、单位 → 建好认出这件 →
仓位格从库位清单选（有清单时，选项带说明）→ 记 2 件。库里：新商品来由「盘点新建 · 这份盘点」；这一条记录数量 2、来源 LINE、库位对。
公司先设成「汽车」类型：扫按台商品的商品码，同电脑只提示要逐台扫、不出数量框，也不为它登记快照行（抬头 y 不变，验收 10-29）；
跑完公司类型还原成普通。老数据没有按台商品，先建一件本地的按台商品。
"""
from __future__ import annotations

import json
import uuid

TITLE = "LINE 手机盘点：当场新建商品、选库位记数"


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    products = manifest.get("products")
    unit_code = products.get("by_unit", {}).get("scan", "") if isinstance(products, dict) else ""
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count, locations
from pearnly_erp_portal.masters import company as companies
user = "stock@erp.regression"
if not frappe.db.exists("Pearnly LINE Binding", {{"user": user, "status": "active"}}):
    frappe.get_doc({{"doctype": "Pearnly LINE Binding", "user": user, "line_user_id": "Uflowlab" + frappe.generate_hash(length=16),
                    "status": "active"}}).insert(ignore_permissions=True)
frappe.set_user(user)
companies.set_active_company({company!r})
frappe.db.set_value("ERP Company", {company!r}, "company_type", "vehicle", update_modified=False)
unit_code = {unit_code!r}
if not unit_code:
    from pearnly_erp_portal.inventory import serials
    from pearnly_erp_portal.products import service as products
    frappe.set_user("admin@erp.regression")
    unit_code = "FLOW-CAR-01"
    car = frappe.db.get_value("ERP Product", {{"code": unit_code}}) or products.create_product(product_name="รถทดสอบ flow", code=unit_code,
                                                                                               unit="คัน", unit_price=500000, default_cost=400000)["name"]
    if not frappe.db.get_value("ERP Product", car, "track_serial"):
        serials.turn_on(car, kind="vehicle")
    frappe.set_user(user)
wh = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
doc = count.create_count(title="flow line " + {uuid.uuid4().hex[:6]!r}, warehouse=wh)
places = frappe.get_all("ERP Warehouse Location", filters={{"parent": wh, "parenttype": "ERP Warehouse", "is_active": 1}}, pluck="location_name",
                        order_by="idx asc", limit=1)
frappe.db.commit()
print(json.dumps({{"count": doc["name"], "location": places[0] if places else "", "unit_code": unit_code,
                  "unit_product": frappe.db.get_value("ERP Product", {{"code": unit_code}})}}))
""")
    code = "8858" + str(uuid.uuid4().int)[:8]
    return {**out, "company": company, "code": code, "name": "flow line " + code[-4:], "cases": []}


def steps(params, camera_code=None):
    count, code, name, loc = (json.dumps(params[k]) for k in ("count", "code", "name", "location"))
    wait = "const until=async(f,n=60)=>{for(let k=0;k<n;k++){const v=f();if(v)return v;await new Promise(r=>setTimeout(r,250));}return null;};"
    return [
        {"name": "进盘点", "js": wait +
         f"await until(()=>document.querySelector('[data-task]')||document.getElementById('code'),120);"
         f"const t=document.querySelector('[data-task=\"'+{count}+'\"]'); if(t) t.click();"
         "const c=await until(()=>document.getElementById('code')); return c?{opened:true}:{error:'stocktake not opened: '+document.body.innerText.slice(0,200)};"},
        {"name": "按台商品要逐台扫", "js": wait +
         # 抬头「已盘点 x / y」：查之前、查之后各读一次，y 不能变（不为按台商品补登快照行）
         "const head=()=>{const m=document.body.innerText.match(/(\\d+)\\s*\\/\\s*(\\d+)/);return m?Number(m[2]):null;}; const before=head();"
         "const toast=document.querySelector('.toast, #toast')||document.body; const old=toast.textContent;"
         f"document.getElementById('code').value={json.dumps(params['unit_code'])}; document.getElementById('find').click();"
         "await until(()=>toast.textContent!==old&&toast.textContent.trim()); await new Promise(r=>setTimeout(r,800));"
         "return {qty_box: !!document.getElementById('qty'), message: (toast.textContent||'').trim().slice(0,200), y_before: before, y_after: head()};"},
        {"name": "输没有的码", "js": wait +
         f"document.getElementById('code').value={code}; document.getElementById('find').click();"
         "const n=await until(()=>document.getElementById('np_name')); return {new_form: !!n, button: n?document.getElementById('np_save').innerText.trim():''};"},
        {"name": "新建", "js": wait +
         f"document.getElementById('np_name').value={name}; document.getElementById('np_unit').value='ชิ้น'; document.getElementById('np_save').click();"
         f"const b=await until(()=>[...document.querySelectorAll('.card b')].find(e=>e.innerText.trim()==={name}));"
         "return {recognised: !!b, options: [...document.querySelectorAll('#loclist option')].map(o=>[o.value,o.label])};"},
        {"name": "选库位记数", "js": wait +
         f"const l=document.getElementById('loc'); if(!l) return {{error:'no location box'}}; l.value={loc};"
         "document.getElementById('qty').value='2'; document.getElementById('save').click();"
         "await new Promise(r=>setTimeout(r,2500)); return {error_text: (document.getElementById('error')||{}).innerText||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    made = by.get("新建", {})
    options = made.get("options") or []
    unit = by.get("按台商品要逐台扫", {})
    return [
        {"case": "unit tracked product asks for one unit at a time", "seen": unit,
         "ok": unit.get("qty_box") is False and bool(unit.get("message")) and unit.get("y_before") is not None
         and unit.get("y_after") == unit.get("y_before")},
        {"case": "unknown code offers a new product form", "seen": by.get("输没有的码"), "ok": (by.get("输没有的码") or {}).get("new_form") is True},
        {"case": "new product recognised", "seen": made, "ok": made.get("recognised") is True},
        {"case": "location list offered", "seen": options, "ok": not params["location"] or any(o[0] == params["location"] for o in options)},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
p = frappe.db.get_value("ERP Product", {{"barcode": {params['code']!r}}}, ["name", "origin", "origin_name"], as_dict=True) or {{}}
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}, "voided": 0}}, fields=["product", "qty", "source", "location"])
unit_rows = frappe.db.count("ERP Stock Count Item", {{"count": {params['count']!r}, "product": {params['unit_product']!r},
                                                     "row_origin": ["is", "set"]}})  # 查码补登的行（账上快照行不算）
frappe.db.set_value("ERP Company", {params['company']!r}, "company_type", "general", update_modified=False)  # 还原
frappe.db.commit()
print(json.dumps({{"product": p, "entries": [dict(r, qty=float(r.qty), location=r.location or "") for r in rows], "unit_rows": unit_rows}}, default=str))
""")
    p = got["product"]
    return [
        {"case": "product origin is this count", "saved": p, "ok": p.get("origin") == "stocktake" and p.get("origin_name") == params["count"]},
        {"case": "no count row added for the unit tracked product by the lookup", "saved": got["unit_rows"], "ok": got["unit_rows"] == 0},
        {"case": "entry saved from LINE at the chosen location", "saved": got["entries"],
         "ok": got["entries"] == [{"product": p.get("name"), "qty": 2.0, "source": "LINE", "location": params["location"]}]},
    ]
