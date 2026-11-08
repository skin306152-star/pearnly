"""流程用例：Cowork 盘点列表「新建盘点」（上传表格建）与盘点页「结束盘点」（清存量基线 11-08）。

盘点岗：
- 列表页头「新建盘点」→ 原生弹窗填名称 → 点「选择文件」在框架原生上传框里放一份按模板填的小表（prepare 生成，两行）→ 弹窗「新建盘点」→ 打开这份；
  库里多一份这个名称的盘点任务、两行物料。
- prepare 另按正式函数建一份盘点并记一项（`stocktake.service.write`）：打开它点页头「结束盘点」→ 原生确认 → 库里这份不再进行中。
「开始盘点」那颗由 `cowork.stocktake.entry` 实点（连续录入、库里每件一条记录）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "Cowork 盘点：上传表格新建盘点、结束盘点"
USER = "stocktake@cowork.regression"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    rows = [(f"CWN{tag}A", f"Flow new A {tag}", "", "pcs", 4), (f"CWN{tag}B", f"Flow new B {tag}", "", "pcs", 2)]
    out = lab.python(manifest["site"], f"""
import base64, io, uuid
from openpyxl import load_workbook
from pearnly_cowork.stocktake import service, upload
frappe.set_user({USER!r})
def sheet(items):
    book = load_workbook(io.BytesIO(upload.template()))
    for code, name, barcode, unit, qty in items:
        book.active.append([code, name, barcode, unit, qty, "", "Main", "A-1", ""])
    data = io.BytesIO(); book.save(data)
    return data.getvalue()
fresh = base64.b64encode(sheet({rows!r})).decode()
task = upload.create("flow close {tag}", str(uuid.uuid4()), sheet([("CWC{tag}", "Flow close {tag}", "", "pcs", 3)]))
frappe.db.commit()
item = frappe.get_all("Cowork Stocktake Item", filters={{"task": task["id"]}}, fields=["name", "warehouse", "location"], limit_page_length=1)[0]
service.write(task["id"], item.name, str(uuid.uuid4()), 3, item.warehouse or "Main", item.location or "")
frappe.db.commit()
print(json.dumps({{"sheet": fresh, "closing": task["id"]}}))
""")
    return {**out, "title": f"flow new {tag}", "cases": []}


SETUP = JS + ACTION + "const t=await frappe.pearnly.texts('cowork.stock.');"


def steps(params, camera_code=None):
    return [
        {"name": "新建盘点", "js": SETUP + f"""
await until(()=>pageOf('pearnly-stocktake')&&frappe.get_route().length===1&&$.active===0, 25000); await idle();
const via=await action(t('cowork.stock.count.new')); if(!via) return {{error:'no new count button'}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog.fields_dict.title&&cur_dialog, 15000);
if(!d) return {{error:'no new count dialog'}};
await d.set_value('title', {js_value(params['title'])}); await sleep(300);
d.fields_dict.pick.$input.click();
const up=await until(()=>[...document.querySelectorAll('.modal.show')].find(m=>m.querySelector('input[type=file]')), 15000);
if(!up) return {{error:'no uploader'}};
const raw=atob({js_value(params['sheet'])}); const bytes=new Uint8Array(raw.length); for(let i=0;i<raw.length;i++) bytes[i]=raw.charCodeAt(i);
const box=up.querySelector('input[type=file]'); const dt=new DataTransfer();
dt.items.add(new File([bytes],'flow-count.xlsx',{{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}}));
box.files=dt.files; box.dispatchEvent(new Event('change',{{bubbles:true}})); await sleep(600);
const send=await until(()=>[...up.querySelectorAll('.modal-footer .btn-primary')].find(b=>b.offsetParent&&!b.disabled), 10000);
if(!send) return {{error:'no upload button'}}; send.click();
const picked=await until(()=>d.get_value('file_name'), 20000); await idle(); await sleep(300);
if(!picked) return {{error:'sheet not attached'}};
d.get_primary_btn().click();
const opened=await until(()=>!d.$wrapper.is(':visible')&&frappe.get_route()[1], 25000); await idle();
return {{via, opened: !!opened, route: frappe.get_route().join('/'), msg: msg()}};"""},
        {"name": "结束盘点", "js": SETUP + f"""
frappe.set_route('pearnly-stocktake', {js_value(params['closing'])});
await until(()=>frappe.get_route()[1]==={js_value(params['closing'])}&&all('.page-head button').length&&$.active===0, 25000); await idle(); await sleep(500);
const via=await action(t('cowork.stock.count.close')); if(!via) return {{error:'no close button'}};
const yes=await until(()=>all('.modal.show .btn-primary')[0], 10000); if(!yes) return {{error:'no confirm dialog'}};
const shown=all('.modal.show').map(m=>(m.querySelector('.modal-body')?.innerText||'').trim().slice(0,60));
// 等确认框淡入完再点：过渡中点「是」，回调照跑、关闭被 bootstrap 忽略（人手点不了这么快）
await sleep(600); yes.click(); await until(()=>!all('.modal.show').length, 15000); await sleep(300); await idle();
return {{via, shown, left: all('.modal.show').map(m=>(m.querySelector('.modal-body')?.innerText||'').trim().slice(0,60)), msg: msg()}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    new, close = by.get("新建盘点", {}), by.get("结束盘点", {})
    return [
        {"case": "New count with an uploaded sheet creates the count and opens it", "seen": new,
         "ok": bool(new.get("via")) and new.get("opened") is True and not new.get("msg")},
        {"case": "Close count confirms without a message", "seen": close, "ok": bool(close.get("via")) and not close.get("msg")},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("Cowork Stocktake Task", filters={{"title": {params['title']!r}}}, fields=["name", "status"])
items = frappe.db.count("Cowork Stocktake Item", {{"task": rows[0]["name"]}}) if rows else 0
closing = frappe.db.get_value("Cowork Stocktake Task", {params['closing']!r}, "status")
print(json.dumps({{"rows": rows, "items": items, "closing": closing}}, default=str))
""")
    return [
        {"case": "one count with this name and both sheet rows", "saved": got, "ok": len(got["rows"]) == 1 and got["items"] == 2},
        {"case": "the closed count is no longer active", "saved": got["closing"], "ok": bool(got["closing"]) and got["closing"] != "active"},
    ]
