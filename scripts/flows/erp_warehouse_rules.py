"""流程用例：ERP 仓库规则（多仓 P0 / 库位清单，服务端判，用页面同一套接口）。

管理员：① 有货的仓不能停用；② 空仓能停用、再启用；③ 仓设了库位清单后，入库只能进清单里的库位，清单外被拒，清单内能进、记在那个库位。
老数据（本地公司）每次现建一个空仓来测 ②③；新开户的仓都由 Express 同步来、由 Express 维护，② 改为零库存的仓停用也被拒，③ 用 07 号仓。
库里：空仓最后是启用的；库位清单里的那个库位这件商品的账面 +1，清单外的库位没有。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, codes, company, js_value, warehouses  # noqa: E402

TITLE = "ERP 仓库：有货不能停用、空仓停用启用、库位清单管入库"


def prepare(lab, manifest):
    co, rows = company(manifest), warehouses(manifest)
    product = next(iter(codes(manifest).values()))["product"]
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import documents, warehouses
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({co!r})
names = {[w["name"] for w in rows]!r}
qty = lambda w: frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance` where warehouse=%s", w)[0][0]
stocked = next((w for w in names if qty(w) > 0), None)
if not stocked:
    stocked = names[0]
    documents.create_entry(direction="in", posting_date=frappe.utils.nowdate(), warehouse=stocked, reason="flow warehouse rules",
                           lines=[{{"product": {product!r}, "qty": 1, "unit_cost": 10, "warehouse": stocked}}], submit=True)
if {bool(manifest.get('express_linked'))!r}:
    target = next(w for w in names if frappe.db.get_value("ERP Warehouse", w, "express_code") == "07")
    empty = next((w for w in names if w not in (stocked, target) and qty(w) == 0), None)
else:
    empty = warehouses.create_warehouse(warehouse_name="คลังโฟลว์ " + {uuid.uuid4().hex[:6]!r}, company={co!r})["name"]
    target = empty
frappe.db.commit()
print(json.dumps({{"stocked": stocked, "empty": empty, "target": target}}))
""")
    return {**out, "product": product, "location": "F-" + uuid.uuid4().hex[:4].upper(), "linked": bool(manifest.get("express_linked"))}


def steps(params, camera_code=None):
    p = {k: js_value(v) for k, v in params.items()}
    return [{"name": "仓库规则", "js": JS + f"""
const I='pearnly_erp_portal.inventory.';
const tryCall=(method,args)=>new Promise(resolve=>frappe.call({{method,args,type:'POST',callback:r=>resolve({{ok:true}}),
  error:r=>resolve({{ok:false, message: JSON.stringify((r&&r._server_messages)||'')}})}}).fail(x=>resolve({{ok:false, message: JSON.stringify((x&&x.responseJSON&&x.responseJSON._server_messages)||'')}})));
const out={{}};
out.stocked_off=await tryCall(I+'api.api_update_warehouse', {{name: {p['stocked']}, is_active: 0}});
out.empty_off=await tryCall(I+'api.api_update_warehouse', {{name: {p['empty']}, is_active: 0}});
out.empty_on=await tryCall(I+'api.api_update_warehouse', {{name: {p['empty']}, is_active: 1}});
const current=await call(I+'locations.api_get_locations', {{warehouse: {p['target']}}}, 'GET');
const rows=[...((current&&(current.rows||current.locations))||current||[]).map(r=>({{location_name: r.location_name, is_active: r.is_active}})), {{location_name: {p['location']}, is_active: 1}}];
out.save_locations=await tryCall(I+'locations.api_save_locations', {{warehouse: {p['target']}, rows: JSON.stringify(rows)}});
const line=l=>JSON.stringify([{{product: {p['product']}, qty: 1, unit_cost: 10, warehouse: {p['target']}, location: l}}]);
out.in_unlisted=await tryCall(I+'api.api_create_stock_entry', {{direction: 'in', posting_date: frappe.datetime.get_today(), warehouse: {p['target']}, lines: line('ZZ-99'), submit: 1}});
out.in_listed=await tryCall(I+'api.api_create_stock_entry', {{direction: 'in', posting_date: frappe.datetime.get_today(), warehouse: {p['target']}, lines: line({p['location']}), submit: 1}});
all('.modal.show .btn-modal-close').forEach(b=>b.click());
return out;"""}]


def judge(params, results, camera_code=None):
    v = next((r.get("value") for r in results if r.get("step") == "仓库规则"), None) or {}
    ok = lambda key: (v.get(key) or {}).get("ok")  # noqa: E731
    checks = [{"case": "有货的仓停用被拒", "seen": v.get("stocked_off"), "ok": ok("stocked_off") is False},
              {"case": "库位清单保存", "seen": v.get("save_locations"), "ok": ok("save_locations") is True},
              {"case": "清单外库位入库被拒", "seen": v.get("in_unlisted"), "ok": ok("in_unlisted") is False},
              {"case": "清单内库位入库", "seen": v.get("in_listed"), "ok": ok("in_listed") is True}]
    if not params.get("empty"):
        checks.append({"case": "有零库存的仓可试停用", "seen": None, "ok": False})
    elif params.get("linked"):
        # Express 同步来的仓由 Express 维护：ERP 里空仓也停用不了（「Maintained in Express」）
        checks.append({"case": "Express 维护的仓停用被拒", "seen": v.get("empty_off"),
                       "ok": ok("empty_off") is False and "Express" in (v.get("empty_off") or {}).get("message", "")})
    else:
        checks += [{"case": "空仓停用", "seen": v.get("empty_off"), "ok": ok("empty_off") is True},
                   {"case": "空仓再启用", "seen": v.get("empty_on"), "ok": ok("empty_on") is True}]
    return checks


def verify(lab, manifest, params, camera_code=None):
    row = lab.python(manifest["site"], f"""
at = lambda loc: float(frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance` where warehouse=%s and product=%s and location=%s",
                                      ({params['target']!r}, {params['product']!r}, loc))[0][0])
print(json.dumps({{"listed": at({params['location']!r}), "unlisted": at("ZZ-99"),
                  "empty_active": frappe.db.get_value("ERP Warehouse", {params.get('empty')!r}, "is_active") if {params.get('empty')!r} else None}}))
""")
    return [{"case": "清单内库位 +1、清单外没有", "seen": row, "ok": row["listed"] == 1 and row["unlisted"] == 0},
            {"case": "空仓最后是启用的", "seen": row["empty_active"], "ok": bool(row["empty_active"])}]
