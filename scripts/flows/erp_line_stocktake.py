"""流程用例：LINE 手机盘点页 —— 扫到系统没有的码当场新建商品、选库位记数（盘点菜单做完整 S3 / S5，用户 10-29 批复）。

LINE 里的 LIFF 验签走不了实验室，这里走同一个页面的「本站点已登录账号自己的绑定」回落路径：实验室给盘点岗账号建一条合成绑定
（只在实验室）。打开 `/stocktake` → 进这份盘点 → 输一个系统里没有的码 → 出新建表单（与物料页新建弹窗同一套字段，码已预填，
汽车公司带按台两格、号码种类默认「车」），填品名、单位、售价 → 建好认出这件 →
仓位格从库位清单选（有清单时，选项带说明）→ 记 2 件 →「实际仓库」默认是这份盘点的仓，换成同公司另一个仓再记 1 件（岳父 10-26：手机上
也能改仓库，同电脑）。库里：新商品来由「盘点新建 · 这份盘点」；第一条数量 2、来源 LINE、盘点的仓、库位对；第二条数量 1、记在换过去的仓。
公司先设成「汽车」类型：扫按台商品的商品码，同电脑只提示要逐台扫、不出数量框，也不为它登记快照行（抬头 y 不变，验收 10-29）；
跑完公司类型还原成普通。老数据没有按台商品，先建一件本地的按台商品。
"""
from __future__ import annotations

import json
import uuid

TITLE = "LINE 手机盘点：当场新建商品、选库位记数"
# 新建表单的各格（`www/stocktake.html` 的 NEW_FIELDS，照物料页新建弹窗；按台两格只在汽车 / 手机电器公司出现）
# 按页面上的先后比（岳父 11-01：条码、名称、分类、特性/颜色在前，同物料页弹窗）
NEW_FIELDS = ["code", "product_name", "category", "attribute", "internal_code", "unit", "base_unit", "unit_price", "default_cost",
              "default_location", "min_stock", "track_serial", "serial_kind", "vat_applicable"]


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
stores = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, pluck="name", order_by="creation asc", limit=2)
wh = stores[0]
if len(stores) < 2:  # 老数据只有一个仓：建第二个，好测「换仓」
    from pearnly_erp_portal.inventory import warehouses
    frappe.set_user("admin@erp.regression")
    stores.append(warehouses.create_warehouse(warehouse_name="flow 二号仓", company={company!r})["name"])
    frappe.set_user(user)
doc = count.create_count(title="flow line " + {uuid.uuid4().hex[:6]!r}, warehouse=wh)
places = frappe.get_all("ERP Warehouse Location", filters={{"parent": wh, "parenttype": "ERP Warehouse", "is_active": 1}}, pluck="location_name",
                        order_by="idx asc", limit=1)
frappe.db.commit()
print(json.dumps({{"count": doc["name"], "location": places[0] if places else "", "unit_code": unit_code, "warehouse": wh, "other": stores[1],
                  "unit_product": frappe.db.get_value("ERP Product", {{"code": unit_code}})}}))
