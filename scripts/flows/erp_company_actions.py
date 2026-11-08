"""流程用例：ERP 公司页页头逐个实点（清存量基线 11-08）。

管理员打开公司：
- 列表「新建公司」→ 新建页「取消」→ 回列表，库里公司数不变；
- 打开一家公司改备注（接了 Express 的公司电话等资料以 Express 为准、只读，备注两种都能改）→ 页头「保存」→ 库里这家公司的备注是填的；
- 仓库页「新建仓库」：老数据实建一个，库里它属于当时的当前公司；接了 Express 的公司没有这颗（仓库以 Express 为准）；
- 新开户（接了 Express）：这家公司页头「同步 Express」→ 请求只发起读取、立刻回一个状态；库里这家公司记下的同步状态就是回来的那个
  （prepare 先清空；实验室没有接电脑，多半是「电脑离线」）；
- 老数据（两家公司、当前是 A）：打开 B 点「设为当前公司」→ 页面整页刷新 → 左上角当前公司是 B、库里本人的当前公司是 B。cleanup 换回 A。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "ERP 公司页：新建后取消、保存、同步 Express、设为当前公司"
ADMIN = "admin@erp.regression"


def prepare(lab, manifest):
    companies = manifest.get("companies") or [manifest["company"]]
    linked = bool(manifest.get("express_linked"))
    target = companies[1] if len(companies) > 1 else companies[0]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
frappe.set_user({ADMIN!r})
current = companies.active_company()
if {linked!r}:
    frappe.db.set_value("ERP Company", {target!r}, {{"express_sync_state": None, "express_sync_note": None}}, update_modified=False)
    frappe.db.commit()
print(json.dumps({{"current": current or "", "count": frappe.db.count("ERP Company"),
                  "note": frappe.db.get_value("ERP Company", {target!r}, "note") or ""}}))
""")
    return {**out, "target": target, "linked": linked, "switch": len(companies) > 1,
            "new_note": "flow note " + uuid.uuid4().hex[:6], "warehouse": "คลังโฟลว์ปุ่ม " + uuid.uuid4().hex[:6], "cases": []}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
frappe.set_user({ADMIN!r})
if {params['current']!r}:
    companies.set_active_company({params['current']!r})
frappe.db.set_value("ERP Company", {params['target']!r}, "note", {params['note']!r}, update_modified=False)
frappe.db.commit()
print(json.dumps({{}}))
""")


SETUP = JS + ACTION + """
const t=await frappe.pearnly.texts('company.');
const open=async(name)=>{frappe.set_route('pearnly-company', name);
  return await until(()=>frappe.get_route()[1]===name&&pageOf('pearnly-company')?.querySelector('[data-fieldname="note"] textarea, [data-fieldname="note"] input')&&$.active===0, 25000);};
"""


def steps(params, camera_code=None):
    target = js_value(params["target"])
    out = [
        {"name": "新建取消", "js": SETUP + """
frappe.set_route('pearnly-company'); await until(()=>frappe.get_route().length===1&&pageOf('pearnly-company')&&$.active===0, 20000); await idle();
const via=await action(t('company.new')); if(!via) return {error:'no new company button'};
const opened=await until(()=>frappe.get_route()[1]==='new', 15000); await idle();
const back=await action(__('Cancel')); if(!back) return {error:'no Cancel button'};
const listed=await until(()=>frappe.get_route().length===1, 15000); await idle();
return {via, opened: !!opened, back, listed: !!listed};"""},
        {"name": "保存", "js": SETUP + f"""
if(!await open({target})) return {{error:'company page did not open'}}; await idle(); await sleep(500);
const el=pageOf('pearnly-company').querySelector('[data-fieldname="note"] textarea, [data-fieldname="note"] input');
setInput(el, {js_value(params['new_note'])}); await sleep(400);
const via=await action(t('company.save')); if(!via) return {{error:'no Save button'}};
await sleep(800); await idle();
return {{via, msg: msg(), alerts: alerts()}};"""},
    ]
    # 仓库页「新建仓库」：接了 Express 的公司仓库以 Express 为准、没有这颗（`warehouses.js` can_create）；没接的实建一个
    out.append({"name": "新建仓库", "js": SETUP + f"""
