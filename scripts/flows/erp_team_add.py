"""流程用例：ERP 团队管理「添加用户」邮箱留空（f1f7b02f，fcb65cca 逐入口实测）。

管理员在团队页按页面同一个接口加一个员工：邮箱留空 → 补成 `<登录名>.<站点前缀>@pearnly.com`，勾的岗位、指的公司都发上；
重新打开团队页列表里有这一行；乱填的邮箱被拒，同一登录名不能再加。库里：账号就是补出来的邮箱、用户名是登录名、启用。
（用登录名登录走 Keycloak，实验室没有，不在这里测。）
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, company, js_value  # noqa: E402

TITLE = "ERP 团队：添加用户邮箱留空补占位邮箱"
API = "pearnly_erp_portal.team.api."


def prepare(lab, manifest):
    login = "flow" + uuid.uuid4().hex[:6]
    return {"login": login, "email": f"{login}.{manifest['site'].split('.')[0]}@pearnly.com", "company": company(manifest)}


def steps(params, camera_code=None):
    login, co = js_value(params["login"]), js_value(params["company"])
    return [
        {"name": "添加用户", "js": JS + f"""
const catalog=(await call('{API}api_list_members', {{}}, 'GET')).roles||[];
const roles=catalog.filter(r=>/stock|products/.test(r.name)).map(r=>r.name).slice(0,2);
const quiet=args=>new Promise(resolve=>frappe.call({{method:'{API}api_add_member', args, type:'POST', callback:r=>resolve({{ok:true}}),
  error:()=>resolve({{ok:false}})}}).fail(()=>resolve({{ok:false}})));
await call('{API}api_add_member', {{username: {login}, email: '', password: 'Flow-' + {login}, roles, companies: [{co}]}});
const bad=await quiet({{username: {login} + 'x', email: 'not-an-email', password: 'Flow-x-9', roles}});
const dup=await quiet({{username: {login}, email: '', password: 'Flow-y-9', roles}});
all('.modal.show .btn-modal-close').forEach(b=>b.click());
return {{roles, bad_rejected: !bad.ok, dup_rejected: !dup.ok}};"""},
        {"name": "重开团队页", "js": JS + f"""
frappe.set_route('pearnly-home'); await sleep(800); frappe.set_route('pearnly-team');
const row=await until(()=>all('.list-row, .dt-row', pageOf('pearnly-team')||document).find(el=>el.innerText.includes({login})), 30000);
return {{listed: !!row, text: row ? row.innerText.replace(/\\s+/g,' ').slice(0,200) : ''}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    added, listed = by.get("添加用户", {}), by.get("重开团队页", {})
    params["roles"] = added.get("roles") or []
    return [{"case": "乱填邮箱被拒、重名被拒", "seen": added, "ok": added.get("bad_rejected") and added.get("dup_rejected") and len(params["roles"]) > 0},
            # 手机宽度的列表不显示邮箱列：页面只核这一行在，邮箱在库里核（verify）
            {"case": "团队页有这一行", "seen": listed, "ok": bool(listed.get("listed"))}]


def verify(lab, manifest, params, camera_code=None):
    row = lab.python(manifest["site"], f"""
user = frappe.db.get_value("User", {params['email']!r}, ["name", "username", "enabled"], as_dict=True) or {{}}
roles = frappe.get_all("Has Role", filters={{"parent": {params['email']!r}}}, pluck="role")
places = frappe.get_all("User Permission", filters={{"user": {params['email']!r}, "allow": "ERP Company"}}, pluck="for_value")
same = frappe.db.count("User", {{"username": {params['login']!r}}})
print(json.dumps({{"user": user, "roles": roles, "companies": places, "same_login": same}}))
""")
    user = row["user"]
    return [{"case": "账号与用户名", "seen": user, "ok": user.get("name") == params["email"] and user.get("username") == params["login"]
             and bool(user.get("enabled")) and row["same_login"] == 1},
            {"case": "岗位与公司", "seen": row, "ok": all(r in row["roles"] for r in params.get("roles", [])) and params["company"] in row["companies"]}]
