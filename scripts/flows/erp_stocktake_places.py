"""流程用例：连续盘点时「实际仓位」下拉列出这个仓的仓位（11-02 三星真机：盘点里点实际仓位每次弹「未找到仓库」）。

起因：录入屏认出物料后把「实际仓库」那格（只收仓库编号的官方 Link）写成了仓库**名称**，实际仓位的选项按这格的值去服务端取，
名称当编号查不到就报「未找到仓库」。这里在一个有仓位清单的仓上开盘点（优先挑这个仓账上没有的两件货，走「账上没有位置」那条：
第一件按盘点的仓预填，第二件起沿用上一件的位置），连续盘两件，每件认出后：实际仓库是这个仓的编号、
点开实际仓位不弹报错、下拉里有这个仓清单上的两个仓位；选第一个仓位保存，库里两条记录都记在这个仓、这个仓位。
"""
from __future__ import annotations

import json
import uuid

from stocktake_lib import type_code

TITLE = "ERP 盘点：连续盘点时实际仓位列出该仓的仓位"
PLACES = ("FLOW-A1", "FLOW-B2")
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def prepare(lab, manifest):
    """这家公司的一个仓带上仓位清单，挑两件普通物料，开一份这个仓的盘点。"""
    company = manifest.get("company") or manifest["companies"][0]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count, locations
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({company!r})
# 接了 Express 的公司不能在系统里建仓：用这家公司第一个仓（`erp.warehouse.codes` 也在它上面建清单），清单里补上这两个仓位
name = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
listed = frappe.get_all("ERP Warehouse Location", filters={{"parent": name, "parenttype": "ERP Warehouse"}},
                        fields=["location_name", "description", "is_active"], order_by="idx asc")
missing = [place for place in {list(PLACES)!r} if place not in {{row.location_name for row in listed}}]
if missing or any(not row.is_active for row in listed if row.location_name in {list(PLACES)!r}):
    rows = [dict(row, is_active=1 if row.location_name in {list(PLACES)!r} else row.is_active) for row in listed]
    locations.api_save_locations(warehouse=name, rows=rows + [{{"location_name": place, "is_active": 1}} for place in missing])
# 先挑这个仓账上没有的两件（走「账上没有位置」：第二件沿用上一件的位置），不够再挑有库存的（走账面位置）
stocked = set(frappe.get_all("ERP Stock Balance", filters={{"warehouse": name, "qty_on_hand": ["!=", 0]}}, pluck="product"))
candidates = frappe.get_all("ERP Product", filters={{"is_active": 1, "track_serial": 0, "code": ["is", "set"]}},
                            fields=["name", "code"], order_by="creation asc", limit_page_length=200)
products = ([p for p in candidates if p.name not in stocked] + [p for p in candidates if p.name in stocked])[:2]
frappe.set_user("stock@erp.regression")
companies.set_active_company({company!r})
doc = count.create_count(title="flow places " + {uuid.uuid4().hex[:6]!r}, warehouse=name)
frappe.db.commit()
print(json.dumps({{"count": doc["name"], "warehouse": name, "products": products}}))
""")
    return {**out, "company": company, "cases": []}


def steps(params, camera_code=None):
    out = [{"name": "开始盘点", "js": IDLE +
            "const btn=()=>[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
            "for(let k=0;k<80&&!btn();k++) await new Promise(r=>setTimeout(r,250)); const b=btn();"
            "if(!b) return {error:'no start button'}; b.click();"
            "for(let k=0;k<80&&!(window.cur_dialog&&cur_dialog.fields_dict.code);k++) await new Promise(r=>setTimeout(r,250));"
            "await idle(); return b.innerText.trim();"}]
    for i, product in enumerate(params["products"]):
        out.append({"name": f"查找 {i + 1}", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.code) return {error:'no stocktake dialog'};"
                    + type_code(json.dumps(product['code'])) + "await idle();"
                    "for(let k=0;k<40&&!d.fields_dict.qty.$wrapper.is(':visible');k++) await new Promise(r=>setTimeout(r,250));"
                    "return {entry: d.fields_dict.qty.$wrapper.is(':visible'), warehouse: d.get_value('warehouse')||''};"})
        # 人点实际仓位：官方 Autocomplete 获得焦点就按 get_query 去取选项；按这格当时的仓去取，报错会弹原生提示框
        out.append({"name": f"点实际仓位 {i + 1}", "js": IDLE + "const d=cur_dialog; const c=d.fields_dict.location;"
                    "const before=$('.modal.show .msgprint').length;"
                    "c.$input.trigger('focus').val('').trigger('input'); await new Promise(r=>setTimeout(r,800)); await idle();"
                    "const box=$('.modal.show .msgprint'); const error=box.length>before?(box.last().text()||'').trim().slice(0,200):'';"
                    "if(error) frappe.msg_dialog&&frappe.msg_dialog.hide();"
                    "const q=c.df.get_query(d.get_values(true)); let options=[], failed='';"
                    "try{const r=await frappe.call({method:q.query, args:{txt:'', ...q.params}}); options=(r.message||[]).map(o=>typeof o==='string'?o:o.value);}"
                    "catch(e){failed=String((e&&e.responseJSON&&e.responseJSON.exc_type)||e); frappe.msg_dialog&&frappe.msg_dialog.hide();}"
                    "const listed=(c.awesomplete&&c.awesomplete._list||[]).map(o=>typeof o==='string'?o:o.value);"
                    "c.$input.trigger('blur'); return {error, failed, options, listed};"})
        out.append({"name": f"保存 {i + 1}", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.qty.$wrapper.is(':visible')) return {error:'no entry'};"
                    f"await d.set_value('location', {json.dumps(PLACES[0])}); await d.set_value('qty', {i + 3});"
                    "await new Promise(r=>setTimeout(r,300)); d.get_primary_btn().trigger('click'); await new Promise(r=>setTimeout(r,300)); await idle();"
                    "return {saved: true};"})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    checks = [{"case": "two products to count", "seen": params["products"], "ok": len(params["products"]) == 2}]
    for i in range(len(params["products"])):
        found, place = by.get(f"查找 {i + 1}") or {}, by.get(f"点实际仓位 {i + 1}") or {}
        checks.append({"case": f"item {i + 1}: actual warehouse holds the warehouse id", "seen": found,
                       "ok": found.get("entry") is True and found.get("warehouse") == params["warehouse"]})
        checks.append({"case": f"item {i + 1}: actual location lists the warehouse's places, no error", "seen": place,
                       "ok": not place.get("error") and not place.get("failed") and set(PLACES) <= set(place.get("options") or [])
                       and set(PLACES) <= set(place.get("listed") or [])})
    return checks


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}, "voided": 0}},
                      fields=["product", "warehouse", "location", "qty"], order_by="creation asc", limit_page_length=10)
print(json.dumps([dict(r, qty=float(r.qty)) for r in rows]))
""")
    expected = [{"product": p["name"], "warehouse": params["warehouse"], "location": PLACES[0], "qty": float(i + 3)}
                for i, p in enumerate(params["products"])]
    return [{"case": "both entries saved in the warehouse and place picked", "saved": rows, "ok": rows == expected}]
