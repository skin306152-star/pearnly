"""流程用例：LINE 卡片「编辑」打开的采购页带上每行的选择（11-06 按行选物料与库存，评估 6.1）。

不跑识别（付费）、不经 LINE：实验室里以采购岗建一张「已识别」的 LINE 收单记录（照识别结果的形状：一行是已有物料的品名、
一行识别说是服务的运费、一行新品名），按收单前选「按票面」跑每行预选（`line_items.prepare`，同识别任务里那一步）；
单头仓定不下来时照 LINE 里选仓那一步写进去。打开 `/desk/pearnly-purchase/line/<记录>`：三行的「入库」勾选依次是 勾 / 不勾 / 勾，
第一行认出了已有物料；提交。库里：这张票已入账，三行类型依次 入库 / 只记账 / 入库，只有入库行有流水，记进明细物料记忆。
"""
from __future__ import annotations

import base64
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, warehouses  # noqa: E402

TITLE = "LINE 卡片打开的采购页：每行预选带到页面，提交后按行入库"
# 1×1 的 PNG：只当这张票的原件，不识别
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest):
    co, tag = company(manifest), uuid.uuid4().hex[:6]
    item = codes(manifest)["by_barcode"]
    warehouse = warehouses(manifest)[0]["name"]
    out = lab.python(manifest["site"], f"""
import base64, hashlib
from pearnly_erp_portal.integrations import intake, line_items
from pearnly_erp_portal.masters import company as companies
frappe.set_user("purchase@erp.regression")
companies.set_active_company({co!r})
express = frappe.db.get_value("ERP Company", {co!r}, "express_enabled")
frappe.db.set_value("ERP Company", {co!r}, "express_enabled", 0, update_modified=False)
name = frappe.db.get_value("ERP Product", {item['product']!r}, "product_name")
file = frappe.get_doc({{"doctype": "File", "file_name": "line-{tag}.png", "is_private": 1,
                        "content": base64.b64decode({base64.b64encode(PIXEL).decode()!r})}}).insert()
result = {{"doc_no": "LINE-{tag}", "doc_date": "2026-11-07", "doc_kind": "purchase_invoice", "has_vat": 1,
          "supplier_name": "flow line supplier {tag}",
          "lines": [{{"item_type": "goods", "description": name, "qty": 2, "unit_price": 10, "vat_rate": 7}},
                    {{"item_type": "service", "description": "ค่าขนส่ง {tag}", "qty": 1, "unit_price": 30, "vat_rate": 7}},
                    {{"item_type": "goods", "description": "ของใหม่ {tag}", "qty": 3, "unit_price": 5, "vat_rate": 7}}]}}
doc = frappe.get_doc({{"doctype": intake.DOCTYPE, "status": intake.READY, "direction": "purchase", "sent_by": frappe.session.user,
                       "line_user_id": "U-flow-{tag}", "language": "zh", "media_type": "image", "company": {co!r}, "party": "@doc",
                       "is_paid": 1, "payment_method": "cash", "source_file": file.file_url, "source_file_name": file.name,
                       "file_hash": hashlib.sha256(base64.b64decode({base64.b64encode(PIXEL).decode()!r})).hexdigest(),
                       "received_at": frappe.utils.now(), "result": json.dumps(result, ensure_ascii=False)}}).insert(ignore_permissions=True)
value = line_items.prepare(doc, result)
preset = [dict(row) for row in value["lines"]]
if not value.get("warehouse"):
    value["warehouse"] = {warehouse!r}  # 同 LINE 里选仓那一步
    frappe.db.set_value(intake.DOCTYPE, doc.name, "line_choices", json.dumps(value))
frappe.db.commit()
print(json.dumps({{"intake": doc.name, "express": int(express or 0), "preset": preset, "warehouse": value["warehouse"]}}))
""")
    return {**out, "company": co, "product": item["product"], "tag": tag, "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "打开", "js": JS + CAPTURE + f"""
frappe.set_route('pearnly-purchase', 'line', {json.dumps(params['intake'])});
const on=f=>{{const w=f.wrapper&&(f.wrapper.jquery?f.wrapper[0]:f.wrapper); return !!w&&w.isConnected;}};
const lines=()=>window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.unit_price&&f.fields_dict.stock_in&&on(f));
await until(()=>lines().length===3, 25000); await idle(); await sleep(800);
const head=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.bulk_edit&&on(f)).pop();
return {{checks: lines().map(l=>l.get_value('stock_in')), products: lines().map(l=>!!l.get_value('product')),
         warehouse: head&&head.get_value('warehouse')}};"""},
        {"name": "提交", "js": JS + """
const btn=all('.page-actions .primary-action, .page-actions .btn-primary')[0]; if(!btn) return {error:'no submit button'};
btn.click(); await sleep(1500);
const yes=all('.modal.show .btn-primary').find(b=>/是|确定|提交|Yes|Submit/.test(b.textContent)); if (yes) yes.click();
const moved=await until(()=>frappe.get_route()[1] && frappe.get_route()[1]!=='line', 25000); await idle();
return {moved: !!moved, route: frappe.get_route(), msg: msg()};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opened, sent = by.get("打开", {}), by.get("提交", {})
    return [{"case": "the server preselected matched / recognised service / new", "seen": params["preset"],
             "ok": [(row["item_type"], bool(row["product"]), row["decided"]) for row in params["preset"]]
             == [("goods", True, True), ("service", False, True), ("goods", False, False)]},
            {"case": "the page carries each line's choice and the warehouse", "seen": opened,
             "ok": opened.get("checks") == [1, 0, 1] and opened.get("products") == [True, False, False]
             and opened.get("warehouse") == params["warehouse"]},
            {"case": "submitted", "seen": sent, "ok": bool(sent.get("moved"))}]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP LINE Intake", {params['intake']!r}, ["status", "purchase"], as_dict=True)
lines = frappe.get_all("Purchase Line", filters={{"parent": row.purchase, "parenttype": "Purchase Document"}},
                       fields=["item_type", "product"], order_by="idx asc") if row.purchase else []
moved = frappe.get_all("ERP Stock Movement", filters={{"ref_id": row.purchase}}, pluck="product") if row.purchase else []
remembered = frappe.db.count("ERP Line Item Memory", {{"last_document": row.purchase}}) if row.purchase else 0
frappe.db.set_value("ERP Company", {params['company']!r}, "express_enabled", {params['express']}, update_modified=False)  # 还原
frappe.db.commit()
print(json.dumps({{"status": row.status, "lines": [dict(r) for r in lines], "moved": sorted(set(moved)), "remembered": remembered}}))
""")
    kinds = [row["item_type"] for row in got["lines"]]
    stocked = sorted({row["product"] for row in got["lines"] if row["item_type"] == "goods"})
    return [{"case": "the LINE document is recorded with in / record only / in", "seen": got,
             "ok": got["status"] == "confirmed" and kinds == ["goods", "service", "goods"]
             and got["lines"][0]["product"] == params["product"] and not got["lines"][1]["product"]},
            {"case": "only the stock lines moved stock", "seen": got["moved"], "ok": got["moved"] == stocked},
            {"case": "the lines are remembered", "seen": got["remembered"], "ok": got["remembered"] == 3}]
