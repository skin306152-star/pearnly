"""流程用例：Cowork 推往多仓 Express 账套的票选仓库（11-08，评估 docs/work/2026-11-07-cowork-express-warehouse-评估.md）。

造数据（产品自己的函数，`cowork_operator_lib.py` 那一套，换成本流程自己的连接名、账套键与公司税号，免得与代录流程并行时互删）：
一条 Express 连接、一份账套，账套资料里有 CC 桌面版 1.12.18 报来的仓库表（01 主仓、02 模板自带没动过、30 分店在用）与一件存货；
两张进项票，各一行库存行（票面品名 = Express 存货名，不问存货对照）。

页面（复核页，电脑与手机）：明细行出「仓库」；「批量编辑」里有「仓库」一栏、只列在用的 01 / 30；全部设成 30、保存。
库里：A 票这一行记着 30；推送准备不再问仓、载荷库存行带 ``loccod = 30``（载荷版本 ≥ 4），这一行的仓按存货对照的键记进账套记忆；
没在行上选过的 B 票：A 推之前「要选的项」问一次默认仓、预选主仓 01；A 推过之后同一样东西照这一行记住的仓、不再问。跑完清掉造的数据。不连 Express、不发 LINE、不调模型。
"""
from __future__ import annotations

import json
import uuid

from cowork_operator_lib import BASE, CLEANUP, LABEL, TAX

TITLE = ("Cowork 复核页多仓账套选仓库：明细行与批量编辑出仓库、只列在用的仓，保存后推送载荷库存行带仓码、行上选的仓记住；"
         "没选过的问一次默认仓（预选主仓）")
