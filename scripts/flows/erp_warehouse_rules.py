"""流程用例：ERP 仓库规则（多仓 P0 / 仓位清单，服务端判，用页面同一套接口）。

管理员：① 有货的仓不能停用；② 空仓能停用、再启用；③ 仓的仓位清单只给盘点记「实际仓位」（11-19 起库存只按物料 + 仓库，
仓位只留在盘点）：清单能保存；入库不再看仓位，进的是这个仓；盘点记实际仓位只能记清单里的，清单外被拒、清单内能记、记录上是那个仓位。
老数据（本地公司）每次现建一个空仓来测 ②③；新开户的仓都由 Express 同步来、由 Express 维护，② 改为零库存的仓停用也被拒，③ 用 07 号仓。
库里：空仓最后是启用的；这件商品在这个仓的账面 +1、余额表没有仓位一栏；盘点里只有清单内那条记录、仓位是清单里的那个。
跑完删掉这份盘点（还在进行中），不留给别的流程。
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
    before = lab.python(manifest["site"], f"""
print(json.dumps({{"qty": float(frappe.db.get_value("ERP Stock Balance", {{"warehouse": {out['target']!r}, "product": {product!r}}}, "qty_on_hand") or 0)}}))
""")["qty"]
    return {**out, "product": product, "before": before, "location": "F-" + uuid.uuid4().hex[:4].upper(),
            "linked": bool(manifest.get("express_linked"))}


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
// 入库按行选仓、不看仓位：进的是行上的仓
out.stock_in=await tryCall(I+'api.api_create_stock_entry', {{direction: 'in', posting_date: frappe.datetime.get_today(), submit: 1,
  lines: JSON.stringify([{{product: {p['product']}, qty: 1, unit_cost: 10, warehouse: {p['target']}}}])}});
// 仓位清单只管盘点记「实际仓位」
const task=await call(I+'api.api_create_stock_count', {{title: 'flow rules ' + {p['location']}, warehouse: {p['target']}}});
out.count=task&&task.name;
const counted=loc=>tryCall(I+'api.api_stock_count_add_entry', {{count: out.count, product: {p['product']}, qty: 1, warehouse: {p['target']}, location: loc}});
out.count_unlisted=await counted('ZZ-99');
out.count_listed=await counted({p['location']});
all('.modal.show .btn-modal-close').forEach(b=>b.click());
return out;"""}]


def judge(params, results, camera_code=None):
    v = next((r.get("value") for r in results if r.get("step") == "仓库规则"), None) or {}
    ok = lambda key: (v.get(key) or {}).get("ok")  # noqa: E731
    checks = [{"case": "有货的仓停用被拒", "seen": v.get("stocked_off"), "ok": ok("stocked_off") is False},
              {"case": "仓位清单保存", "seen": v.get("save_locations"), "ok": ok("save_locations") is True},
              {"case": "入库按行上的仓进，不看仓位", "seen": v.get("stock_in"), "ok": ok("stock_in") is True},
              {"case": "盘点记清单外的实际仓位被拒", "seen": v.get("count_unlisted"), "ok": ok("count_unlisted") is False},
              {"case": "盘点记清单内的实际仓位", "seen": v.get("count_listed"), "ok": ok("count_listed") is True}]
    params["count"] = v.get("count")
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
qty = float(frappe.db.get_value("ERP Stock Balance", {{"warehouse": {params['target']!r}, "product": {params['product']!r}}}, "qty_on_hand") or 0)
entries = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params.get('count') or '-'!r}}}, fields=["warehouse", "location", "qty"],
                         limit_page_length=10)
print(json.dumps({{"qty": qty, "balance_location": bool(frappe.get_meta("ERP Stock Balance").has_field("location")),
                  "entries": [dict(e, qty=float(e.qty)) for e in entries],
                  "empty_active": frappe.db.get_value("ERP Warehouse", {params.get('empty')!r}, "is_active") if {params.get('empty')!r} else None}}))
""")
    return [{"case": "这个仓这件商品 +1，余额只按物料 + 仓库", "seen": [params["before"], row["qty"], row["balance_location"]],
             "ok": row["qty"] == params["before"] + 1 and row["balance_location"] is False},
            {"case": "盘点只记下清单内那一条，仓位是清单里的", "seen": row["entries"],
             "ok": row["entries"] == [{"warehouse": params["target"], "location": params["location"], "qty": 1.0}]},
            {"case": "空仓最后是启用的", "seen": row["empty_active"], "ok": bool(row["empty_active"])}]


def cleanup(lab, manifest, params):
    """这一遍开的盘点（还在进行中）删掉，不留一份进行中的盘点给别的流程（成功、失败、超时都跑）。"""
    lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
frappe.set_user("admin@erp.regression")
names = frappe.get_all("ERP Stock Count", filters={{"title": "flow rules " + {params['location']!r}, "status": "Active"}}, pluck="name",
                       limit_page_length=5)
for name in names:
    count.delete_count(name)
frappe.db.commit()
print(json.dumps({{"deleted": names}}))
""")
