"""流程用例：ERP 库存卡（一件商品在一个仓的收发与结存，10-31 补）。

库存岗打开一件在某仓某库位有结存的商品的库存卡（`pearnly-stock-card/<商品>/<仓>/<库位>`；没有结存时 prepare 先入库，不靠别的流程留下的库存），同一次跳转里再连着触发两次页面显示（多仓 P2 查出的竞态：
过时的渲染后完成会再建一份表单）→ 页面上只有一份卡、期末结存与库里这件这个仓的账面一致。页面只读。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, js_value  # noqa: E402

TITLE = "ERP 库存卡：打开一件商品在一个仓的卡、期末结存对得上"


def prepare(lab, manifest):
    """数据清单里按编码那件、这家公司第一个启用的仓：取它结存最多的那个库位（卡按「商品 + 仓 + 库位」一格看）；
    一格都没有就先按正式入库记 5 件（仓设了库位清单时进第一个库位），有就不动、可重复跑，不靠别的流程先进货。"""
    co, product = company(manifest), codes(manifest)["by_code"]["product"]
    row = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies
co, product = {co!r}, {product!r}
warehouse = frappe.get_all("ERP Warehouse", filters={{"company": co, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
held = lambda: frappe.db.sql(\"\"\"select location, qty_on_hand from `tabERP Stock Balance` where product=%s and warehouse=%s and qty_on_hand > 0
  order by qty_on_hand desc, location asc limit 1\"\"\", (product, warehouse))
if not held():
    frappe.set_user("admin@erp.regression")
    companies.set_active_company(co)
    shelf = frappe.get_all("ERP Warehouse Location", filters={{"parent": warehouse, "parenttype": "ERP Warehouse"}}, pluck="location_name",
                           order_by="idx asc", limit=1)
    documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=warehouse, reason="flow stock card", submit=True,
                           lines=[{{"product": product, "qty": 5, "unit_cost": 10, "location": shelf[0] if shelf else ""}}])
    frappe.db.commit()
location, qty = held()[0]
print(json.dumps({{"product": product, "warehouse": warehouse, "location": location or "", "qty": float(qty)}}))
""")
    return {**row, "cases": []}


def steps(params, camera_code=None):
    product, warehouse, location = js_value(params["product"]), js_value(params["warehouse"]), js_value(params.get("location", ""))
    return [{"name": "打开库存卡", "js": JS + f"""
frappe.set_route('pearnly-stock-card', {product}, {warehouse}, {location});
const wrapper=await until(()=>document.querySelector('[data-page-route="pearnly-stock-card"]'));
if(!wrapper) return {{error:'page did not open'}};
wrapper.on_page_show(); await sleep(20); wrapper.on_page_show();
const page=await until(()=>pageOf('pearnly-stock-card'),20000);
await until(()=>page.querySelector('[data-fieldname="closing"]'),20000); await sleep(1500); await idle();
const cards=page.querySelectorAll('[data-fieldname="closing"]');
const el=cards[0]; const input=el&&el.querySelector('input');
const shown=((input&&input.value)||(el&&(el.querySelector('.control-value,.like-disabled-input')||{{}}).textContent)||'').trim();
return {{cards: cards.length, closing: shown}};
"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "打开库存卡"), {})
    shown = str(seen.get("closing") or "").replace(",", "").strip()
    try:
        closing = float(shown)
    except ValueError:
        closing = None
    return [{"case": "one card on the page after two renders", "seen": seen.get("cards"), "ok": seen.get("cards") == 1},
            {"case": "closing balance matches the books", "seen": [seen.get("closing"), params["qty"]], "ok": closing == params["qty"]}]


def verify(lab, manifest, params, camera_code=None):
    return []


if __name__ == "__main__":
    print(json.dumps(steps({"product": "PRD-1", "warehouse": "WH-1"})[0]["name"]))
