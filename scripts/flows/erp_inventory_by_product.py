"""流程用例：库存一览按物料合计（多仓闭环 P4）。

- 准备：新建一件物料（最低库存 5），入库到这家公司的两个仓（3 + 1），公司合计 4 ≤ 5。
- 页面：搜这件物料 → 按位置两行、两行状态都是低库存（按公司合计判，单看每个仓都不到 5 也一样）；
  换「按物料」→ 一行、数量 4、没有仓库列、状态低库存；导出这一看法 → 文件里一行合计 4；点这一行 → 回到按位置、只看这件物料的两行。
- 判断按列：页面读每格的字段名（列表格子的 `data-fieldname`）、导出按表头的列位置，不在整行文字里找字
  （11-02：仓库「คลัง 1」碰上物料名随机后缀以 1 开头，按行文字找仓库名会误判有仓库列）。物料名也不带「คลัง」这类仓库常用字。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import company as dataset_company  # noqa: E402

from desk_list import ACTION, CAPTURE_EXPORTS, IDLE, workbook_text  # noqa: E402

TITLE = "ERP 库存一览按物料：两仓合计、低库存按公司合计、点开看各仓"


def prepare(lab, manifest):
    name = "รวมยอด " + uuid.uuid4().hex[:6]
    return lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service as products
frappe.set_user("admin@erp.regression")
company = {dataset_company(manifest)!r}  # 数据清单里那家，不跟着别的流程切走的当前公司
frappe.set_user("stock@erp.regression")
companies.set_active_company(company)  # 页面以库存岗打开，看的是它当前的公司
frappe.set_user("admin@erp.regression")
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
from pearnly_erp_portal.inventory.api import EXPORT_COLUMNS
print(json.dumps({{"product": product, "name": {name!r}, "warehouses": [h.warehouse_name for h in houses],
                  "export_fields": [field for field, _key in EXPORT_COLUMNS]}}))
""")


# 读看得见的数据行：每行按格子的字段名（`data-fieldname`）取各格文字。手机宽度框架把多余的列收起（还在页面里，只是不显示），
# 文字照样读得到，所以两种宽度都按列判断
ROWS = ("const rows=()=>[...document.querySelectorAll('.result .list-row-container')]"
        ".filter(e=>e.offsetParent&&!e.querySelector('.list-row-head')).map(e=>{const cells={};"
        "for(const c of e.querySelectorAll('[data-fieldname]')){const f=c.getAttribute('data-fieldname');"
        "if(!(f in cells)) cells[f]=(c.textContent||'').replace(/\\s+/g,' ').trim();} return cells;});"
        "const wait_rows=async(n)=>{for(let k=0;k<80&&rows().length!==n;k++) await new Promise(r=>setTimeout(r,250)); await idle();};"
        # 页头的控件按 Frappe 页面对象取（`page.add_field` 记在 `fields_dict`），用控件自己的 set_value / get_value
        "const field=n=>frappe.container.page&&frappe.container.page.page&&frappe.container.page.page.fields_dict[n];")


def steps(params, camera_code=None):
    name = params["name"]
    return [
        {"name": "按位置", "js": IDLE + ROWS + CAPTURE_EXPORTS + "await idle();"
         "for(let k=0;k<80&&!field('search');k++) await new Promise(r=>setTimeout(r,250));"
         # 刚进页面（列表画好之后）：新会话不该带着上一格留下的搜索、筛选、看法（列表记忆按人存，见 `scripts/smoke/session.py`）
         "for(let k=0;k<80&&!document.querySelector('.result .list-row-container, .result .no-result');k++) await new Promise(r=>setTimeout(r,250));"
         "await idle(); const entry={search: field('search').get_value()||'', view: field('view').get_value(),"
         " filters: (document.querySelector('.filter-selector .filter-label')?.innerText||'').trim()};"
         f"await field('search').set_value({name!r}); await new Promise(r=>setTimeout(r,800)); await idle();"
         "await wait_rows(2); return {rows: rows(), entry};"},
        {"name": "按物料", "js": IDLE + ROWS + "await idle();"
         "const options=(field('view').df.options||[]).map(o=>o.label||o);"
         "await field('view').set_value('product'); await new Promise(r=>setTimeout(r,800)); await idle();"
         "await wait_rows(1); return {rows: rows(), options, view: field('view').get_value()};"},
        {"name": "导出按物料", "js": IDLE + ACTION + CAPTURE_EXPORTS + "await idle();"
         "const before=window.__exports.length; if(!await action('导出')) return {error:'no export button'};"
         "for(let k=0;k<80&&window.__exports.length===before;k++) await new Promise(r=>setTimeout(r,250));"
         "const got=window.__exports.length>before?window.__exports[window.__exports.length-1]:null;"
         "return got?{status:got.status,b64:got.b64}:{error:'no export'};"},
        {"name": "点开看各仓", "js": IDLE + ROWS + "await idle();"
         "const row=[...document.querySelectorAll('.result .list-row-container')].find(e=>e.offsetParent&&!e.querySelector('.list-row-head'));"
         "if(!row) return {error:'no product row'}; (row.querySelector('.list-subject a, .list-row-col a')||row.querySelector('.list-row')||row).click();"
         "for(let k=0;k<80&&field('view').get_value()!=='position';k++) await new Promise(r=>setTimeout(r,250));"
         "await new Promise(r=>setTimeout(r,800)); await idle(); await wait_rows(2);"
         "return {view: field('view').get_value(), rows: rows(), filters: (document.querySelector('.filter-selector .filter-label, .filter-button')?.innerText||'').trim()};"},
        # 去别页再点回来：还是刚才那样（按位置、只看这件物料的两行、搜索框还在）——列表记忆（用户 11-13「应该跳转到上次界面」）
        {"name": "离开再回来", "shot": True, "js": IDLE + ROWS + "await idle();"
         "frappe.set_route('pearnly-stock-card'); for(let k=0;k<80&&frappe.get_route()[0]!=='pearnly-stock-card';k++) await new Promise(r=>setTimeout(r,250));"
         "await new Promise(r=>setTimeout(r,800)); await idle();"
         "frappe.set_route('pearnly-inventory'); for(let k=0;k<80&&!(frappe.get_route()[0]==='pearnly-inventory'&&field('view'));k++) await new Promise(r=>setTimeout(r,250));"
         "await new Promise(r=>setTimeout(r,800)); await idle(); await wait_rows(2);"
         "return {view: field('view').get_value(), search: field('search').get_value()||'', rows: rows(),"
         " filters: (document.querySelector('.filter-selector .filter-label')?.innerText||'').trim()};"},
    ]


