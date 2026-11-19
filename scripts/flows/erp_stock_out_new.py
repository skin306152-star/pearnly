"""流程用例：ERP 出库列表页头「新建出库」→ 原生弹窗填单 →「提交」（清存量基线 11-08）。

库管打开出库：页头「新建出库」开弹窗（单头不放仓，11-19 起仓库按行）→ 明细第一行输物料编码回车认出 → 这一行选仓 → 数量 1 →
弹窗「提交」。prepare 先按正式接口在这个仓入 1 件（保证够出）。库里：这张出库单已提交、明细就是这件 1 个、行上是这个仓；
库存流水里这张单记了这件 -1。只认本次这张单，不比账面总数（别的流程也在这个仓进出）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, LINES, codes, js_value, warehouses  # noqa: E402

TITLE = "ERP 出库：列表新建出库、弹窗提交"
USER = "stock@erp.regression"


def prepare(lab, manifest):
    item = codes(manifest)["by_code"]
    wh = warehouses(manifest)[0]["name"]
    out = lab.python(manifest["site"], f"""
frappe.set_user({USER!r})
from pearnly_erp_portal.inventory import api
api.api_create_stock_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse={wh!r}, submit=1,
                           lines=json.dumps([{{"product": {item['product']!r}, "qty": 1, "unit_cost": 10, "warehouse": {wh!r}}}]))
frappe.db.commit()
name = frappe.db.get_value("ERP Product", {item['product']!r}, "product_name")
last = frappe.get_all("ERP Stock Entry", filters={{"direction": "out"}}, pluck="name", order_by="creation desc", limit_page_length=1)
print(json.dumps({{"name": name, "last": last[0] if last else ""}}))
""")
    return {"product": item["product"], "scan": item["scan"], "name": out["name"], "warehouse": wh, "last": out["last"], "cases": []}


def steps(params, camera_code=None):
    return [{"name": "新建出库", "js": JS + LINES + f"""
await until(()=>pageOf('pearnly-stock-out')&&document.querySelector('.result, .no-result'), 25000); await idle();
const b=all('.page-head .primary-action, .page-head .btn-primary').find(e=>e.offsetParent); if(!b) return {{error:'no new button'}};
const label=b.innerText.trim(); b.click();
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog.fields_dict.lines&&cur_dialog.fields_dict.add_line&&cur_dialog, 15000);
if(!d) return {{error:'no stock entry dialog'}}; await idle();
const head=Object.keys(d.fields_dict).filter(k=>['warehouse','location','to_warehouse','to_location'].includes(k));
const l=await until(()=>entryLines(d).pop(), 10000); if(!l) return {{error:'no line'}};
const i=d.$wrapper.find('.pearnly-line-editor [data-fieldname="product"] input')[0]; if(!i) return {{error:'no line product box'}};
i.focus(); i.value={js_value(params['scan'])}; i.dispatchEvent(new Event('input',{{bubbles:true}}));
i.dispatchEvent(new KeyboardEvent('keydown',{{key:'Enter',bubbles:true}}));
const desc=()=>d.$wrapper.find('.pearnly-line-editor [data-fieldname="description"] input, .pearnly-line-editor [data-fieldname="description"] textarea').val();
await until(desc, 15000); await idle();
await l.set_value('warehouse', {js_value(params['warehouse'])}); await sleep(300); await idle();
const q=d.$wrapper.find('.pearnly-line-editor [data-fieldname="qty"] input')[0]; if(q){{setInput(q,'1'); await sleep(300);}}
const submit=d.get_primary_btn().text().trim(); d.get_primary_btn().click();
const closed=await until(()=>!d.$wrapper.is(':visible'), 25000); await idle();
return {{label, submit, head, line_warehouse: l.get_value('warehouse'), line: desc()||'', closed: !!closed, msg: msg()}};"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "新建出库"), None) or {}
    return [{"case": "the list New button opens the entry dialog, the line finds the product and Submit closes it", "seen": seen,
             "ok": bool(seen.get("label")) and seen.get("line") == params["name"] and seen.get("closed") is True and not seen.get("msg")},
            # 仓库按行（11-19，同 Express 原生 OU）：单头没有仓库 / 仓位，仓在明细行上
            {"case": "the header has no warehouse or location; the line holds the warehouse", "seen": seen,
             "ok": seen.get("head") == [] and seen.get("line_warehouse") == params["warehouse"]}]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Entry", filters={{"direction": "out", "owner": {USER!r}}}, fields=["name", "status", "warehouse"],
                      order_by="creation desc", limit_page_length=1)
row = rows[0] if rows else {{}}
lines = frappe.get_all("ERP Stock Entry Line", filters={{"parent": row.get("name") or ""}}, fields=["product", "qty", "warehouse"]) if row else []
moves = frappe.db.sql("select sum(qty_base) from `tabERP Stock Movement` where ref_id=%s and product=%s", (row.get("name") or "", {params['product']!r}))[0][0] if row else None
print(json.dumps({{"row": row, "lines": lines, "moves": moves}}, default=str))
""")
    row = got["row"] or {}
    return [
        {"case": "a new submitted stock out entry holds this product, one unit, in this warehouse", "saved": got,
         "ok": bool(row.get("name")) and row["name"] != params["last"] and row.get("status") == "submitted"
         and row.get("warehouse") == params["warehouse"]
         and [(line["product"], float(line["qty"]), line["warehouse"]) for line in got["lines"]] == [(params["product"], 1.0, params["warehouse"])]},
        {"case": "the stock ledger took the unit out", "saved": got["moves"], "ok": got["moves"] is not None and float(got["moves"]) == -1.0},
    ]
