"""流程用例：CC 里这个人显示成姓名（用户 10-30：个人信息加姓名，显示用户名的地方改完必须全部覆盖，包括 CC）。

管理员先在「我的资料」那条路（原生 User 保存）填好名、姓。打开 CC：空对话的问候语带「名」（问候取全名第一段），
电脑宽度侧栏底部的用户按钮、手机宽度抽屉头像的无障碍标签都是全名（共用显示名 `pearnly_core.account.names`，经 `shell.describe`）。
跑完把名还原成登录名、姓清空（同存量账号的样子）。
"""
from __future__ import annotations

import uuid

TITLE = "CC 问候语与头像显示姓名"
USER = "admin@erp.regression"
WAIT = "const until=async(f,n=80)=>{for(let k=0;k<n;k++){try{const v=f(); if(v) return v;}catch(e){} await new Promise(r=>setTimeout(r,250));} return null;};"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:4]
    first, last = "สมชาย" + tag, "ใจดี"
    before = lab.python(manifest["site"], f"""
doc = frappe.get_doc("User", {USER!r})
before = {{"first_name": doc.first_name, "last_name": doc.last_name}}
doc.first_name, doc.last_name = {first!r}, {last!r}
doc.save(ignore_permissions=True)
frappe.db.commit()
print(json.dumps(before))
""")
    return {"first": first, "last": last, "before": before}


def steps(params, camera_code=None):
    return [{"name": "问候与头像", "js": WAIT + """
await until(()=>document.readyState==='complete' && document.querySelector('h1.cc-greeting'));
const hello=(document.querySelector('h1.cc-greeting')||{}).innerText||'';
const shown=e=>!!(e&&e.getClientRects().length);
const labels=[...document.querySelectorAll('button[aria-label]')].filter(shown).map(b=>b.getAttribute('aria-label'));
const texts=[...document.querySelectorAll('button')].filter(shown).map(b=>b.innerText.trim()).filter(Boolean);
return {hello, labels, texts};"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "问候与头像"), None) or {}
    full = f"{params['first']} {params['last']}"
    return [
        {"case": "the greeting carries the first name", "seen": seen.get("hello"), "ok": params["first"] in str(seen.get("hello") or "")},
        {"case": "the user button or avatar shows the full name", "seen": {"labels": seen.get("labels"), "texts": seen.get("texts")},
         # 电脑宽度：侧栏底部用户按钮的文字（头像缩写 + 全名 · 产品）；手机宽度：抽屉头像的无障碍标签
         "ok": full in (seen.get("labels") or []) or any(full in text for text in seen.get("texts") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
doc = frappe.get_doc("User", {USER!r})
seen = {{"full_name": doc.full_name}}
doc.first_name, doc.last_name = {params['before'].get('first_name') or doc.username or 'admin'!r}, {params['before'].get('last_name') or ''!r}
doc.save(ignore_permissions=True)  # 还原
frappe.db.commit()
print(json.dumps(seen))
""")
    return [{"case": "the user's full name is the first and last name", "seen": got,
             "ok": got.get("full_name") == f"{params['first']} {params['last']}"}]