frappe.set_route('pearnly-warehouses'); await until(()=>frappe.get_route().length===1&&pageOf('pearnly-warehouses')&&$.active===0, 20000); await idle(); await sleep(500);
if({'true' if params['linked'] else 'false'}) return {{absent: !all('.page-head button').some(b=>b.innerText.trim()===t('company.newWarehouse'))}};
const via=await action(t('company.newWarehouse')); if(!via) return {{error:'no new warehouse button'}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog.fields_dict.warehouse_name&&cur_dialog, 15000); if(!d) return {{error:'no warehouse dialog'}};
await d.set_value('warehouse_name', {js_value(params['warehouse'])}); await sleep(300);
d.get_primary_btn().click(); const closed=await until(()=>!d.$wrapper.is(':visible'), 20000); await idle();
return {{via, closed: !!closed, msg: msg()}};"""})
    if params["linked"]:
        out.append({"name": "同步 Express", "js": SETUP + f"""
window.__sync=[]; if(!window.__shook){{window.__shook=1; const _c=frappe.call; frappe.call=function(o){{const r=_c.apply(this, arguments);
  if(o&&String(o.method||'').endsWith('express.api.sync_company')) Promise.resolve(r).then(x=>window.__sync.push((x&&x.message)||x)).catch(()=>window.__sync.push('error')); return r;}};}}
if(!await open({target})) return {{error:'company page did not open'}}; await idle();
const via=await action(t('company.syncExpress')); if(!via) return {{error:'no sync button'}};
await until(()=>window.__sync.length, 15000); await idle();
return {{via, result: window.__sync[0]||null}};"""})
    if params["switch"]:
        out.append({"name": "设为当前", "wait_ms": 6000, "js": SETUP + f"""
if(!await open({target})) return {{error:'company page did not open'}}; await idle();
// 点下去会写当前公司、整页刷新：点完就回，下一步在刷新后的页面上看
const via=await action(t('company.setCurrent')); return {{via}};"""})
        out.append({"name": "刷新后当前公司", "js": """
for(let k=0;k<120&&!(window.frappe&&frappe.boot&&frappe.router&&document.readyState==='complete'&&window.frappe.pearnly);k++) await new Promise(r=>setTimeout(r,250));
return {current: (window.frappe&&frappe.boot&&frappe.boot.pearnly_company)||''};"""})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    cancel, save = by.get("新建取消", {}), by.get("保存", {})
    checks = [
        {"case": "New company opens the new page and Cancel goes back to the list", "seen": cancel,
         "ok": bool(cancel.get("via")) and cancel.get("opened") is True and bool(cancel.get("back")) and cancel.get("listed") is True},
        {"case": "the head Save button saves without a message", "seen": save, "ok": bool(save.get("via")) and not save.get("msg")},
    ]
    store = by.get("新建仓库", {})
    checks.append({"case": "warehouse page New: absent for an Express company, otherwise the dialog saves", "seen": store,
                   "ok": store.get("absent") is True if params["linked"] else (bool(store.get("via")) and store.get("closed") is True and not store.get("msg"))})
    if params["linked"]:
        sync = by.get("同步 Express", {})
        checks.append({"case": "Sync Express answers at once with a state", "seen": sync,
                       "ok": bool(sync.get("via")) and isinstance(sync.get("result"), dict) and bool(sync["result"].get("state"))})
    if params["switch"]:
        switch, after = by.get("设为当前", {}), by.get("刷新后当前公司", {})
        checks.append({"case": "Set as current reloads into the chosen company", "seen": {"switch": switch, "after": after},
                       "ok": bool(switch.get("via")) and after.get("current") == params["target"]})
    return checks


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
frappe.set_user({ADMIN!r})
from pearnly_erp_portal.masters import company as companies
current = companies.active_company()
row = frappe.db.get_value("ERP Company", {params['target']!r}, ["note", "express_sync_state"], as_dict=True)
stores = frappe.get_all("ERP Warehouse", filters={{"warehouse_name": {params['warehouse']!r}}}, fields=["company", "is_active"])
print(json.dumps({{"count": frappe.db.count("ERP Company"), "current": current or "", "stores": stores, **row}}, default=str))
""")
    checks = [
        {"case": "Cancel created no company", "saved": got["count"], "ok": got["count"] == params["count"]},
        {"case": "Save stored the typed note", "saved": got["note"], "ok": got["note"] == params["new_note"]},
    ]
    checks.append({"case": "the new warehouse belongs to the company that was current", "saved": got["stores"],
                   "ok": got["stores"] == [] if params["linked"] else (len(got["stores"]) == 1 and got["stores"][0]["company"] == params["current"])})
    if params["linked"]:
        checks.append({"case": "the company records the sync state", "saved": got["express_sync_state"], "ok": bool(got["express_sync_state"])})
    if params["switch"]:
        checks.append({"case": "the user's current company is the chosen one", "saved": got["current"], "ok": got["current"] == params["target"]})
    return checks