""")
    code = "8858" + str(uuid.uuid4().int)[:8]
    # 分类写一个系统里还没有的名称（泰文 + 尺寸）：建商品时一并建出这条分类
    return {**out, "company": company, "code": code, "name": "flow line " + code[-4:], "category": "ไซส์ XL " + code[-4:],
            "cases": []}


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
        # 网址（TIS 认证码）不是商品码：提示一句、不出新建卡片（认码那一层统一拒，岳父 11-01）
        {"name": "输网址码", "js": wait +
         "const toast=document.querySelector('.toast, #toast')||document.body; const old=toast.textContent;"
         "document.getElementById('code').value='https://appdb.tisi.go.th/Q/i.php?d=PEARNLYFLOW0002'; document.getElementById('find').click();"
         "await until(()=>toast.textContent!==old&&toast.textContent.trim()); await new Promise(r=>setTimeout(r,800));"
         "return {message: (toast.textContent||'').trim().slice(0,200), new_form: !!document.getElementById('np_product_name')};"},
        {"name": "输没有的码", "js": wait +
         f"document.getElementById('code').value={code}; document.getElementById('find').click();"
         "const n=await until(()=>document.getElementById('np_product_name'));"
         f"const ids=[...document.querySelectorAll('#newproduct [id^=np_]')].map(e=>e.id.slice(3)).filter(x=>{json.dumps(NEW_FIELDS)}.includes(x));"
         "return {new_form: !!n, button: n?document.getElementById('np_save').innerText.trim():'', fields: ids,"
         " code: (document.getElementById('np_code')||{}).value||'', serial_kind: (document.getElementById('np_serial_kind')||{}).value||'',"
         " vat: !!(document.getElementById('np_vat_applicable')||{}).checked};"},
        {"name": "新建", "js": wait +
         f"document.getElementById('np_product_name').value={name}; document.getElementById('np_unit').value='ชิ้น';"
         "document.getElementById('np_unit_price').value='45';"
         # 分类写名称：这页没有的名称建档时一并建（盘点录入权即可，主会话 11-01）
         f"document.getElementById('np_category').value={json.dumps(params['category'])}; document.getElementById('np_attribute').value='ดำ';"
         "document.getElementById('np_save').click();"
         f"const b=await until(()=>[...document.querySelectorAll('.card b')].find(e=>e.innerText.trim()==={name}));"
         "return {recognised: !!b, options: [...document.querySelectorAll('#loclist option')].map(o=>[o.value,o.label]),"
         # 认出后的物料信息先后（岳父 11-01：条码、名称、分类、特性/颜色，再接编码）
         " order: [...document.querySelectorAll('#product_info [data-k]')].map(e=>e.dataset.k),"
         " barcode: ((document.querySelector('#product_info [data-k=barcode] span:last-child')||{}).innerText||'').trim()};"},
        {"name": "选库位记数", "js": wait +
         f"const l=document.getElementById('loc'); if(!l) return {{error:'no location box'}}; l.value={loc};"
         "const store=document.getElementById('store'); const shown=store?store.value:'';"
         "document.getElementById('qty').value='2'; document.getElementById('save').click();"
         "await new Promise(r=>setTimeout(r,2500)); return {error_text: (document.getElementById('error')||{}).innerText||'', store_default: shown};"},
        {"name": "换仓记数", "js": wait +
         "const store=await until(()=>document.getElementById('store')); if(!store) return {error:'no warehouse box'};"
         f"store.value={json.dumps(params['other'])}; store.dispatchEvent(new Event('change'));"
         "await new Promise(r=>setTimeout(r,1500)); document.getElementById('qty').value='1'; document.getElementById('save').click();"
         "await new Promise(r=>setTimeout(r,2500)); return {chosen: store.value, options: [...store.options].map(o=>o.value), location: document.getElementById('loc')?.value};"},
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
        {"case": "web address is not a product barcode and offers no new product", "seen": by.get("输网址码"),
         "ok": bool((by.get("输网址码") or {}).get("message")) and (by.get("输网址码") or {}).get("new_form") is False},
        {"case": "unknown code offers a new product form", "seen": by.get("输没有的码"), "ok": (by.get("输没有的码") or {}).get("new_form") is True},
        {"case": "new product form has the product dialog's fields", "seen": by.get("输没有的码"),
         "ok": (by.get("输没有的码") or {}).get("fields") == NEW_FIELDS and (by.get("输没有的码") or {}).get("code") == params["code"]
         and (by.get("输没有的码") or {}).get("serial_kind") == "vehicle" and (by.get("输没有的码") or {}).get("vat") is True},
        {"case": "new product recognised", "seen": made, "ok": made.get("recognised") is True},
        {"case": "recognised product shows barcode, name, category, attribute, then code", "seen": made,
         "ok": made.get("order") == ["barcode", "name", "category", "attribute", "code"] and made.get("barcode") == params["code"]},
        {"case": "location list offered", "seen": options, "ok": not params["location"] or any(o[0] == params["location"] for o in options)},
        {"case": "warehouse defaults to the count's", "seen": by.get("选库位记数"), "ok": (by.get("选库位记数") or {}).get("store_default") == params["warehouse"]},
        {"case": "warehouse list offers the company's other warehouse", "seen": by.get("换仓记数"),
         "ok": params["other"] in ((by.get("换仓记数") or {}).get("options") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
p = frappe.db.get_value("ERP Product", {{"barcode": {params['code']!r}}}, ["name", "origin", "origin_name", "unit", "unit_price", "price_set",
                        "vat_applicable", "track_serial", "category", "attribute"], as_dict=True) or {{}}
p["category_name"] = frappe.db.get_value("ERP Product Category", p.get("category"), "category_name") if p.get("category") else None
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}, "voided": 0}}, fields=["product", "qty", "source", "location", "warehouse"],
                      order_by="creation asc")
unit_rows = frappe.db.count("ERP Stock Count Item", {{"count": {params['count']!r}, "product": {params['unit_product']!r},
                                                     "row_origin": ["is", "set"]}})  # 查码补登的行（账上快照行不算）
frappe.db.set_value("ERP Company", {params['company']!r}, "company_type", "general", update_modified=False)  # 还原
frappe.db.commit()
print(json.dumps({{"product": p, "entries": [dict(r, qty=float(r.qty), location=r.location or "") for r in rows], "unit_rows": unit_rows}}, default=str))
""")
    p = got["product"]
    return [
        {"case": "product origin is this count", "saved": p, "ok": p.get("origin") == "stocktake" and p.get("origin_name") == params["count"]},
        {"case": "new product fields saved", "saved": p,
         "ok": p.get("unit") == "ชิ้น" and float(p.get("unit_price") or 0) == 45 and p.get("price_set") == 1 and p.get("vat_applicable") == 1
         and not p.get("track_serial") and p.get("category_name") == params["category"] and p.get("attribute") == "ดำ"},
        {"case": "no count row added for the unit tracked product by the lookup", "saved": got["unit_rows"], "ok": got["unit_rows"] == 0},
        {"case": "entry saved from LINE at the chosen location", "saved": got["entries"],
         "ok": got["entries"][:1] == [{"product": p.get("name"), "qty": 2.0, "source": "LINE", "location": params["location"],
                                       "warehouse": params["warehouse"]}]},
        {"case": "second entry saved in the warehouse chosen on the phone", "saved": got["entries"][1:],
         "ok": [(e["qty"], e["warehouse"], e["source"]) for e in got["entries"][1:]] == [(1.0, params["other"], "LINE")]},
    ]
