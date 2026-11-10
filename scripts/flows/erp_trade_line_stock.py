"""流程用例：ERP 采购单按行选入库（11-06 按行选物料与库存，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 6.2）。
销售单同一套步骤见 `erp_trade_line_stock_sales.py`。

手动录入一张三行的单：第一行按码认出已有物料、第二行打「运费」、第三行打一个新品名；每行都看得见「入库」（销售「出库」）勾选。
点明细下面的「批量编辑」：原生弹窗列出三行和单头仓库，「全部」选入库后把运费那行取消、保存，页面上运费那行的勾选跟着取消；提交。
库里：三行类型依次是入库 / 只记账 / 入库，只有第一、三行有库存流水（第三行随单新建了物料、记着是这张单建的），运费那行不建物料；
三行都记进明细物料记忆（同一家公司）。这家公司接了 Express 的，跑的时候先关掉「接 Express」（不走写账预检），跑完还原。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, js_value, warehouses  # noqa: E402

TITLE = "ERP 采购：明细按行勾入库、批量编辑设整张，只有入库行动库存"
DIRECTION = "purchase"
CONFIG = {"purchase": {"route": "pearnly-purchase", "user": "purchase@erp.regression", "check": "stock_in",
                       "doctype": "Purchase Document", "line": "Purchase Line", "head": "supplier_name"},
          "sales": {"route": "pearnly-sales", "user": "sales@erp.regression", "check": "stock_out",
                    "doctype": "ERP Sales Document", "line": "ERP Sales Document Line", "head": "buyer_name"}}
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest, direction=None):
    direction = direction or DIRECTION
    item = codes(manifest)["by_barcode"]
    co = company(manifest)
    # 这一格关掉「接 Express」，单子不排进账本；格结束时 run_flow 还原（`Lab.set_fields`，成功、失败、超时都还原）
    lab.set_fields(manifest["site"], "ERP Company", co, {"express_enabled": 0})
    out = lab.python(manifest["site"], f"""
print(json.dumps({{"name": frappe.db.get_value("ERP Product", {item['product']!r}, "product_name")}}))
""")
    tag = uuid.uuid4().hex[:6]
    return {"direction": direction, "company": co, "warehouse": warehouses(manifest)[0]["name"], "product": item["product"],
            "scan": item["scan"], "name": out["name"], "party": f"flow party {tag}", "freight": f"ค่าขนส่ง {tag}",
            "fresh": f"ของใหม่ {tag}", "cases": []}


def steps(params, camera_code=None):
    cfg = CONFIG[params["direction"]]
    check, route = js_value(cfg["check"]), js_value(cfg["route"])
    return [
        {"name": "三行明细", "js": JS + CAPTURE + f"""
frappe.set_route({route}, 'new');
const page=await until(()=>pageOf({route}), 20000); if(!page) return {{error:'page did not open'}};
await until(()=>page.querySelector('[data-fieldname="doc_date"] input'));
const manual=await until(()=>all('label, .checkbox, .pearnly-entry-choice, div', page).find(el=>el.children.length<6&&/^手动录入/.test(el.textContent.trim())), 8000);
if (manual) {{ (manual.querySelector('input[type=checkbox], input[type=radio]')||manual).click(); await sleep(1500); await idle(); }}
const head=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.add_line&&f.fields_dict.bulk_edit).pop(); if(!head) return {{error:'no head form'}};
const party=[...page.querySelectorAll('[data-fieldname="entry_choice"]')].find(el=>!el.closest('[data-fieldname="lines"]'));
setInput(party.querySelector('input, textarea'), {js_value(params['party'])}); await sleep(600); await idle();
await head.set_value('warehouse', {js_value(params['warehouse'])}); await idle();
if(head.fields_dict.doc_no) await head.set_value('doc_no', {js_value(params['party'])});
const on=f=>{{const w=f.wrapper&&(f.wrapper.jquery?f.wrapper[0]:f.wrapper); return !!w&&w.isConnected;}};
const lineForms=()=>window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.unit_price&&f.fields_dict[{check}]&&on(f));
const first=await until(()=>lineForms()[0], 10000); if(!first) return {{error:'no line form'}};
if(!await pick(first.fields_dict.entry_choice.input, {js_value(params['scan'])}, {js_value(params['name'])})) return {{error:'no match in the item picker'}};
await until(()=>first.get_value('product'), 20000); await idle();
for (const text of [{js_value(params['freight'])}, {js_value(params['fresh'])}]) {{
  head.fields_dict.add_line.$input.click(); await sleep(800); await idle();
  const line=lineForms().pop(); setInput(line.fields_dict.entry_choice.input, text); await sleep(600); await idle();
}}
const lines=lineForms(); if(lines.length!==3) return {{error:'lines '+lines.length}};
for (const line of lines) {{ await line.set_values({{qty: 2, unit_price: 10}}); }}
await sleep(600); await idle();
return {{checks: lines.map(l=>l.get_value({check})), products: lines.map(l=>!!l.get_value('product'))}};"""},
        # 打开弹窗、设好（全部入库、运费那行取消）后截图（设了 PEARNLY_FLOWS_SHOTS 时，`flows-shots/<流程>-<数据>-<宽度>/bulk-edit.png`）
        {"name": "打开批量编辑", "shot": True, "full": True, "file": "bulk-edit", "js": JS + f"""
