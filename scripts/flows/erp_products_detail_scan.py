"""流程用例：ERP 物料详情弹窗 —— 条码格旁的扫码图标扫码填条码、保存 → 库里这件的条码就是扫到的码。

产品地图 erp.scan.products.detail.barcode-scan（11-04 相机入口逐个成行后补的测试）。
本地物料（不是 Express 同步来的）的详情弹窗里，「条码 / 二维码」格右边有扫码图标（Express 的物料条码只认 Express，不出图标）。
每遍现建一件没条码的本地物料，从选择框「打开」那条路开它的详情弹窗（`frappe.pearnly.link_editors["ERP Product"]`）：
手输那遍把码填进那一格（同扫码枪打进去），手机宽度另用假摄像头点扫码图标；保存。每遍一个系统里没有的 EAN-13 码。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, js_value  # noqa: E402

TITLE = "ERP 物料详情：条码格扫码填码、保存"


def prepare(lab, manifest):
    first12 = "8857" + str(uuid.uuid4().int)[:8]
    # 系统里没有的码：合法的 EAN-13（带校验位），假摄像头画面才读得出
    code = first12 + str((10 - sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(first12)) % 10) % 10)
    sample = codes(manifest)["by_barcode"]["product"]
    made = lab.python(manifest["site"], f"""
from pearnly_erp_portal.products import service
frappe.set_user("admin@erp.regression")
unit = frappe.db.get_value("ERP Product", {sample!r}, "unit")
made = service.create_product(product_name="flow detail " + {code[-5:]!r}, unit=unit)
frappe.db.commit()
print(json.dumps({{"product": made["name"], "unit": unit}}))
""")
    return {**made, "code": code, "cases": [{"key": "by_barcode", "scan": code}]}


def steps(params, camera_code=None):
    fill = ("const btn=d.$wrapper.find('.pearnly-scan-icon')[0]; if(!btn) return {error:'no scan icon by the code field'}; btn.click();"
            f"await until(()=>d.get_value('code_value')==={js_value(params['code'])}, 25000);"
            if camera_code else
            f"await d.set_value('code_value', {js_value(params['code'])});")
    return [
        {"name": "扫码填码", "js": JS + f"""
frappe.set_route('pearnly-products'); await until(()=>pageOf('pearnly-products'), 20000); await idle();
const d=await frappe.pearnly.link_editors['ERP Product']({{name: {js_value(params['product'])}}}); if(!d) return {{error:'no detail dialog'}};
await until(()=>d.$wrapper.is(':visible'), 10000); await idle();
{fill}
await idle();
return {{code: d.get_value('code_value')||''}};"""},
        {"name": "保存", "js": JS + """
const d=window.cur_dialog; if(!d) return {error:'dialog lost'};
d.get_primary_btn().click();
const closed=await until(()=>!d.$wrapper.is(':visible'), 20000); await idle();
return {closed: !!closed, msg: msg()};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    filled, saved = by.get("扫码填码", {}), by.get("保存", {})
    return [{"case": "码进了条码格" + ("（相机扫码）" if camera_code else ""), "seen": filled, "ok": filled.get("code") == params["code"]},
            {"case": "保存后弹窗关上", "seen": saved, "ok": bool(saved.get("closed"))}]


def verify(lab, manifest, params, camera_code=None):
    row = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("ERP Product", {params['product']!r}, ["barcode", "qr_payload"], as_dict=True) or {{}}))
""")
    return [{"case": "库里这件的条码就是扫到的码", "seen": row, "ok": row.get("barcode") == params["code"]}]
