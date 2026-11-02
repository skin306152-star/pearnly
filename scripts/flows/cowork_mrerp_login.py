"""流程用例：MR.ERP 连接的登录在 CC 桌面版里授权（ADR-026 第 7 条）——网页上不再有 MR.ERP 密码框。

每套 Cowork 数据：
1. 造数据（产品自己的函数，不连 MR.ERP）：一条 MR.ERP 连接（网页老做法存的账号密码 `flow-old`，即「老连接」）、合成账套目录、
   从目录建一家公司，再把连接停用——这家公司就没有可用账套，「新建任务」走不带公司种子的表单，MR.ERP 卡片出「连接」；
   另建一条 Express 连接并给一台合成电脑发密钥（发票要认得出电脑，同 CC 桌面版登记）。
2. CC 对话页 `/assistant?view=tasks&company=<公司>`：点「新建任务」→ MR.ERP 卡片的「连接」→ 弹窗里**没有任何密码框**，显示
   「已授权：flow-old」，「在 CC 桌面版中授权」按钮在浏览器里不可用；改系统网址、点「连接」→ 弹窗关上。
3. 页面会话向站点要一次性票（CC 窗口里点授权时同一条接口，带合成电脑的设备密钥）：拿得到、形状对。
4. Cowork Desk：同一个连接弹窗（录入、定时任务两处共用的 `intake_targets.connect_dialog`）里同样没有密码字段、显示授权状态、
   授权按钮不可用。
落库核对：网址改了、连接启用、**密码仍是老连接存的那个**（页面没有收、也没有改密码）、没有授权记录；最后把造的数据全部删掉。
机器通道交密码那一半（CC 桌面版经机器令牌交给站点）回归实验室没有 CC 桌面版，由 scripts/cowork-mrerp-login-lab-verify.py 在真实站点
走 WSGI 验，离线由 tests/test_mrerp_cc_login.py、tests/test_cowork_mrerp_cc_login.py 守住。
"""
from __future__ import annotations

import json
import uuid

TITLE = "Cowork MR.ERP 登录：网页没有密码框、只显示授权状态，授权只在 CC 桌面版里；网址照存、老连接的密码不动"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
LABEL, EXPRESS_LABEL = "Flow MR.ERP login", "Flow MR.ERP login Express"
USER = "admin@cowork.regression"
OLD_USER, OLD_PASSWORD = "flow-old", "flow-old-password"
NEW_URL = "https://www.mrerp4sme.com/flow"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const q=s=>document.querySelector(s); const qa=s=>[...document.querySelectorAll(s)];"
        "const shown=e=>!!(e&&e.getClientRects().length);"
        "const passwords=()=>qa('input[type=password]').filter(shown).length;")

