"""流程用例：「我的资料」（框架原生 User 表单）改名后点页头「保存」，库里跟着变（ERP、Cowork 各一条，同一模块；清存量基线 11-08）。

岗位员工打开自己的资料：名改成新的（在名那一格里输入）→ 页头「保存」→ 表单不再是未保存、页头标题跟着变 → 库里 `User.first_name` 是新的、
`full_name` 跟着拼好。cleanup 把名改回原来的（实验室，直接写库）。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "我的资料：改名后页头保存，库里的姓名跟着变"


def _who(manifest):
    return "intake@cowork.regression" if manifest["site"].startswith("cowork") else "purchase@erp.regression"


def prepare(lab, manifest):
    user = _who(manifest)
    row = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("User", {user!r}, ["first_name", "last_name"], as_dict=True) or {{}}))
""")
    return {"user": user, "first": row.get("first_name") or "", "last": row.get("last_name") or "",
            "renamed": "Flow" + uuid.uuid4().hex[:6], "cases": []}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
doc = frappe.get_doc("User", {params['user']!r})
doc.first_name = {params['first']!r}
doc.flags.ignore_permissions = True
doc.save()
frappe.db.commit()
print(json.dumps({{}}))
""")


def steps(params, camera_code=None):
    return [{"name": "改名保存", "js": JS + ACTION + f"""
frappe.set_route('Form','User',frappe.session.user);
const frm=await until(()=>window.cur_frm&&cur_frm.doctype==='User'&&cur_frm.docname===frappe.session.user&&cur_frm.fields_dict.first_name?.$input&&cur_frm, 25000);
await idle(); if(!frm) return {{error:'no profile form'}};
const input=frm.fields_dict.first_name.$input[0]; setInput(input, {js_value(params['renamed'])}); await sleep(400);
const dirty=!!frm.is_dirty();
const via=await action(__('Save')); if(!via) return {{error:'no Save button'}};
const saved=await until(()=>!frm.is_dirty()&&frm.doc.first_name==={js_value(params['renamed'])}&&$.active===0, 20000); await idle();
return {{dirty, via, saved: !!saved, msg: msg()}};"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "改名保存"), None) or {}
    return [{"case": "typing the name dirties the form and the head Save button saves it", "seen": seen,
             "ok": seen.get("dirty") is True and bool(seen.get("via")) and seen.get("saved") is True and not seen.get("msg")}]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
print(json.dumps(frappe.db.get_value("User", {params['user']!r}, ["first_name", "full_name"], as_dict=True) or {{}}))
""")
    return [{"case": "the user's first name and full name are saved", "saved": got,
             "ok": got.get("first_name") == params["renamed"] and (got.get("full_name") or "").startswith(params["renamed"])}]
