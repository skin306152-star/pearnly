"""流程用例：ERP 物料新建整页（表格）—— 扫码新加一行、填品名单位、保存 → 库里有这个条码的新物料。

产品地图 erp.scan.products.new-page.scan（11-04 相机入口逐个成行后补的测试）。
`/desk/pearnly-products/new` 是一张可编辑的表：表上方的扫码按钮（Grid 的 `.grid-custom-buttons` 插槽）扫一次 = 新加一行、码填进「条码 / 二维码」格。
手输那遍把码填进第一行（同扫码枪打进那一格）；手机宽度另用假摄像头点扫码按钮。每遍一个系统里没有的 EAN-13 码。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, js_value  # noqa: E402

TITLE = "ERP 物料新建整页：扫码新加一行、保存出新物料"
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest):
    first12 = "8858" + str(uuid.uuid4().int)[:8]
    # 系统里没有的码：合法的 EAN-13（带校验位），假摄像头画面才读得出
    code = first12 + str((10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12)) % 10) % 10)
    sample = codes(manifest)["by_barcode"]["product"]
    unit = lab.python(manifest["site"], f"print(json.dumps({{'unit': frappe.db.get_value('ERP Product', {sample!r}, 'unit')}}))")["unit"]
    return {"code": code, "name": "flow scan " + code[-5:], "unit": unit, "cases": [{"key": "by_barcode", "scan": code}]}


def steps(params, camera_code=None):
    add = ("const btn=grid.wrapper.find('.pearnly-scan-icon')[0]; if(!btn) return {error:'no scan button above the table'}; btn.click();"
           f"await until(()=>grid.df.data.some(r=>r.code_value==={js_value(params['code'])}), 25000);"
           if camera_code else
           f"grid.df.data[0].code_value={js_value(params['code'])}; grid.refresh();")
    return [
        {"name": "扫码加行", "js": JS + CAPTURE + f"""
frappe.set_route('pearnly-products', 'new');
const fg=await until(()=>(window.__fgs||[]).filter(f=>f.fields_dict&&f.fields_dict.products).pop(), 20000); if(!fg) return {{error:'no table'}};
await idle();
const grid=fg.fields_dict.products.grid;
{add}
await idle();
const row=grid.df.data.find(r=>r.code_value==={js_value(params['code'])}); if(!row) return {{error:'scanned code not in a row'}};
Object.assign(row, {{product_name: {js_value(params['name'])}, unit: {js_value(params['unit'])}}}); grid.refresh(); await idle();
return {{rows: grid.df.data.length, code: row.code_value, kind: row.__scanned||''}};"""},
        {"name": "保存", "js": JS + """
const page=pageOf('pearnly-products'); if(!page) return {error:'page lost'};
const btn=all('.page-actions .primary-action, .page-actions .btn-primary', page.closest('.page-container')||document)[0];
if(!btn) return {error:'no save button'}; btn.click();
const left=await until(()=>frappe.get_route().join('/')!=='pearnly-products/new', 25000); await idle();
return {route: frappe.get_route().join('/'), left: !!left, msg: msg(), alerts: alerts()};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    added, saved = by.get("扫码加行", {}), by.get("保存", {})
    return [{"case": "码进了表里那一行" + ("（相机扫码新加一行）" if camera_code else ""), "seen": added,
             "ok": added.get("code") == params["code"] and (not camera_code or added.get("kind") == "barcode")},
            {"case": "保存后离开新建页", "seen": saved, "ok": bool(saved.get("left"))}]


def verify(lab, manifest, params, camera_code=None):
    row = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("ERP Product", {{"barcode": {params['code']!r}}}, ["name", "product_name", "unit"], as_dict=True) or {{}}))
""")
    return [{"case": "库里有这个条码的新物料", "seen": row,
             "ok": row.get("product_name") == params["name"] and row.get("unit") == params["unit"]}]