CLEANUP = f'''
from pearnly_cowork.companies import service as companies
def cleanup():
    frappe.set_user({USER!r})
    gone = 0
    # 页面「连接」会把连接名改成 MR.ERP：按标签找，再按流程写进去的网址找
    names = set(frappe.get_all("Cowork ERP Connection", filters={{"label": ["in", [{LABEL!r}, {EXPRESS_LABEL!r}]]}}, pluck="name",
                               limit_page_length=20))
    names |= set(frappe.get_all("Cowork ERP Connection", filters={{"adapter": "mrerp", "config_json": ["like", "%{NEW_URL}%"]}},
                                pluck="name", limit_page_length=20))
    for c in sorted(names):
        for company in frappe.get_all("Cowork Company", filters={{"seed_connection": c}}, pluck="name", limit_page_length=20):
            companies.delete(company)
            frappe.db.delete("Cowork Company Choice", {{"company": company}})
        devices = frappe.get_all("Cowork Express Agent Credential", filters={{"connection": c}}, pluck="device", limit_page_length=20)
        for dt in ("Cowork ERP Refresh", "Cowork ERP Snapshot", "Cowork Express Agent Credential"):
            frappe.db.delete(dt, {{"connection": c}})
        for device in [d for d in devices if d]:
            frappe.db.delete("Cowork CC Desktop Device", {{"name": device}})
        frappe.db.delete("Cowork ERP Connection", {{"name": c}})
        gone += 1
    frappe.db.commit()
    return gone
'''


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    key = "flow" + uuid.uuid4().hex + "x" * 3  # 设备密钥：39 个以上 [A-Za-z0-9_-]
    out = lab.python(site, CLEANUP + f"""
from uuid import uuid4
from pearnly_cowork.access import scope
from pearnly_cowork.companion import credentials
from pearnly_cowork.connections import service as connections
cleanup()
frappe.set_user({USER!r})
others = frappe.db.count("Cowork ERP Connection", {{"adapter": "mrerp"}})
ws = scope.existing_workspace()
conn = connections.configure(ws, "mrerp", uuid4().hex, {LABEL!r}, {{}}, users=[{USER!r}],
                             credentials={{"username": {OLD_USER!r}, "password": {OLD_PASSWORD!r}}})
refresh = connections.request_refresh(conn["name"], uuid4().hex)
observation = {{"adapter": "mrerp", "observed_at": "2026-10-01T00:00:00Z", "collector": {{"kind": "synthetic_lab"}},
    "account_sets": [{{"source_id": "88:1", "label": "FLOW2026", "attributes": {{"company": "FLOWCO", "company_name": "Flow MR.ERP Co",
        "comidyear": "88", "seldb": "1", "period_start": "2026-01-01", "period_end": "2026-12-31"}}}}]}}
connections.install_snapshot(conn["name"], observation, refresh["request_id"], conn["revision"], None)
company = companies.create(conn["name"], "88:1")
frappe.db.commit()
conn = connections.configure(ws, "mrerp", uuid4().hex, {LABEL!r}, {{}}, name=conn["name"], expected_revision=conn["revision"],
                             enabled=False)
express = connections.configure(ws, "express", uuid4().hex, {EXPRESS_LABEL!r}, {{}}, users=[{USER!r}])
issued = credentials._register_device(express["name"], "express", {USER!r}, uuid4().hex, {key!r}, "FLOW-PC", False)
frappe.db.commit()
print(json.dumps({{"connection": conn["name"], "revision": conn["revision"], "company": company["id"], "device": issued["device"],
                  "others": others}}))
""")
    if out["others"]:
        raise RuntimeError(f"this dataset already has an MR.ERP connection; the flow needs its own: {out}")
    return {**out, "device_key": key, "cases": []}


