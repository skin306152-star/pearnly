"""流程用例：ERP 新建采购单（盘点菜单做完整 S1：明细不再有批号 / 失效日期；按码认商品走单据物料选择）。
手机宽度另用假摄像头跑一遍：明细物料格不输条码，点格子右边的扫码按钮，由相机读出条码认商品（产品地图 erp.scan.purchase.item-scan，11-04）。

打开采购新建页、切到「手动录入」：明细里没有批号、失效日期两格；单头填供应商名、单头仓、付清（现金），明细物料格输条码回车（单据物料选择按码认出商品）、
数量 2、单价 10，提交。库里：采购单已提交、明细是这件商品、批号 / 失效日期为空，单头仓 +2。
新开户那家公司接了 Express：这条流程只看采购本身，跑的时候先关掉「接 Express」（不走写账预检），跑完还原。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import codes, ledger_case  # noqa: E402

TITLE = "ERP 采购：新建采购单（无批号 / 失效日期）入库"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    # 带条码的那件（两套数据同一个取法，假摄像头读的就是它的条码）
    item = codes(manifest)["by_barcode"]
    product = item["product"]
    # 这一遍关掉「接 Express」，单子不排进账本；格结束时 run_flow 还原（`Lab.set_fields`）
    lab.set_fields(manifest["site"], "ERP Company", company, {"express_enabled": 0})
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
frappe.set_user("purchase@erp.regression")
companies.set_active_company({company!r})
p = frappe.db.get_value("ERP Product", {product!r}, ["name", "product_name", "barcode", "code"], as_dict=True)
wh = frappe.get_all("ERP Warehouse", filters={{"company": {company!r}, "is_active": 1}}, pluck="name", order_by="creation asc", limit=1)[0]
qty = frappe.db.get_value("ERP Stock Balance", {{"product": p.name, "warehouse": wh, "location": ""}}, "qty_on_hand") or 0
frappe.db.commit()
print(json.dumps({{"product": p.name, "product_name": p.product_name, "scan": p.barcode or p.code, "warehouse": wh, "before": float(qty)}}))
""")
    return {**out, "company": company, "supplier": "flow supplier " + uuid.uuid4().hex[:5],
            "cases": [{"key": "by_barcode", "scan": item["scan"]}]}


def steps(params, camera_code=None):
    sup, wh, product, name, scan = (json.dumps(params[k]) for k in ("supplier", "warehouse", "product", "product_name", "scan"))
    return [
        {"name": "打开新建页", "js": IDLE + CAPTURE +
         "frappe.set_route('pearnly-purchase'); await idle(); await new Promise(r=>setTimeout(r,800)); frappe.set_route('pearnly-purchase','new');"
         "for(let k=0;k<60&&!window.__fgs.find(f=>f.fields_dict&&f.fields_dict.unit_price);k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const line=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.unit_price).pop(); if(!line) return {error:'no line form'};"
         "return {line_fields: Object.keys(line.fields_dict)};"},
        {"name": "按条码认商品", "js": IDLE +
         f"const r=await frappe.call({{method:'pearnly_erp_portal.masters.selection.api_choices', args:{{kind:'product', context:'purchase', txt:{scan}, scan:1}}}});"
         "return {choices: JSON.stringify(r.message||[]).slice(0,400)};"},
        {"name": "填写提交", "js": IDLE +
         "const head=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.supplier_name&&f.fields_dict.doc_kind).pop();"
         "const line=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.unit_price).pop(); if(!head||!line) return {error:'no forms'};"
         # 新建采购默认「上传识别」，切到「手动录入」才能直接填写提交
         "const entry=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.entry_manual).pop(); if(entry){await entry.set_value('entry_manual',1); await idle();}"
         f"await head.set_values({{supplier_name:{sup}, doc_no:{sup}, doc_kind:'purchase_invoice', is_paid:1, payment_method:'cash'}});"
         f"if(head.fields_dict.warehouse) await head.set_value('warehouse', {wh});"
         + (
         # 假摄像头那一遍：点明细物料格右边的扫码按钮，相机读出条码 → 单据物料选择按码认出
         "const btn=line.fields_dict.entry_choice.$wrapper.find('.pearnly-link-scan')[0]; if(!btn) return {error:'no scan button on the item cell'}; btn.click();"
         if camera_code else
         # 明细物料格当扫码枪用：输条码回车 → 单据物料选择按码认出（`masters.selection.api_choices`）
         f"const inp=line.fields_dict.entry_choice.input; inp.focus(); inp.value={scan}; inp.dispatchEvent(new Event('input',{{bubbles:true}}));"
         "inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));") +
         f"for(let k=0;k<100&&line.get_value('product')!=={product};k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "if(!line.get_value('product')) return {error:'product not chosen by code'};"
         f"await line.set_values({{qty:2, unit_price:10, warehouse:{wh}}}); await idle();"
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no submit button'}; b.click();"
         "for(let k=0;k<80&&(frappe.get_route()[1]||'')==='new';k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "return {route: frappe.get_route().join('/')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    fields = (by.get("打开新建页") or {}).get("line_fields") or []
    return [
        {"case": "line has no batch or expiry", "seen": fields, "ok": "unit_price" in fields and not {"batch_no", "expiry_date"} & set(fields)},
        {"case": "chooser finds the product by barcode", "seen": by.get("按条码认商品"),
         "ok": params["product_name"] in ((by.get("按条码认商品") or {}).get("choices") or "")},
        {"case": "submitted and opened", "seen": by.get("填写提交"), "ok": not ((by.get("填写提交") or {}).get("route") or "").endswith("/new")},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
doc = frappe.db.get_value("Purchase Document", {{"supplier_name": {params['supplier']!r}}}, ["name", "status"], as_dict=True) or {{}}
lines = frappe.get_all("Purchase Line", filters={{"parent": doc.get("name")}}, fields=["product", "qty"]) if doc else []
# 批次 / 有效期 10-30 彻底删除：库里的列也没有了
gone = [c for c in ("batch_no", "expiry_date") if frappe.db.has_column("Purchase Line", c)]
# 这张单自己的库存流水（两遍各开一张单，账面先后都在变，按单核入库数）
moved = sum(r.qty_base or 0 for r in frappe.get_all("ERP Stock Movement", filters={{"ref_id": doc.get("name"), "product": {params['product']!r},
            "warehouse": {params['warehouse']!r}}}, fields=["qty_base"])) if doc else 0
print(json.dumps({{"doc": doc, "lines": [dict(r, qty=float(r.qty)) for r in lines], "moved": float(moved), "batch_columns": gone}}, default=str))
""")
    line = (got["lines"] or [{}])[0]
    return [
        {"case": "purchase submitted without batch or expiry", "saved": got,
         "ok": got["doc"].get("status") == "submitted" and line.get("product") == params["product"] and line.get("qty") == 2.0
         and got.get("batch_columns") == []},
        {"case": "stock went up by 2", "saved": got["moved"], "ok": got["moved"] == 2.0},
        # 账本栏是通用列；这一遍关了「接 Express」，单子不排进账本
        ledger_case(lab, manifest["site"], "Purchase Document", [got["doc"].get("name")], False),
    ]
