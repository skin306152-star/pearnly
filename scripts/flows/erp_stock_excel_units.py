"""流程用例：期初 / 盘点 Excel 按台（岳父 10-25：盘点用的 Excel 模板带上内部编码和车辆栏位；评估 docs/work/2026-10-30-father-three-items-评估.md 第三项）。

只跑老数据（期初只给没接 Express 的公司）。公司设成「汽车」类型，建一件本地按台商品（没有库存，直接开按台）：
- 期初：同一个仓三台一行一台（数量 1、单价一样）预览 → 提交，一个位置一笔期初、账面 3，三台都登记在这个仓、在库；
- 盘点：开一份这个仓的盘点，上传一张只有车架号与实盘两列的表：第一台 1、第二台 0、一个系统里没有的号 1 →
  第一台逐台记上、第二台不记、没有的号逐行报「该台不存在」（不当场建台）；同一张表再传一次不重记；
- 盘点模板（这份盘点的表）：这件商品一台一行、带车架号，第一台实盘 1。
跑完公司类型还原成普通（同 erp_stock_in_units.py）。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, company, js_value, stock_of, warehouses  # noqa: E402

TITLE = "ERP 期初 / 盘点 Excel 按台：汽车公司一台一行，车架号认台，未知号只报不建"


def prepare(lab, manifest):
    co = company(manifest)
    warehouse = warehouses(manifest)[0]["name"]
    tag = uuid.uuid4().hex[:6].upper()
    vins = [f"XLS{tag}{suffix}" for suffix in ("A", "B", "C")]
    out = lab.python(manifest["site"], f"""
import io, openpyxl
from pearnly_erp_portal.inventory import serials
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service as products
frappe.set_user("admin@erp.regression")
companies.set_active_company({co!r})
frappe.db.set_value("ERP Company", {co!r}, "company_type", "vehicle", update_modified=False)
product = products.create_product(product_name="รถ Excel {tag}", unit="คัน", unit_price=500000, default_cost=400000)["name"]
serials.turn_on(product, kind="vehicle")
book = openpyxl.Workbook()
for row in (["serial_no", "counted_qty"], [{vins[0]!r}, 1], [{vins[1]!r}, 0], ["XLS{tag}ZZ", 1]):
    book.active.append(row)
buffer = io.BytesIO(); book.save(buffer)
file = frappe.get_doc({{"doctype": "File", "file_name": "count-{tag}.xlsx", "content": buffer.getvalue(), "is_private": 1}})
file.insert(ignore_permissions=True)
frappe.db.commit()
print(json.dumps({{"product": product, "code": frappe.db.get_value("ERP Product", product, "code"), "file_url": file.file_url}}))
""")
    return {**out, "company": co, "warehouse": warehouse, "vins": vins, "title": f"Excel 按台 {tag}"}


def steps(params, camera_code=None):
    api = "pearnly_erp_portal.inventory.api."
    rows = [{"product_code": params["code"], "serial_no": vin, "qty": "1", "unit_cost": "400000"} for vin in params["vins"]]
    rows[0].update(secondary_no="ENG-1", color="แดง")
    head = {"warehouse": params["warehouse"], "rows": rows}
    return [
        {"name": "期初预览与提交", "js": JS + f"""
const head={js_value(head)}; head.as_of_date=frappe.datetime.get_today(); head.rows=JSON.stringify(head.rows);
const review=await call({js_value(api + 'api_preview_stock_opening')}, head);
if(!review||!review.valid) return {{valid:false, rows:(review||{{}}).rows}};
const done=await call({js_value(api + 'api_submit_stock_opening')}, {{...head, review_token:review.review_token}});
return {{valid:true, statuses:review.rows.map(r=>r.status), serials:review.rows.map(r=>r.serial_no), done}};"""},
        {"name": "上传盘点表", "js": JS + f"""
const task=await call({js_value(api + 'api_create_stock_count')}, {{title:{js_value(params['title'])}, warehouse:{js_value(params['warehouse'])}}});
if(!task||!task.name) return {{error:'no count'}};
const first=await call({js_value(api + 'api_stock_count_import')}, {{name:task.name, file_url:{js_value(params['file_url'])}}});
const again=await call({js_value(api + 'api_stock_count_import')}, {{name:task.name, file_url:{js_value(params['file_url'])}}});
return {{count:task.name, first, again}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opening, upload = by.get("期初预览与提交") or {}, by.get("上传盘点表") or {}
    first, again = upload.get("first") or {}, upload.get("again") or {}
    unknown = [p for p in first.get("problems") or [] if p.get("reason") == "unit_not_found"]
    return [
        {"case": "opening preview reads three units at one place and submits one position", "seen": opening,
         "ok": opening.get("valid") is True and opening.get("statuses") == ["ready"] * 3
         and opening.get("serials") == params["vins"] and (opening.get("done") or {}).get("created") == 1},
        {"case": "stocktake upload counts the first unit, skips the zero and lists the unknown number", "seen": first,
         "ok": first.get("rows") == 2 and first.get("counted") == 2 and len(unknown) == 1 and unknown[0].get("row") == 4},
        {"case": "the same file uploaded again records nothing new", "seen": again,
         "ok": again.get("rows") == 2 and len(again.get("problems") or []) == 1},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count as counts
frappe.set_user("admin@erp.regression")
units = frappe.get_all("ERP Serial No", filters={{"product": {params['product']!r}}},
                       fields=["name", "serial_no", "status", "warehouse", "secondary_no", "color"], order_by="serial_no asc", limit_page_length=10)
task = frappe.db.get_value("ERP Stock Count", {{"title": {params['title']!r}}}, "name")
entries = frappe.get_all("ERP Stock Count Entry", filters={{"count": task, "voided": 0}}, fields=["serial", "qty", "source"], limit_page_length=10)
# 同一个仓里别的流程也留了按台的车：只看这次建的三台
template = [row for row in counts.template_rows(name=task) if row.get("serial_no") in {params['vins']!r}] if task else []
kind = counts.template_unit_kind(name=task) if task else None
frappe.db.set_value("ERP Company", {params['company']!r}, "company_type", "general", update_modified=False)  # 还原
frappe.db.commit()
print(json.dumps({{"units": units, "entries": entries, "kind": kind,
                  "template": [{{"serial_no": r["serial_no"], "counted": r["counted_qty"]}} for r in template]}}, default=str))
""")
    units = {unit["serial_no"]: unit for unit in got["units"]}
    first = units.get(params["vins"][0]) or {}
    book = stock_of(lab, manifest["site"], params["product"], params["warehouse"])
    return [
        {"case": "three units registered in stock in the warehouse and the book is 3", "seen": {"units": got["units"], "stock": book},
         "ok": sorted(units) == sorted(params["vins"]) and all(u["status"] == "in_stock" and u["warehouse"] == params["warehouse"]
                                                              for u in units.values())
         and first.get("secondary_no") == "ENG-1" and first.get("color") == "แดง" and book == 3},
        {"case": "one import record for the first unit only, and no unit was made for the unknown number",
         "seen": got["entries"], "ok": [(e["serial"], e["source"]) for e in got["entries"]] == [(first.get("name"), "Import")]},
        {"case": "the count template lists each unit with its chassis number, the first counted", "seen": got["template"],
         "ok": got["kind"] == "vehicle" and sorted(r["serial_no"] for r in got["template"]) == sorted(params["vins"])
         and {r["serial_no"]: r["counted"] for r in got["template"]}.get(params["vins"][0]) == 1},
    ]
