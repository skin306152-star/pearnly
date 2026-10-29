"""流程用例：ERP 新建物料带各仓期初（多仓 P1）；接了 Express 的公司期初取自 Express，不收手工期初（3f492849）。

管理员在物料页点「新建」：
- 老数据（本地公司）：弹窗里有期初表，填这家公司两个仓各一行（5、3，单价 20）提交 → 商品建出，两个仓的账面就是期初数；
- 新开户（接了 Express）：弹窗里没有期初表（期初以 Express 为准），只核这一点、不建。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, js_value, stock_of, warehouses  # noqa: E402

TITLE = "ERP 新建物料带各仓期初（接 Express 的公司没有期初表）"


def prepare(lab, manifest):
    rows = warehouses(manifest)
    return {"linked": bool(manifest.get("express_linked")), "name": "ยอดยกมา " + uuid.uuid4().hex[:6],
            "openings": [{"warehouse": rows[0]["name"], "qty": 5}, {"warehouse": rows[1]["name"], "qty": 3}] if len(rows) > 1 else []}


def steps(params, camera_code=None):
    fill = "" if params["linked"] else f"""
await d.set_value('product_name', {js_value(params['name'])});
d.fields_dict.opening.df.data = {js_value(params['openings'])}.map((row, i) => ({{idx: i + 1, name: 'row' + i, unit_cost: 20, ...row}}));
d.fields_dict.opening.grid.refresh(); await sleep(500);
d.get_primary_btn().trigger('click'); await sleep(1500); await idle();
"""
    return [{"name": "新建物料弹窗", "js": JS + f"""
const page=await until(()=>pageOf('pearnly-products'), 20000);
const btn=await until(()=>all('.page-head .primary-action', page)[0], 15000); if(!btn) return {{error:'no new button'}};
btn.click();
const d=await until(()=>window.cur_dialog&&cur_dialog.display&&cur_dialog.fields_dict.product_name&&cur_dialog, 15000); if(!d) return {{error:'no dialog'}};
const has_opening=!!d.fields_dict.opening;  // 期初表在默认收起的分区里：看字段在不在，不看可见
if (!has_opening && !{js_value(params['linked'])}) return {{has_opening}};
{fill}
if (d.display) d.hide();
return {{has_opening, msg: msg()}};"""}]


def judge(params, results, camera_code=None):
    v = next((r.get("value") for r in results if r.get("step") == "新建物料弹窗"), None) or {}
    want = not params["linked"]
    return [{"case": "期初表" + ("有" if want else "没有"), "seen": v, "ok": v.get("has_opening") is want}]


def verify(lab, manifest, params, camera_code=None):
    if params["linked"]:
        n = lab.python(manifest["site"], f"print(json.dumps({{'n': frappe.db.count('ERP Product', {{'product_name': {params['name']!r}}})}}))")["n"]
        return [{"case": "没建商品", "seen": n, "ok": n == 0}]
    product = lab.python(manifest["site"], f"print(json.dumps({{'name': frappe.db.get_value('ERP Product', {{'product_name': {params['name']!r}}}, 'name')}}))")["name"]
    got = [stock_of(lab, manifest["site"], product, row["warehouse"]) if product else None for row in params["openings"]]
    return [{"case": "两个仓的账面就是期初数", "seen": {"product": product, "stock": got},
             "ok": bool(product) and got == [row["qty"] for row in params["openings"]]}]