def steps(params, camera_code=None):
    company, connection, key = params["company"], params["connection"], params["device_key"]
    return [
        {"name": "打开这家公司的定时任务", "js": WAIT +
         f"location.href='/assistant?view=tasks&company={company}'; return 1;", "wait_ms": 5000},
        {"name": "新建任务、点 MR.ERP 的连接", "js": WAIT +
         "await until(()=>q('.cc-page-head button[data-primary]')); q('.cc-page-head button[data-primary]').click(); "
         "const card=()=>qa('.cc-card').filter(shown).find(c=>c.innerText.includes('MR.ERP')); "
         "const ok=await until(()=>card()&&card().querySelector('button.cc-btn'),80); if(!ok) return {error:'no MR.ERP connect button'}; "
         "card().querySelector('button.cc-btn').click(); "
         "const opened=await until(()=>q('[data-slot=\"cc-mrerp-login\"]'),60); "
         "const login=q('[data-slot=\"cc-mrerp-login\"]'); const button=q('[data-slot=\"cc-mrerp-authorize\"]'); "
         "const url=qa('input.cc-input').filter(shown).find(e=>/^https?:/.test(e.value)); "
         "return {opened, passwords: passwords(), status: login?login.innerText:'', authorize_disabled: !!(button&&button.disabled), "
         "url: url?url.value:''};"},
        {"name": "改网址、点连接", "js": WAIT +
         "const url=qa('input.cc-input').filter(shown).find(e=>/^https?:/.test(e.value)); if(!url) return {error:'no url input'}; "
         "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(url," + json.dumps(NEW_URL) + "); "
         "url.dispatchEvent(new Event('input',{bubbles:true})); await new Promise(r=>setTimeout(r,300)); "
         "const box=q('[data-slot=\"cc-mrerp-login\"]').closest('[role=dialog]'); const save=box&&box.querySelector('button[data-primary]'); "
         "if(!save) return {error:'no connect button in the dialog'}; save.click(); "
         "const closed=await until(()=>!q('[data-slot=\"cc-mrerp-login\"]'),80); return {closed, passwords: passwords()};"},
        {"name": "页面要一次性票", "js": WAIT +
         "const body=new URLSearchParams({connection:" + json.dumps(connection) + ",device_key:" + json.dumps(key) + "}); "
         "const r=await fetch('/api/method/pearnly_cowork.companion.api.mrerp_login_ticket',{method:'POST',"
         "headers:{'X-Frappe-CSRF-Token':window.csrf_token||'','Content-Type':'application/x-www-form-urlencoded'},body}); "
         "const m=(await r.json()).message||{}; return {status: r.status, ok: m.ok===true, shape: /^mrl_[A-Za-z0-9_-]{32,64}$/.test(m.ticket||''), "
         "expires: m.expires_in};"},
        {"name": "打开 Desk", "js": "location.href='/desk/pearnly-home'; return 1;", "wait_ms": 6000},
        {"name": "Desk 的连接弹窗", "js": WAIT +
         "await until(()=>window.frappe&&frappe.pearnly&&frappe.pearnly.intake_targets,80); "
         "const rows=await frappe.xcall('pearnly_cowork.connections.api.records',{limit:100}); "
         "const connection=rows.find(r=>r.name===" + json.dumps(connection) + "); "
         "const d=frappe.pearnly.intake_targets.connect_dialog({connection, on_done: async()=>{}}); "
         "await until(()=>d.$wrapper.is(':visible'),40); const w=d.$wrapper; "
         "const out={passwords: w.find('input[type=password]').length, text: w.find('.modal-body').text(), "
         "authorize_disabled: w.find('[data-fieldname=authorize] button').prop('disabled')===true, "
         "url: w.find('[data-fieldname=system_url] input').val()}; d.hide(); return out;"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    dialog, saved, ticket, desk = (by.get(k) or {} for k in ("新建任务、点 MR.ERP 的连接", "改网址、点连接", "页面要一次性票",
                                                            "Desk 的连接弹窗"))
    return [
        {"case": "CC 页的 MR.ERP 连接弹窗打开、没有密码框", "seen": dialog,
         "ok": bool(dialog.get("opened")) and dialog.get("passwords") == 0},
        {"case": "显示「已授权：flow-old」、授权按钮在浏览器里不可用", "seen": dialog,
         "ok": "已授权：" + OLD_USER in str(dialog.get("status")) and dialog.get("authorize_disabled") is True},
        {"case": "改网址点连接后弹窗关上", "seen": saved, "ok": bool(saved.get("closed"))},
        {"case": "页面会话拿得到一次性票", "seen": ticket,
         "ok": ticket.get("status") == 200 and ticket.get("ok") is True and ticket.get("shape") is True},
        {"case": "Desk 连接弹窗没有密码字段、显示授权状态、授权按钮不可用", "seen": desk,
         "ok": desk.get("passwords") == 0 and "已授权：" + OLD_USER in str(desk.get("text")) and desk.get("authorize_disabled") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], CLEANUP + f"""
import json as _json
from frappe.utils.password import get_decrypted_password
name = {params['connection']!r}
row = frappe.db.get_value("Cowork ERP Connection", name, ["enabled", "config_json", "credential_authorized_by"], as_dict=True)
saved = {{"enabled": row.enabled, "url": _json.loads(row.config_json or "{{}}").get("system_url"),
         "username": get_decrypted_password("Cowork ERP Connection", name, "credential_username", raise_exception=False),
         "password_kept": get_decrypted_password("Cowork ERP Connection", name, "credential_password", raise_exception=False)
                          == {OLD_PASSWORD!r},
         "authorized_by": row.credential_authorized_by or ""}}
saved["cleaned"] = cleanup()
print(json.dumps(saved))
""")
    return [
        {"case": "网址照存、连接启用", "saved": out, "ok": out["url"] == NEW_URL and out["enabled"] == 1},
        {"case": "老连接的账号密码不动（网页没有收、也没有改密码），没有授权记录", "saved": out,
         "ok": out["username"] == OLD_USER and out["password_kept"] is True and out["authorized_by"] == ""},
        {"case": "造的数据删干净", "saved": out["cleaned"], "ok": out["cleaned"] == 2},
    ]
