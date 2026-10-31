"""流程用例：员工姓名（岳父 10-28 清单第 1 条，用户 10-30：个人信息加姓名，分清是哪个人；登录名不变）。

ERP、Cowork 各跑一遍（同一个模块，按站点认产品）。团队页「新建」弹窗里有名（必填）、姓两格；按团队接口建一个员工（名 + 姓），
名单那一行「姓名」显示拼好的全名、「用户名」是登录名；编辑改名再保存，名单跟着变。打开本人的「我的资料」：名、姓两格看得见、能改，
用户名一格只读。库里：User 的 first_name / last_name / full_name 是填的，username 是登录名。
"""
from __future__ import annotations

import json
import uuid

TITLE = "员工姓名：团队页新建 / 编辑带名姓，名单与我的资料"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
CAPTURE = ("window.__dlgs=window.__dlgs||[];if(!frappe.ui.Dialog.prototype.__cap){const o=frappe.ui.Dialog.prototype.show;"
           "frappe.ui.Dialog.prototype.show=function(){window.__dlgs.push(this);return o.apply(this,arguments)};frappe.ui.Dialog.prototype.__cap=1;}")


def _app(manifest):
    return "pearnly_cowork" if manifest["site"].startswith("cowork") else "pearnly_erp_portal"


def prepare(lab, manifest):
    app = _app(manifest)
    out = lab.python(manifest["site"], f"""
from {app}.team import service
frappe.set_user({("admin@cowork.regression" if app == "pearnly_cowork" else "admin@erp.regression")!r})
roles = [row["name"] for row in service.role_catalog() if not str(row["name"]).endswith("LINE")]
print(json.dumps({{"role": roles[0] if roles else ""}}))
""")
    tag = uuid.uuid4().hex[:6]
    return {**out, "app": app, "username": "flowname" + tag, "first": "สมชาย" + tag[:2], "last": "ใจดี",
            "renamed": "Somsri" + tag[:2], "cases": []}


def steps(params, camera_code=None):
    api = json.dumps(params["app"] + ".team.api.")
    user, first, last, renamed = (json.dumps(params[k]) for k in ("username", "first", "last", "renamed"))
    return [
        {"name": "新建弹窗", "js": IDLE + CAPTURE + "await idle();"
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no new button'}; const n=window.__dlgs.length; b.click();"
         "for(let k=0;k<40&&window.__dlgs.length===n;k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const d=window.__dlgs[window.__dlgs.length-1]; if(!d) return {error:'no dialog'};"
         "const f=d.fields_dict; const out={fields:Object.keys(f), first_reqd: !!(f.first_name&&f.first_name.df.reqd)}; d.hide(); return out;"},
        {"name": "建员工", "js": IDLE +
         f"const r=await frappe.call({{method:{api}+'api_add_member', type:'POST', args:{{username:{user}, first_name:{first}, last_name:{last},"
         f"password:'Flow-'+Date.now(), roles:JSON.stringify([{json.dumps(params['role'])}])}}}});"
         "return {email:(r.message||{}).email||''};"},
        {"name": "名单显示姓名", "js": IDLE +
         f"const r=await frappe.call({{method:{api}+'api_list_members'}}); const rows=(r.message||{{}}).members||[];"
         f"const row=rows.find(x=>x.username==={user})||{{}}; return {{display: row.display||'', username: row.username||'', first: row.first_name||''}};"},
        {"name": "编辑改名", "js": IDLE +
         f"const list=await frappe.call({{method:{api}+'api_list_members'}}); const row=((list.message||{{}}).members||[]).find(x=>x.username==={user});"
         "if(!row) return {error:'member not found'};"
         f"await frappe.call({{method:{api}+'api_set_member_roles', type:'POST', args:{{email:row.email, roles:JSON.stringify(row.roles),"
         f"first_name:{renamed}, last_name:{last}}}}});"
         f"const again=await frappe.call({{method:{api}+'api_list_members'}});"
         f"return {{display: ((((again.message||{{}}).members)||[]).find(x=>x.username==={user})||{{}}).display||''}};"},
        {"name": "我的资料", "js": IDLE +
         "frappe.set_route('Form','User',frappe.session.user); let frm=null;"
         "for(let k=0;k<60;k++){frm=window.cur_frm; if(frm&&frm.doctype==='User'&&frm.docname===frappe.session.user&&frm.fields_dict.first_name) break; await new Promise(r=>setTimeout(r,250));}"
         "await idle(); if(!frm) return {error:'no profile form'};"
         "const vis=n=>{const f=frm.fields_dict[n]; return !!(f&&f.$wrapper&&f.$wrapper.is(':visible'));};"
         "const ro=n=>{const f=frm.fields_dict[n]; return !!(f&&(f.df.read_only||f.disp_status==='Read'));};"
         "return {first_visible: vis('first_name'), last_visible: vis('last_name'), username_visible: vis('username'),"
         "first_editable: !ro('first_name'), last_editable: !ro('last_name'), username_readonly: ro('username')};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    dialog, listed, edited, profile = (by.get(k) or {} for k in ("新建弹窗", "名单显示姓名", "编辑改名", "我的资料"))
    return [
        {"case": "new member dialog has a required first name and a last name", "seen": dialog,
         "ok": {"first_name", "last_name", "username"} <= set(dialog.get("fields") or []) and dialog.get("first_reqd") is True},
        {"case": "member list shows the full name and the login name", "seen": listed,
         "ok": listed.get("display") == f"{params['first']} {params['last']}" and listed.get("username") == params["username"]},
        {"case": "editing the name updates the list", "seen": edited, "ok": edited.get("display") == f"{params['renamed']} {params['last']}"},
        {"case": "my profile has editable first and last name and a read only username", "seen": profile,
         "ok": all(profile.get(k) is True for k in ("first_visible", "last_visible", "username_visible", "first_editable",
                                                       "last_editable", "username_readonly"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
row = frappe.db.get_value("User", {{"username": {params['username']!r}}}, ["name", "username", "first_name", "last_name", "full_name"], as_dict=True) or {{}}
if row.get("name"):
    # 只在实验室：建的员工停用（删用户要改一串关联表，几条流程并行时会撞锁）
    frappe.db.set_value("User", row["name"], "enabled", 0, update_modified=False)
    frappe.db.commit()
print(json.dumps(row, default=str))
""")
    return [{"case": "user saved with the first, last and full name and the login name", "saved": got,
             "ok": got.get("username") == params["username"] and got.get("first_name") == params["renamed"]
             and got.get("last_name") == params["last"] and got.get("full_name") == f"{params['renamed']} {params['last']}"}]
