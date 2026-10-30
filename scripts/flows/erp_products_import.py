"""流程用例：ERP 物料「Excel 导入」——照正式模板填表、在页面上选文件上传、导入，落库与页面提示对得上（演示准备 10-30）。

准备阶段在站点里按正式模板（`products.service._template_headers`，表头跟站点语言走）生成一份 xlsx，写 3 行这一轮专用的商品，
另加一行编码与第一行重复的（应被逐行拒、不连累其他行）。页面上照人的点法：页头「Excel 导入」（手机宽度在页头「…」菜单里）
→ 对话框「选择文件」→ 原生上传组件里放进这份文件、点上传 → 回到对话框点「Excel 导入」。
断言页面提示导入 3 件、跳过 1 行；库里多出这 3 件，名称、编码、条码、单位、价格对，来由是 import；重复的那行没进库。
"""
from __future__ import annotations

import json
import uuid

from desk_list import ACTION, IDLE

TITLE = "ERP 物料 Excel 导入：模板填表、页面上传、逐行入库、重复编码逐行拒（演示准备）"
USER = "admin"  # 演示是老板账号：成本只有管理员与库存岗能设（员工导入时成本列按字段权限丢掉，口径同「员工看不到进货价」）


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    tag = uuid.uuid4().hex[:6].upper()
    rows = [{"product_name": f"สินค้านำเข้า {tag} {i}", "code": f"IMP-{tag}-{i}", "code_value": f"IMP{tag}{i:03d}",
             "unit": "ชิ้น", "unit_price": 100 + i, "default_cost": 60 + i} for i in (1, 2, 3)]
    duplicate = dict(rows[0], product_name=f"สินค้าซ้ำ {tag}")
    out = lab.python(manifest["site"], f"""
import base64, io
from openpyxl import Workbook
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.products import service, workbook
frappe.set_user("{USER}@erp.regression")
companies.set_active_company({company!r})
headers = service._template_headers()
fields = [field for field, _key in workbook.FIELD_LABELS]
book = Workbook()
sheet = book.active
sheet.append(headers)
for row in {json.dumps(rows + [duplicate], ensure_ascii=False)}:
    sheet.append([row.get(field, "") for field in fields])
data = io.BytesIO()
book.save(data)
print(json.dumps({{"xlsx": base64.b64encode(data.getvalue()).decode(), "before": frappe.db.count("ERP Product")}}))
""")
    return {"tag": tag, "rows": rows, "duplicate": duplicate, "xlsx": out["xlsx"], "before": out["before"], "cases": []}


def steps(params, camera_code=None):
    upload = (
        # 原生上传组件：把文件放进它的文件选择框（同人选了文件），再点它的上传
        "const bin=atob(" + json.dumps(params["xlsx"]) + "); const bytes=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) bytes[i]=bin.charCodeAt(i);"
        "const file=new File([bytes], 'products-" + params["tag"] + ".xlsx', {type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});"
        "for(let k=0;k<80&&!document.querySelector('.modal.show input[type=file]');k++) await new Promise(r=>setTimeout(r,250));"
        "const input=[...document.querySelectorAll('.modal.show input[type=file]')].pop(); if(!input) return {error:'no file input'};"
        "const dt=new DataTransfer(); dt.items.add(file); input.files=dt.files; input.dispatchEvent(new Event('change',{bubbles:true}));"
        "await new Promise(r=>setTimeout(r,800)); const up=cur_dialog; up.get_primary_btn().trigger('click');"
        "for(let k=0;k<80&&cur_dialog===up;k++) await new Promise(r=>setTimeout(r,250)); await idle();")
    return [
        {"name": "打开 Excel 导入", "js": IDLE + ACTION + "await idle(); const how=await action('Excel 导入'); "
         "for(let k=0;k<40&&!(window.cur_dialog&&cur_dialog.fields_dict.pick);k++) await new Promise(r=>setTimeout(r,250)); "
         "return {how, dialog: !!(window.cur_dialog&&cur_dialog.fields_dict.pick)};"},
        {"name": "选文件上传", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.pick) return {error:'no import dialog'}; "
         "d.fields_dict.pick.$input.trigger('click'); " + upload +
         "return {file_name: d.get_value('file_name')||''};"},
        {"name": "导入", "js": IDLE + "const d=cur_dialog; if(!d||!d.fields_dict.pick) return {error:'no import dialog'}; "
         "d.get_primary_btn().trigger('click'); await new Promise(r=>setTimeout(r,500)); await idle(); await new Promise(r=>setTimeout(r,800)); "
         "const alerts=[...document.querySelectorAll('.alert-message, .desk-alert, .msgprint, .modal.show .modal-body')].map(e=>e.innerText.trim()).filter(Boolean); "
         "return {alerts, closed: !d.$wrapper.is(':visible')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, uploaded, imported = (by.get(k) or {} for k in ("打开 Excel 导入", "选文件上传", "导入"))
    text = " ".join(imported.get("alerts") or [])
    return [
        {"case": "页头（手机在「…」菜单）打开 Excel 导入", "seen": opened, "ok": bool(opened.get("dialog")) and opened.get("how") in ("button", "menu")},
        {"case": "原生上传组件传上文件", "seen": uploaded, "ok": str(uploaded.get("file_name", "")).endswith(".xlsx")},
        {"case": "提示导入 3 件、重复的那行逐行报出", "seen": imported,
         "ok": bool(imported.get("closed")) and "3" in text and params["duplicate"]["product_name"] not in text and "1" in text},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Product", filters={{"code": ["like", "IMP-{params['tag']}-%"]}},
                      fields=["product_name", "code", "barcode", "qr_payload", "unit", "unit_price", "default_cost", "origin"], order_by="code asc")
dup = frappe.db.count("ERP Product", {{"product_name": {params['duplicate']['product_name']!r}}})
print(json.dumps({{"rows": [dict(r, unit_price=float(r.unit_price or 0), default_cost=float(r.default_cost or 0)) for r in rows], "dup": dup}}, default=str))
""")
    rows = got["rows"]
    want = params["rows"]
    same = len(rows) == 3 and all(
        r["product_name"] == w["product_name"] and r["code"] == w["code"] and w["code_value"] in (r.get("barcode"), r.get("qr_payload"))
        and r["unit"] == w["unit"] and r["unit_price"] == float(w["unit_price"]) and r["default_cost"] == float(w["default_cost"])
        and r["origin"] == "import" for r, w in zip(rows, want))
    return [
        {"case": "3 件入库：名称、编码、条码、单位、价格对，来由 import", "saved": rows, "ok": same},
        {"case": "编码重复的那行没进库", "saved": got["dup"], "ok": got["dup"] == 0},
    ]