const on=f=>{{const w=f.wrapper&&(f.wrapper.jquery?f.wrapper[0]:f.wrapper); return !!w&&w.isConnected;}};
const head=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.bulk_edit&&on(f)).pop();
head.fields_dict.bulk_edit.$input.click(); await sleep(800); await idle();
const dlg=await until(()=>(frappe.ui.open_dialogs||[]).slice(-1)[0]); if(!dlg||!dlg.fields_dict.rows) return {{error:'no dialog'}};
const listed=(dlg.fields_dict.rows.df.data||[]).map(r=>r.label); const warehouse=dlg.get_value('warehouse');
await dlg.set_value('all','goods'); await sleep(400);
const data=dlg.fields_dict.rows.df.data; data[1].stock=0; dlg.fields_dict.rows.grid.refresh(); await sleep(400);
return {{listed: listed.length, warehouse}};"""},
        {"name": "批量编辑", "js": JS + f"""
const on=f=>{{const w=f.wrapper&&(f.wrapper.jquery?f.wrapper[0]:f.wrapper); return !!w&&w.isConnected;}};
const dlg=(frappe.ui.open_dialogs||[]).slice(-1)[0]; if(!dlg||!dlg.fields_dict.rows) return {{error:'no dialog'}};
const ok=dlg.$wrapper.find('.modal-footer .btn-primary:visible')[0]; if(!ok) return {{error:'no primary'}}; ok.click();
await sleep(900); await idle();
const lines=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.unit_price&&f.fields_dict[{check}]&&on(f));
return {{checks: lines.map(l=>l.get_value({check}))}};"""},
        {"name": "提交", "js": JS + """
const btn=all('.page-actions .primary-action, .page-actions .btn-primary')[0]; if(!btn) return {error:'no submit button'};
btn.click(); await sleep(1500);
const yes=all('.modal.show .btn-primary').find(b=>/是|确定|提交|Yes|Submit/.test(b.textContent)); if (yes) yes.click();
const moved=await until(()=>frappe.get_route()[1] && frappe.get_route()[1]!=='new', 25000); await idle();
return {moved: !!moved, route: frappe.get_route(), msg: msg()};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    made, sent = by.get("三行明细", {}), by.get("提交", {})
    bulk = {**by.get("打开批量编辑", {}), **by.get("批量编辑", {})}
    params["doc"] = (sent.get("route") or [None, None])[1]
    return [{"case": "every line shows the stock check, ticked by default; the first line is the existing item", "seen": made,
             "ok": made.get("checks") == [1, 1, 1] and made.get("products") == [True, False, False]},
            {"case": "bulk edit lists the lines with the header warehouse and writes the checks back", "seen": bulk,
             "ok": bulk.get("listed") == 3 and bulk.get("warehouse") == params["warehouse"] and bulk.get("checks") == [1, 0, 1]},
            {"case": "submitted", "seen": sent, "ok": bool(sent.get("moved"))}]


def verify(lab, manifest, params, camera_code=None):
    cfg = CONFIG[params["direction"]]
    got = lab.python(manifest["site"], f"""
name = {params.get('doc')!r}
lines = frappe.get_all({cfg['line']!r}, filters={{"parent": name, "parenttype": {cfg['doctype']!r}}},
                       fields=["item_type", "product", "description"], order_by="idx asc") if name else []
moved = frappe.get_all("ERP Stock Movement", filters={{"ref_id": name}}, pluck="product") if name else []
fresh = next((row.product for row in lines if row.description == {params['fresh']!r}), None)
origin = frappe.db.get_value("ERP Product", fresh, ["origin_doctype", "origin_name"], as_dict=True) if fresh else None
remembered = frappe.db.count("ERP Line Item Memory", {{"company": {params['company']!r}, "last_document": name}}) if name else 0
print(json.dumps({{"lines": [dict(r) for r in lines], "moved": sorted(set(moved)), "fresh": fresh, "origin": origin,
                  "remembered": remembered}}, default=str))
""")
    kinds = [row["item_type"] for row in got["lines"]]
    products = [row["product"] for row in got["lines"]]
    return [{"case": "line kinds are in / record only / in", "seen": got["lines"], "ok": kinds == ["goods", "service", "goods"]},
            {"case": "the freight line has no item; the new line made one", "seen": products,
             "ok": products[:1] == [params["product"]] and products[1] is None and bool(products[2])},
            {"case": "only the stock lines moved stock", "seen": got["moved"],
             "ok": got["moved"] == sorted({params["product"], got["fresh"] or "-"})},
            {"case": "the new item records the document that created it", "seen": got["origin"],
             "ok": (got["origin"] or {}).get("origin_doctype") == cfg["doctype"] and (got["origin"] or {}).get("origin_name") == params.get("doc")},
            {"case": "every line is remembered for this company", "seen": got["remembered"], "ok": got["remembered"] == 3}]
