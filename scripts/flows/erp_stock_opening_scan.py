"""流程用例：ERP 录入期初 —— 明细商品格按码认商品（手机宽度另用假摄像头点商品格的扫码按钮）→ 预览 → 提交 → 期初入账。

产品地图 erp.scan.inventory.opening.item-scan（11-04 相机入口逐个成行后补的测试）。
库存岗：库存 → 录入期初（`/desk/pearnly-inventory/opening`）；表头选这一遍现建的空仓、日期今天；明细第一行商品格输条码选中那件
（相机那遍点格子右边的扫码按钮，由假摄像头读条码）→ 数量 5、成本 10 → 预览 → 提交。库里：这个仓这件的账面 +5。
只跑老数据：接了 Express 的公司库存来自 Express，没有「录入期初」（inventory.js 的 can_open），新开户那套接了 Express。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, js_value, stock_of  # noqa: E402

TITLE = "ERP 录入期初：明细商品格按码认商品、预览、提交入账"
# 记下页面建的 FieldGroup：明细行的值从表单对象读（只读格不是输入框）
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest):
    """每遍现建一个空仓（手机宽度两遍在开跑前各准备一次，各用各的仓）。"""
    item = codes(manifest)["by_barcode"]
    warehouse = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import warehouses
frappe.set_user("admin@erp.regression")
made = warehouses.create_warehouse(warehouse_name="คลังเปิดโฟลว์ " + frappe.generate_hash(length=6), company={company(manifest)!r})
frappe.db.commit()
print(json.dumps({{"name": made["name"]}}))
""")["name"]
    made = lab.python(manifest["site"], f"""
print(json.dumps({{"warehouse": {warehouse!r}, "name": frappe.db.get_value("ERP Product", {item['product']!r}, "product_name")}}))
""")
    return {**made, "product": item["product"], "scan": item["scan"],
            "before": stock_of(lab, manifest["site"], item["product"], made["warehouse"]),
            "cases": [{"key": "by_barcode", "scan": item["scan"]}]}


def steps(params, camera_code=None):
    find = ("const btn=row.querySelector('[data-fieldname=\"product\"] .pearnly-link-scan'); if(!btn) return {error:'no scan button on the product cell'};"
            " btn.click();"
            if camera_code else
            f"if(!await pick(row.querySelector('[data-fieldname=\"product\"] input'), {js_value(params['scan'])}, {js_value(params['name'])}))"
            " return {error:'no match in the product picker'};")
    return [
        {"name": "填表头", "js": JS + CAPTURE + f"""
frappe.set_route('pearnly-inventory', 'opening');
const page=await until(()=>pageOf('pearnly-inventory'), 20000); if(!page) return {{error:'page did not open'}};
const head=await until(()=>page.querySelector('[data-fieldname="warehouse"] input'), 15000); if(!head) return {{error:'no warehouse field'}};
// 表头仓按本人上次用的仓异步预填：等页面请求都回来再设，不然会被盖回去
await idle(); await sleep(800); await idle();
setInput(head, {js_value(params['warehouse'])}); await sleep(1200); await idle();
return {{warehouse: head.value}};"""},
        {"name": "明细", "js": JS + f"""
const page=pageOf('pearnly-inventory'); if(!page) return {{error:'page lost'}};
const row=await until(()=>page.querySelector('[data-fieldname="rows"] [data-fieldname="product"]')&&page.querySelector('[data-fieldname="rows"]'), 15000);
if(!row) return {{error:'no line'}};
{find}
const line=()=>(window.__fgs||[]).filter(f=>f.fields_dict&&f.fields_dict.unit_cost&&f.fields_dict.product).pop();
const unit=()=>line()&&line().get_value('unit');
await until(unit, 25000); await idle();
await line().set_value('qty', '5'); await sleep(300);
await line().set_value('unit_cost', '10'); await sleep(800); await idle();
return {{product: line()&&line().get_value('product'), unit: unit()||''}};"""},
        {"name": "预览提交", "js": JS + """
const page=pageOf('pearnly-inventory'); if(!page) return {error:'page lost'};
const primary=()=>all('.page-actions .primary-action, .page-actions .btn-primary', page.closest('.page-container')||document)[0];
let btn=primary(); if(!btn) return {error:'no preview button'}; const first=btn.textContent.trim(); btn.click();
await until(()=>primary()&&primary().textContent.trim()!==first, 20000); await idle();
const status=()=>(window.__fgs||[]).filter(f=>f.fields_dict&&f.fields_dict.status_text).map(f=>f.get_value('status_text')).filter(Boolean).join(' | ');
if(primary()&&primary().textContent.trim()===first) return {error:'preview did not pass: '+status()+' '+msg()};
btn=primary(); if(!btn) return {error:'no submit button'}; const second=btn.textContent.trim(); btn.click();
const back=await until(()=>frappe.get_route().join('/')==='pearnly-inventory', 25000); await idle();
return {preview: first, submit: second, back: !!back, msg: msg()};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    line, sent = by.get("明细", {}), by.get("预览提交", {})
    return [{"case": "按码认出商品、带出单位", "seen": line, "ok": bool(line.get("unit")) and bool(line.get("product"))},
            {"case": "预览后提交、回到库存页", "seen": sent, "ok": bool(sent.get("back")) and sent.get("preview") != sent.get("submit")}]


def verify(lab, manifest, params, camera_code=None):
    after = stock_of(lab, manifest["site"], params["product"], params["warehouse"])
    return [{"case": "期初入账：这个仓这件账面 +5", "seen": {"before": params["before"], "after": after},
             "ok": after == params["before"] + 5}]