MY_LABEL = "Flow warehouse Express"
MY_TAX = "0994000777010"
SELLER_TAX = "0107561000013"
ITEM = "Ice flow"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def _mine(snippet: str) -> str:
    """代录流程的造数据片段换成本流程自己的连接名、账套键、公司税号。"""
    return (snippet.replace(LABEL, MY_LABEL).replace("FLOWOP", "FLOWWH").replace("flowop", "flowwh")
            .replace(TAX, MY_TAX))


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6].upper()
    out = lab.python(manifest["site"], _mine(CLEANUP + BASE) + f"""
pool.put_facts(frappe.get_doc("Cowork ERP Connection", conn["name"]), "flowwh", {{
    "reported_warehouses": [{{"code": "01", "name": "Main", "main": "1", "used": "1"}}, {{"code": "02", "name": "Spare"}},
                            {{"code": "30", "name": "Branch", "used": "1"}}],
    "reported_products": [{{"code": "ICE01", "name": {ITEM!r}, "kind": "stock"}}]}},
    device="flow", routine=False, now=frappe.utils.now_datetime())
def stock_doc(number):
    fields = {{"direction": "purchase", "seller_name": "Flow seller", "seller_tax": {SELLER_TAX!r}, "buyer_tax": {MY_TAX!r},
              "buyer_name": "Flow Operator Co", "subtotal": "100", "vat": "7", "total_amount": "107", "date": period + "-15",
              "document_type": "tax_invoice", "invoice_number": number, "posting_kind": "stock",
              "items": [{{"name": {ITEM!r}, "qty": "1", "price": "100", "subtotal": "100", "posting_kind": "stock"}}]}}
    pages = [{{"page_index": 1, "fields": fields}}]
    made = storage.create_recognized(ws, pages, uuid4().hex, source="manual", confidence="high", ai_raw=pages)
    storage.set_push_targets([made["name"]], conn["name"], "FLOWWH", "express")
    application.save_choices(made["name"], conn["name"], "FLOWWH", json.dumps([
        {{"kind": "payment", "subject": "purchase|{SELLER_TAX}", "value": "credit", "label": "Flow seller"}}]))
    frappe.db.commit()
    return made["name"]
print(json.dumps({{"record": stock_doc("FLOW-WH-A-{tag}"), "other": stock_doc("FLOW-WH-B-{tag}"), "connection": conn["name"]}}))
""")
    return {**out, "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "明细行出仓库", "js": IDLE + "await idle();"
         "let shown=null; for(let k=0;k<60&&!shown;k++){shown=[...document.querySelectorAll('.frappe-control[data-fieldname=\"location\"]')]"
         ".find(e=>e.offsetParent); if(!shown) await new Promise(r=>setTimeout(r,250));}"
         "return {shown: Boolean(shown)};"},
        {"name": "批量编辑全部设成 30 并保存", "js": IDLE + "await idle(); await new Promise(r=>setTimeout(r,400));"
         "const b=[...document.querySelectorAll('.frappe-control[data-fieldname=\"bulk_kind\"] button')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no bulk edit button'}; b.click(); await new Promise(r=>setTimeout(r,800)); await idle();"
         # 产品皮肤把下拉换成了可搜索的组合框：经原生弹窗的 set_value 选值，onchange 照常触发（同 cowork_intake_bulk_kind）
         "const dlg=(frappe.ui.open_dialogs||[]).slice(-1)[0]; if(!dlg||!dlg.fields_dict.all_location) return {error:'no warehouse field'};"
         "const options=(dlg.fields_dict.all_location.df.options||[]).map(o=>o.value);"
         "await dlg.set_value('all_location','30'); await new Promise(r=>setTimeout(r,600));"
         "const rows=(dlg.fields_dict.rows.df.data||[]).map(r=>r.location);"
         "const ok=dlg.$wrapper.find('.modal-footer .btn-primary:visible')[0]; if(!ok) return {error:'no primary', options, rows}; ok.click();"
         "await new Promise(r=>setTimeout(r,900)); await idle();"
         "const save=[...document.querySelectorAll('.page-head .btn-secondary, .page-head .secondary-action')].find(e=>e.offsetParent);"
         "if(!save) return {error:'no save button', options, rows}; save.click(); await new Promise(r=>setTimeout(r,900)); await idle();"
         "return {options, rows, saved:true};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    line, bulk = by.get("明细行出仓库") or {}, by.get("批量编辑全部设成 30 并保存") or {}
    return [
        {"case": "the line shows a warehouse select for a multi-warehouse book", "seen": line, "ok": line.get("shown") is True},
        {"case": "bulk edit lists only the warehouses in use and sets every line", "seen": bulk,
         "ok": bulk.get("options") == ["", "01", "30"] and bulk.get("rows") == ["30"] and bulk.get("saved") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], _mine(CLEANUP) + f"""
from uuid import uuid4
from pearnly_core.express_rules.push import choices as rules
from pearnly_cowork.connections import service as connections
from pearnly_cowork.history import storage
from pearnly_cowork.push.application import service as application
frappe.set_user("admin@cowork.regression")
frappe.conf.pearnly_cowork_erp_push_enabled = True
conn, record, other = {params['connection']!r}, {params['record']!r}, {params['other']!r}
fields = ((storage.project(storage.read(record)).get("pages") or [{{}}])[0] or {{}}).get("fields") or {{}}
out = {{"line": [row.get("location") for row in fields.get("items") or []],
       "listed": [row["code"] for row in connections.warehouses(conn, "FLOWWH")["warehouses"]],
       "asked": [item["kind"] for item in application.choices_view(record, conn, "FLOWWH")["needed"]]}}
# B 票先看（A 推出去后这一行的仓已记住，同一家同一样东西 B 就照记忆、不再问）
need = [item for item in application.choices_view(other, conn, "FLOWWH")["needed"] if item["kind"] == "location"]
out["other"] = [{{"subject": item["subject"], "current": item["current"], "options": [o["code"] for o in item["options"]]}}
                for item in need]
doc = storage.read(record)
prepared = application.prepare(record, conn, uuid4().hex, doc.record_version, connections.detail(conn)["revision"], "FLOWWH")
row = frappe.get_doc("Cowork Push Record", prepared["name"]) if prepared.get("name") else None
payload = json.loads(row.payload_json or "{{}}") if row else {{}}
out["prepared"] = {{"status": prepared.get("status"), "reason": prepared.get("reason") or ""}}
out["loccod"] = [item.get("loccod") for item in payload.get("items") or []]
out["version"] = int(payload.get("payload_version") or 0)
out["remembered"] = [dict(r) for r in frappe.get_all("Cowork Express Choice", filters={{"connection": conn, "kind": "location"}},
                                                    fields=["subject", "value"], limit_page_length=5)]
# 推过之后 B 票同一样东西照这一行的记忆（30），不再问仓
out["after"] = [item["kind"] for item in application.choices_view(other, conn, "FLOWWH")["needed"]]
frappe.db.rollback()
out["subject"] = rules.item_subject("purchase", {SELLER_TAX!r}, "Flow seller", {ITEM!r})
out["gone"] = cleanup()
out["left"] = leftovers()
print(json.dumps(out, default=str))
""")
    return [
        {"case": "the record keeps the line's warehouse", "saved": rows["line"], "ok": rows["line"] == ["30"]},
        {"case": "the warehouses API lists only the ones in use", "saved": rows["listed"], "ok": rows["listed"] == ["01", "30"]},
        {"case": "a picked line is not asked again and the payload stock line carries loccod 30 at version 4 or later",
         "saved": {k: rows[k] for k in ("asked", "prepared", "loccod", "version")},
         "ok": "location" not in rows["asked"] and rows["prepared"]["status"] == "prepared" and rows["loccod"] == ["30"]
         and rows["version"] >= 4},
        {"case": "the line's warehouse is remembered under the item key", "saved": rows["remembered"],
         "ok": rows["remembered"] == [{"subject": rows["subject"], "value": "30"}]},
        {"case": "a record without a picked warehouse asks the default once, preselecting the main one", "saved": rows["other"],
         "ok": rows["other"] == [{"subject": "*", "current": "01", "options": ["01", "30"]}]},
        {"case": "after the line's warehouse is remembered the same item on another record is not asked", "saved": rows["after"],
         "ok": "location" not in rows["after"]},
        {"case": "the made connection, company and documents are cleaned up", "saved": {"gone": rows["gone"], "left": rows["left"]},
         "ok": rows["gone"]["connections"] == 1 and not any(rows["left"].values())},
    ]
