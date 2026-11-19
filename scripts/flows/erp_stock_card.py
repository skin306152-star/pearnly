"""流程用例：ERP 库存卡（一件商品在一个仓的收发与结存，10-31 补）。

库存岗打开一件在某仓有结存的商品的库存卡（`pearnly-stock-card/<商品>/<仓>`，11-19 起库存只按物料 + 仓库；没有结存时 prepare 先入库，
不靠别的流程留下的库存），同一次跳转里再连着触发两次页面显示（多仓 P2 查出的竞态：过时的渲染后完成会再建一份表单）→ 页面上只有一份卡、
没有仓位栏、期末结存与库里这件这个仓的账面一致；11-19 以前带第三段（仓位）的旧链接照样打开同一张卡。页面只读。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, js_value  # noqa: E402

TITLE = "ERP 库存卡：打开一件商品在一个仓的卡、期末结存对得上"


def prepare(lab, manifest):
    """数据清单里按编码那件、这家公司第一个启用的仓（卡按「商品 + 仓」一格看）：这个仓没有结存就先按正式入库记 5 件，
    有就不动、可重复跑，不靠别的流程先进货。"""
    co, product = company(manifest), codes(manifest)["by_code"]["product"]
    row = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents
from pearnly_erp_portal.masters import company as companies
co, product = {co!r}, {product!r}
warehouse = frappe.get_all("ERP Warehouse", filters={{"company": co, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
held = lambda: float(frappe.db.get_value("ERP Stock Balance", {{"product": product, "warehouse": warehouse}}, "qty_on_hand") or 0)
if held() <= 0:
    frappe.set_user("admin@erp.regression")
    companies.set_active_company(co)
    documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), reason="flow stock card", submit=True,
                           lines=[{{"product": product, "qty": 5, "unit_cost": 10, "warehouse": warehouse}}])
    frappe.db.commit()
print(json.dumps({{"product": product, "warehouse": warehouse, "qty": held()}}))
""")
    return {**row, "cases": []}


def steps(params, camera_code=None):
    product, warehouse = js_value(params["product"]), js_value(params["warehouse"])
    closing = ("const closingOf=page=>{const el=page.querySelector('[data-fieldname=\"closing\"]'); const input=el&&el.querySelector('input');"
               "return ((input&&input.value)||(el&&(el.querySelector('.control-value,.like-disabled-input')||{}).textContent)||'').trim();};")
    return [{"name": "打开库存卡", "js": JS + closing + f"""
frappe.set_route('pearnly-stock-card', {product}, {warehouse});
const wrapper=await until(()=>document.querySelector('[data-page-route="pearnly-stock-card"]'));
if(!wrapper) return {{error:'page did not open'}};
wrapper.on_page_show(); await sleep(20); wrapper.on_page_show();
const page=await until(()=>pageOf('pearnly-stock-card'),20000);
await until(()=>page.querySelector('[data-fieldname="closing"]'),20000); await sleep(1500); await idle();
const cards=page.querySelectorAll('[data-fieldname="closing"]');
return {{cards: cards.length, closing: closingOf(page), location: page.querySelectorAll('[data-fieldname="location"]').length,
         warehouse: page.querySelectorAll('[data-fieldname="warehouse"]').length}};
"""},
            # 11-19 以前的链接多一段仓位：照样打开这件这个仓的卡（第三段不看）
            {"name": "旧链接带仓位", "js": JS + closing + f"""
frappe.set_route('pearnly-stock-card', {product}, {warehouse}, 'OLD-SHELF');
await until(()=>frappe.get_route().length===4, 10000); await sleep(1500); await idle();
const page=await until(()=>pageOf('pearnly-stock-card')&&pageOf('pearnly-stock-card').querySelector('[data-fieldname="closing"]')&&pageOf('pearnly-stock-card'),20000);
if(!page) return {{error:'page did not open'}};
return {{cards: page.querySelectorAll('[data-fieldname="closing"]').length, closing: closingOf(page), route: frappe.get_route().length}};
"""}]


def _number(text):
    try:
        return float(str(text or "").replace(",", "").strip())
    except ValueError:
        return None


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "打开库存卡"), {})
    old = next((r.get("value") or {} for r in results if r.get("step") == "旧链接带仓位"), {})
    return [{"case": "one card on the page after two renders", "seen": seen.get("cards"), "ok": seen.get("cards") == 1},
            {"case": "closing balance matches the books", "seen": [seen.get("closing"), params["qty"]], "ok": _number(seen.get("closing")) == params["qty"]},
            # 库存卡只到物料 + 仓库（11-19）：有仓库一格，没有仓位一格
            {"case": "the card shows the warehouse and no location", "seen": seen,
             "ok": (seen.get("warehouse") or 0) >= 1 and seen.get("location") == 0},
            {"case": "an old link with a location segment opens the same card", "seen": old,
             "ok": old.get("route") == 4 and old.get("cards") == 1 and _number(old.get("closing")) == params["qty"]}]


def verify(lab, manifest, params, camera_code=None):
    return []


if __name__ == "__main__":
    print(json.dumps(steps({"product": "PRD-1", "warehouse": "WH-1"})[0]["name"]))
