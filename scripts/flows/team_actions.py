"""流程用例：团队页的新建 / 保存 / 取消逐个实点（ERP、Cowork 同一个模块，按站点认产品；清存量基线 11-08）。

管理员打开团队：
- 列表页头「新建」→ 原生弹窗：填名、姓、用户名、初始密码，勾一个功能 → 弹窗主按钮「新建」→ 库里多了这位用户（名、姓、用户名对，有勾的那个角色）；
- 整页新建（弹窗同一套字段，地址 `pearnly-team/new`）：填好点页头「新建」→ 回到名单 → 库里多了第二位；
- 第一位整页编辑：名改掉点页头「保存」→ 回到名单 → 库里名是改后的；
- 第二位整页编辑：名改掉点页头「取消」→ 回到名单 → 库里名没变。
建的两位跑完停用（同 `team_full_name`：删用户要动一串关联表，并行时撞锁）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "团队页：列表新建弹窗、整页新建、整页编辑保存与取消"


def _app(manifest):
    return "pearnly_cowork" if manifest["site"].startswith("cowork") else "pearnly_erp_portal"


def prepare(lab, manifest):
    app = _app(manifest)
    admin = "admin@cowork.regression" if app == "pearnly_cowork" else "admin@erp.regression"
    out = lab.python(manifest["site"], f"""
from {app}.team import service
frappe.set_user({admin!r})
roles = [row["name"] for row in service.role_catalog() if not str(row["name"]).endswith("LINE")]
print(json.dumps({{"role": roles[0] if roles else ""}}))
""")
    tag = uuid.uuid4().hex[:6]
    return {**out, "app": app, "prefix": "cowork.team." if app == "pearnly_cowork" else "team.",
            "dialog": {"username": "flowdlg" + tag, "first": "Dlg" + tag, "last": "Flow"},
            "page": {"username": "flowpage" + tag, "first": "Page" + tag, "last": "Flow"},
            "renamed": "Saved" + tag, "cancelled": "Dropped" + tag, "cases": []}


# 管理员团队页：术语（`p.texts`）、等名单画好、填一套新建字段（名 / 姓 / 用户名 / 密码，勾一个功能）；
# 单行文字格外壳画成自动长高的多行框（textarea），密码格是 input
SETUP = JS + ACTION + """
const T=async(prefix)=>await frappe.pearnly.texts(prefix);
const at=(...sub)=>{const r=frappe.get_route();return r[0]==='pearnly-team'&&r.length===1+sub.length&&sub.every((x,i)=>r[i+1]===x);};
const listed=async()=>{await until(()=>at()&&document.querySelector('.result'), 25000); await idle();};
const fill=async(root,v,role)=>{
  for(const [k,val] of Object.entries(v)){const el=root.querySelector('[data-fieldname="'+k+'"] input, [data-fieldname="'+k+'"] textarea'); if(!el) return 'no field '+k; setInput(el,val); await sleep(150);}
  const box=root.querySelector('input[type=checkbox][data-unit="'+role+'"]'); if(!box) return 'no role box '+role; if(!box.checked) box.click(); await sleep(200); return '';};
"""


def _member(v):
    return {"first_name": v["first"], "last_name": v["last"], "username": v["username"], "password": "Flow-" + v["username"]}


def steps(params, camera_code=None):
    prefix, role = js_value(params["prefix"]), js_value(params["role"])
    dlg, pg = js_value(_member(params["dialog"])), js_value(_member(params["page"]))
    email_of = "const email_of=async(u)=>((await call(" + js_value(params["app"] + ".team.api.api_list_members") + \
        ",{},'GET'))?.members||[]).find(m=>m.username===u)?.email||'';"
    return [
        {"name": "列表新建弹窗", "js": SETUP + f"""
const t=await T({prefix}); await listed();
const via=await action(t({prefix}+'new')); if(!via) return {{error:'no New button'}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000); if(!d) return {{error:'no new dialog'}};
const miss=await fill(d.$wrapper[0], {dlg}, {role}); if(miss) return {{error: miss}};
const label=d.get_primary_btn().text().trim(); d.get_primary_btn().click();
const closed=await until(()=>!d.$wrapper.is(':visible'), 20000); await idle();
return {{via, label, closed: !!closed, msg: msg()}};"""},
        {"name": "整页新建", "js": SETUP + f"""
