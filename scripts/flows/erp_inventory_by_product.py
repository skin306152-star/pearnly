"""流程用例：库存一览按物料合计（多仓闭环 P4）。

- 准备：新建一件物料（最低库存 5），入库到这家公司的两个仓（3 + 1），公司合计 4 ≤ 5。
- 页面：搜这件物料 → 按位置两行、两行状态都是低库存（按公司合计判，单看每个仓都不到 5 也一样）；
  换「按物料」→ 一行、数量 4、没有仓库列、状态低库存；导出这一看法 → 文件里一行合计 4；点这一行 → 回到按位置、只看这件物料的两行。
"""
from __future__ import annotations

import uuid

from desk_list import ACTION, CAPTURE_EXPORTS, IDLE, workbook_text

TITLE = "ERP 库存一览按物料：两仓合计、低库存按公司合计、点开看各仓"


def prepare(lab, manifest):
    name = "รวมคลัง " + uuid.uuid4().hex[:6]
    return lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service as products
frappe.set_user("admin@erp.regression")
company = companies.active_company("stock@erp.regression")  # 页面以库存岗打开，看的是它当前的公司
companies.set_active_company(company)
houses = frappe.get_all("ERP Warehouse", filters={{"company": company, "is_active": 1}}, fields=["name", "warehouse_name"],
                        order_by="creation asc", limit_page_length=2)
if len(houses) < 2:
    from pearnly_erp_portal.inventory import warehouses
    warehouses.create_warehouse(warehouse_name="คลังรวม " + frappe.generate_hash(length=4), company=company)
    frappe.db.commit()
    houses = frappe.get_all("ERP Warehouse", filters={{"company": company, "is_active": 1}}, fields=["name", "warehouse_name"],
                            order_by="creation asc", limit_page_length=2)
product = products.create_product(product_name={name!r}, unit="ชิ้น", unit_price=100, default_cost=10, min_stock=5)["name"]
frappe.db.commit()
for house, qty in zip(houses, (3, 1)):
    documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=house.name, submit=True,
                           lines=[{{"product": product, "qty": qty, "unit": "ชิ้น", "unit_cost": 10}}])
frappe.db.commit()
print(json.dumps({{"product": product, "name": {name!r}, "warehouses": [h.warehouse_name for h in houses]}}))
""")


# 读看得见的数据行：每行的文字（物料名、数量、状态都在里面）
ROWS = ("const rows=()=>[...document.querySelectorAll('.result .list-row-container')]"
        ".filter(e=>e.offsetParent&&!e.querySelector('.list-row-head')).map(e=>e.innerText.replace(/\\s+/g,' ').trim());"
        "const heads=()=>[...document.querySelectorAll('.list-row-head .list-row-col')].filter(e=>e.offsetParent)"
        ".map(e=>e.innerText.trim()).filter(Boolean);"
        "const wait_rows=async(n)=>{for(let k=0;k<80&&rows().length!==n;k++) await new Promise(r=>setTimeout(r,250)); await idle();};"
        "const field=n=>document.querySelector(`.page-form [data-fieldname=\"${n}\"] input, .page-form [data-fieldname=\"${n}\"] select`);")


def steps(params, camera_code=None):
    name = params["name"]
    return [
        {"name": "按位置", "js": IDLE + ROWS + CAPTURE_EXPORTS + "await idle();"
         "for(let k=0;k<80&&!field('search');k++) await new Promise(r=>setTimeout(r,250));"
         f"$(field('search')).val({name!r}).trigger('change'); await new Promise(r=>setTimeout(r,800)); await idle();"
         "await wait_rows(2); return {rows: rows(), heads: heads()};"},
        {"name": "按物料", "js": IDLE + ROWS + "await idle();"
         "$(field('view')).val('product').trigger('change'); await new Promise(r=>setTimeout(r,800)); await idle();"
         "await wait_rows(1); return {rows: rows(), heads: heads(), options: [...field('view').options].map(o=>o.text)};"},
        {"name": "导出按物料", "js": IDLE + ACTION + CAPTURE_EXPORTS + "await idle();"
         "const before=window.__exports.length; if(!await action('导出')) return {error:'no export button'};"
         "for(let k=0;k<80&&window.__exports.length===before;k++) await new Promise(r=>setTimeout(r,250));"
         "const got=window.__exports.length>before?window.__exports[window.__exports.length-1]:null;"
         "return got?{status:got.status,b64:got.b64}:{error:'no export'};"},
        {"name": "点开看各仓", "js": IDLE + ROWS + "await idle();"
         "const row=[...document.querySelectorAll('.result .list-row-container')].find(e=>e.offsetParent&&!e.querySelector('.list-row-head'));"
         "if(!row) return {error:'no product row'}; (row.querySelector('.list-subject a, .list-row-col a')||row.querySelector('.list-row')||row).click();"
         "for(let k=0;k<80&&field('view').value!=='position';k++) await new Promise(r=>setTimeout(r,250));"
         "await new Promise(r=>setTimeout(r,800)); await idle(); await wait_rows(2);"
         "return {view: field('view').value, rows: rows(), filters: (document.querySelector('.filter-selector .filter-label, .filter-button')?.innerText||'').trim()};"},
    ]


def _value(results, step):
    return next((r.get("value") for r in results if r.get("step") == step), None) or {}


def judge(params, results, camera_code=None):
    name = params["name"]
    position, product, export, opened = (_value(results, step) for step in ("按位置", "按物料", "导出按物料", "点开看各仓"))
    low = ("低库存", "Low", "ต่ำ")
    houses = params["warehouses"]
    checks = [
        {"case": "by location shows the product in both warehouses", "seen": position.get("rows"),
         "ok": len(position.get("rows") or []) == 2 and all(name in row for row in position.get("rows") or [])},
        {"case": "each location is low stock by the company total (3 and 1 against 5)", "seen": position.get("rows"),
         "ok": bool(position.get("rows")) and all(any(word in row for word in low) for row in position.get("rows") or [])},
        {"case": "the view choice has by location and by product", "seen": product.get("options"),
         "ok": len(product.get("options") or []) == 2},
        {"case": "by product shows one row with the total 4 and low stock", "seen": product.get("rows"),
         "ok": len(product.get("rows") or []) == 1 and " 4 " in f" {product['rows'][0]} " and any(word in product["rows"][0] for word in low)},
        {"case": "by product has no warehouse column", "seen": product.get("heads"),
         "ok": bool(product.get("heads")) and not any(house in " ".join(product.get("rows") or []) for house in houses)},
        {"case": "opening the product row goes back to its locations", "seen": opened,
         "ok": opened.get("view") == "position" and len(opened.get("rows") or []) == 2},
    ]
    try:
        _sheets, text = workbook_text(export.get("b64") or "")
        lines = [line for line in text.splitlines() if name in line]
    except Exception as exc:  # noqa: BLE001 — 下载不是文件也算不通过
        lines = [repr(exc)]
    checks.append({"case": "the export follows the by product view", "seen": lines,
                   "ok": export.get("status") == 200 and len(lines) == 1 and ",4," in lines[0].replace(" ", "")})
    return checks


def verify(lab, manifest, params, camera_code=None):
    return []
