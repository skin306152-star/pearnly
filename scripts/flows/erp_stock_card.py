"""流程用例：ERP 库存卡（一件商品在一个仓的收发与结存，10-31 补）。

库存岗打开一件在某仓有结存的商品的库存卡（`pearnly-stock-card/<商品>/<仓>`），同一次跳转里再连着触发两次页面显示（多仓 P2 查出的竞态：
过时的渲染后完成会再建一份表单）→ 页面上只有一份卡、期末结存与库里这件这个仓的账面一致。只读，不改数据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, company, js_value  # noqa: E402

TITLE = "ERP 库存卡：打开一件商品在一个仓的卡、期末结存对得上"


def prepare(lab, manifest):
    co = company(manifest)
    row = lab.python(manifest["site"], f"""
row = frappe.db.sql(\"\"\"select b.product, b.warehouse, sum(b.qty_on_hand) qty from `tabERP Stock Balance` b
  join `tabERP Warehouse` w on w.name = b.warehouse where w.company = %s and w.is_active = 1
  group by b.product, b.warehouse having sum(b.qty_on_hand) > 0 order by b.product, b.warehouse limit 1\"\"\", ({co!r},), as_dict=True)[0]
print(json.dumps({{"product": row.product, "warehouse": row.warehouse, "qty": float(row.qty)}}))
""")
    return {**row, "cases": []}


def steps(params, camera_code=None):
    product, warehouse = js_value(params["product"]), js_value(params["warehouse"])
    return [{"name": "打开库存卡", "js": JS + f"""
frappe.set_route('pearnly-stock-card', {product}, {warehouse});
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