const t=await T({prefix}); frappe.set_route('pearnly-team','new');
const form=await until(()=>at('new')&&pageOf('pearnly-team')?.querySelector('[data-fieldname="first_name"] input, [data-fieldname="first_name"] textarea'), 25000); await idle();
if(!form) return {{error:'full page new did not open'}};
const miss=await fill(pageOf('pearnly-team'), {pg}, {role}); if(miss) return {{error: miss}};
const via=await action(t({prefix}+'new')); if(!via) return {{error:'no New button on the full page'}};
const back=await until(()=>at(), 20000); await idle();
return {{via, back: !!back, msg: msg()}};"""},
        {"name": "整页编辑保存", "js": SETUP + email_of + f"""
const email=await email_of({js_value(params['dialog']['username'])}); if(!email) return {{error:'dialog member not listed'}};
frappe.set_route('pearnly-team','edit',email);
const el=await until(()=>at('edit',email)&&pageOf('pearnly-team')?.querySelector('[data-fieldname="first_name"] input, [data-fieldname="first_name"] textarea'), 25000); await idle();
if(!el) return {{error:'edit page did not open'}};
setInput(el, {js_value(params['renamed'])}); await sleep(300);
const via=await action(__('Save')); if(!via) return {{error:'no Save button'}};
const back=await until(()=>at(), 20000); await idle();
return {{via, back: !!back, msg: msg()}};"""},
        {"name": "整页编辑取消", "js": SETUP + email_of + f"""
const email=await email_of({js_value(params['page']['username'])}); if(!email) return {{error:'page member not listed'}};
frappe.set_route('pearnly-team','edit',email);
const el=await until(()=>at('edit',email)&&pageOf('pearnly-team')?.querySelector('[data-fieldname="first_name"] input, [data-fieldname="first_name"] textarea'), 25000); await idle();
if(!el) return {{error:'edit page did not open'}};
setInput(el, {js_value(params['cancelled'])}); await sleep(300);
const via=await action(__('Cancel')); if(!via) return {{error:'no Cancel button'}};
const back=await until(()=>at(), 20000); await idle();
return {{via, back: !!back, msg: msg()}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    new, page, save, cancel = (by.get(k, {}) for k in ("列表新建弹窗", "整页新建", "整页编辑保存", "整页编辑取消"))
    return [
        {"case": "the list New button opens the dialog and its New button closes it", "seen": new,
         "ok": bool(new.get("via")) and new.get("closed") is True and not new.get("msg")},
        {"case": "the full page New button goes back to the list", "seen": page,
         "ok": bool(page.get("via")) and page.get("back") is True and not page.get("msg")},
        {"case": "the full page Save and Cancel buttons go back to the list", "seen": {"save": save, "cancel": cancel},
         "ok": all(bool(x.get("via")) and x.get("back") is True and not x.get("msg") for x in (save, cancel))},
    ]


def verify(lab, manifest, params, camera_code=None):
    names = [params["dialog"]["username"], params["page"]["username"]]
    got = lab.python(manifest["site"], f"""
out = {{}}
for username in {names!r}:
    row = frappe.db.get_value("User", {{"username": username}}, ["name", "first_name", "last_name"], as_dict=True) or {{}}
    if row.get("name"):
        row["roles"] = frappe.get_roles(row["name"])
        # 只在实验室：建的用户停用（删用户要改一串关联表，几条流程并行时会撞锁）
        frappe.db.set_value("User", row["name"], "enabled", 0, update_modified=False)
    out[username] = row
frappe.db.commit()
print(json.dumps(out, default=str))
""")
    dialog, page = got.get(names[0]) or {}, got.get(names[1]) or {}
    return [
        {"case": "the dialog created the user with the ticked role", "saved": dialog,
         "ok": bool(dialog.get("name")) and dialog.get("last_name") == params["dialog"]["last"] and params["role"] in (dialog.get("roles") or [])},
        {"case": "the full page created the second user with the ticked role", "saved": page,
         "ok": bool(page.get("name")) and params["role"] in (page.get("roles") or [])},
        {"case": "Save stored the new first name", "saved": dialog.get("first_name"), "ok": dialog.get("first_name") == params["renamed"]},
        {"case": "Cancel left the first name as it was", "saved": page.get("first_name"), "ok": page.get("first_name") == params["page"]["first"]},
    ]