def _value(results, step):
    return next((r.get("value") for r in results if r.get("step") == step), None) or {}


def judge(params, results, camera_code=None):
    name = params["name"]
    position, product, export, opened, back = (_value(results, step)
                                               for step in ("按位置", "按物料", "导出按物料", "点开看各仓", "离开再回来"))
    places, merged = position.get("rows") or [], product.get("rows") or []
    entry = position.get("entry") or {}
    checks = [
        # 11-24：上一格点开物料留下的「物料 = …」被下一格取回，叠上新搜索成空表（手机那格在桌面那格之后跑）
        {"case": "a new session opens the list without the previous run's search, view or conditions", "seen": entry,
         "ok": entry == {"search": "", "view": "position", "filters": ""}},
        {"case": "by location shows the product in both warehouses", "seen": places,
         "ok": len(places) == 2 and all(name in row.get("product_name", "") for row in places)
         and sorted(row.get("warehouse_name") for row in places) == sorted(params["warehouses"])},
        {"case": "the view choice has by location and by product", "seen": product.get("options"),
         "ok": len(product.get("options") or []) == 2},
        {"case": "by product shows one row", "seen": merged,
         "ok": product.get("view") == "product" and len(merged) == 1 and name in merged[0].get("product_name", "")},
        # 按列：按物料那一行没有仓库、仓位两格（按位置那两行有，见上一条）
        {"case": "by product has no warehouse column", "seen": merged,
         "ok": bool(merged) and not {"warehouse_name", "location"} & set(merged[0])},
        # 库存一览删了状态、平均成本、金额、仓位四栏，编码栏是内部编码（岳父 11-18，用户 11-19）；低库存仍由服务端判
        # （见 `verify`：两行都是 low、按物料一行 low），页面上不再有状态那一格
        {"case": "the list has no status, cost, value or location column and shows the internal code", "seen": places,
         "ok": bool(places) and not {"status", "unit_cost", "value", "location"} & set(places[0])
         and "internal_code" in places[0]},
        {"case": "by product shows the total 4", "seen": merged,
         "ok": bool(merged) and merged[0].get("qty_on_hand") == "4"},
    ]
    checks.append({"case": "opening the product row goes back to its locations", "seen": opened,
                   "ok": opened.get("view") == "position" and len(opened.get("rows") or []) == 2})
    back_rows = back.get("rows") or []
    checks.append({"case": "leaving and coming back keeps the view, the search and the product's two rows", "seen": back,
                   "ok": back.get("view") == "position" and back.get("search") == name and back.get("filters") == "1"
                   and len(back_rows) == 2 and all(name in row.get("product_name", "") for row in back_rows)
                   and sorted(row.get("warehouse_name") for row in back_rows) == sorted(params["warehouses"])})
    # 导出按表头的列位置取格子（`inventory.api.EXPORT_COLUMNS`），不在整行文字里找
    try:
        import csv
        _sheets, text = workbook_text(export.get("b64") or "")
        fields = params["export_fields"]
        lines = [dict(zip(fields, cells)) for cells in list(csv.reader(text.splitlines()))[1:]]
        lines = [line for line in lines if name in line.get("product_name", "")]
    except Exception as exc:  # noqa: BLE001 — 下载不是文件也算不通过
        lines = [{"error": repr(exc)}]
    checks.append({"case": "the export follows the by product view", "seen": lines,
                   "ok": export.get("status") == 200 and len(lines) == 1 and _number(lines[0].get("qty_on_hand")) == 4
                   and not lines[0].get("warehouse_name")})
    return checks


def _number(value):
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def verify(lab, manifest, params, camera_code=None):
    """库存岗身份在服务端取两种看法：按位置两行都是低库存（各仓 3 / 1，公司合计 4 ≤ 5）、按物料一行合计 4、低库存。"""
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import stock
frappe.set_user("stock@erp.regression")
rows = stock.stock_overview(q={params['name']!r}, limit=0)
merged = stock.stock_overview(q={params['name']!r}, limit=0, view="product")
print(json.dumps({{"position": [(r["qty_on_hand"], r["status"]) for r in rows],
                  "product": [(r["qty_on_hand"], r["status"], r["warehouse"]) for r in merged]}}))
""")
    return [{"case": "by location: both rows are low stock by the company total", "saved": out["position"],
             "ok": sorted(out["position"]) == [[1.0, "low"], [3.0, "low"]]},
            {"case": "by product: one row of 4, low stock, no warehouse", "saved": out["product"],
             "ok": out["product"] == [[4.0, "low", None]]}]
