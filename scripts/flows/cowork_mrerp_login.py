"""流程用例：MR.ERP 连接的登录在 CC 桌面版里授权（ADR-026 第 7 条）——用户名、密码只在 CC 桌面版的「连接 MR.ERP」弹窗里输
（11-23 用户「第一个弹窗不行吗」），浏览器里两格不可用。

每套 Cowork 数据：
1. 造数据（产品自己的函数，不连 MR.ERP）：一条 MR.ERP 连接（网页老做法存的账号密码 `flow-old`，即「老连接」）、合成账套目录、
   从目录建一家公司，再把连接停用——这家公司就没有可用账套，「新建任务」走不带公司种子的表单，MR.ERP 卡片出「连接」；
   另建一条 Express 连接并给一台合成电脑发密钥（发票要认得出电脑，同 CC 桌面版登记）。
2. CC 对话页 `/assistant?view=tasks&company=<公司>`：点「新建任务」→ MR.ERP 卡片的「连接」→ 打开设置 → 连接（11-07 起 MR.ERP 连接只在
   这里），账本组第一行「MR.ERP」名下写授权人 flow-old、右边「● 已连接 + ···」→「···」→「重新登录」→ 弹窗里有用户名、密码两格（整页只有这一个密码框），
   在浏览器里（没有 CC 本机桥）两格都不可用；改系统地址、点「连接」→ 弹窗关上。
3. 页面会话向站点要一次性票（CC 窗口里点授权时同一条接口，带合成电脑的设备密钥）：拿得到、形状对。
4. Cowork Desk：录入、自动录入规则两处共用的 `intake_targets.connect_dialog` 不再自己弹窗，新开一页 `/assistant?settings=connections`；
   打开这个地址就是设置 → 连接，有 MR.ERP 一行。
落库核对：网址改了、连接启用、**密码仍是老连接存的那个**（页面没有收、也没有改密码）、没有授权记录；最后把造的数据全部删掉。
机器通道交密码那一半（CC 桌面版经机器令牌交给站点）回归实验室没有 CC 桌面版，由 scripts/cowork-mrerp-login-lab-verify.py 在真实站点
走 WSGI 验，离线由 tests/test_mrerp_cc_login.py、tests/test_cowork_mrerp_cc_login.py 守住。
"""
from __future__ import annotations

import json
import uuid

TITLE = "Cowork MR.ERP 连接只在 CC 设置 → 连接：定时任务与 Desk 的「连接」都到这里；网页没有密码框、授权只在 CC 桌面版里；网址照存、老连接的密码不动"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
LABEL, EXPRESS_LABEL = "Flow MR.ERP login", "Flow MR.ERP login Express"
USER = "admin@cowork.regression"
OLD_USER, OLD_PASSWORD = "flow-old", "flow-old-password"
NEW_URL = "https://www.mrerp4sme.com/flow"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const q=s=>document.querySelector(s); const qa=s=>[...document.querySelectorAll(s)];"
        "const shown=e=>!!(e&&e.getClientRects().length);"
        "const passwords=()=>qa('input[type=password]').filter(shown).length;"
        # Radix 下拉只认指针按下：按真实点按的事件序列发（同 cc_settings.py）
        "const press=e=>{const o={bubbles:true,cancelable:true,pointerType:'mouse',isPrimary:true,button:0}; "
        "e.dispatchEvent(new PointerEvent('pointerdown',o)); e.dispatchEvent(new MouseEvent('mousedown',o)); "
        "e.dispatchEvent(new PointerEvent('pointerup',o)); e.dispatchEvent(new MouseEvent('mouseup',o)); e.click();};")

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
conn = connections.configure(ws, "mrerp", uuid4().hex, {LABEL!r}, {{}}, users=[{USER!r}])
# 老连接：第 10 步以前网页存下的账号密码。网页接口已不收 MR.ERP 密码（ADR-026 第 7 条），实验栈专用的路：站点里直接写这两格
from pearnly_cowork.connections import access as connection_access
old = frappe.get_doc("Cowork ERP Connection", conn["name"])
old.set("credential_username", {OLD_USER!r})
old.set("credential_password", {OLD_PASSWORD!r})
connection_access.write(old)
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
         # 打开设置 → 连接（定时任务的新建表单留在后面）；账本组第一行是 MR.ERP（站点 → 账本 → 电脑，用户 11-25 审定）：
         # 授权过的名下只写授权人、右边「● 已连接 + ···」，「···」里「重新登录」打开连接弹窗
         "const row=()=>q('[data-slot=\"cc-mrerp-row\"]'); "
         "const settled=await until(()=>row()&&row().querySelector('[data-slot=\"cc-mrerp-status\"]')?.innerText.trim(),80); "
         "const status=row()?row().querySelector('[data-slot=\"cc-mrerp-status\"]')?.innerText.trim()||'':''; "
         "const online=row()?row().querySelector('.cc-status-line[data-status=\"online\"]')?.innerText.trim()||'':''; "
         "const prev=row()?.previousElementSibling; const first=!!prev&&prev.tagName==='H3'; const group=prev?prev.innerText.trim():''; "
         "const more=row()?.querySelector('[data-slot=\"cc-mrerp-more\"]'); if(more){press(more); "
         "await until(()=>qa('[role=menuitem][data-slot=\"cc-mrerp-connect\"]').some(shown),40); "
         "const item=qa('[role=menuitem][data-slot=\"cc-mrerp-connect\"]').find(shown); if(item) press(item);} "
         "const opened=await until(()=>q('[data-slot=\"cc-mrerp-connect-dialog\"] [data-slot=\"cc-mrerp-login\"]'),60); "
         "const box=q('[data-slot=\"cc-mrerp-connect-dialog\"]'); const user=box?.querySelector('[data-slot=\"cc-mrerp-username\"]'); "
         "const secret=box?.querySelector('[data-slot=\"cc-mrerp-password\"]'); "
         "const url=box?.querySelector('[data-slot=\"cc-mrerp-url\"]'); "
         "return {settled, first, group, status, online, more: !!more, opened, passwords: passwords(), login: box?.querySelector('[data-slot=\"cc-mrerp-login\"]')?.innerText||'', "
         "login_disabled: !!(user&&user.disabled&&secret&&secret.disabled&&secret.type==='password'), url: url?url.value:''};"},
        {"name": "改网址、点连接", "js": WAIT +
         "const box=q('[data-slot=\"cc-mrerp-connect-dialog\"]'); const url=box?.querySelector('[data-slot=\"cc-mrerp-url\"]'); if(!url) return {error:'no url input'}; "
         "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(url," + json.dumps(NEW_URL) + "); "
         "url.dispatchEvent(new Event('input',{bubbles:true})); await new Promise(r=>setTimeout(r,300)); "
         "const save=box.querySelector('.cc-dialog-foot button[data-primary]'); if(!save) return {error:'no connect button in the dialog'}; save.click(); "
         "const closed=await until(()=>!q('[data-slot=\"cc-mrerp-connect-dialog\"]'),80); return {closed, passwords: passwords(), "
         "row: !!q('[data-slot=\"cc-mrerp-row\"]')};"},
        {"name": "页面要一次性票", "js": WAIT +
         "const body=new URLSearchParams({connection:" + json.dumps(connection) + ",device_key:" + json.dumps(key) + "}); "
         "const r=await fetch('/api/method/pearnly_cowork.companion.api.mrerp_login_ticket',{method:'POST',"
         "headers:{'X-Frappe-CSRF-Token':window.csrf_token||'','Content-Type':'application/x-www-form-urlencoded'},body}); "
         "const m=(await r.json()).message||{}; return {status: r.status, ok: m.ok===true, shape: /^mrl_[A-Za-z0-9_-]{32,64}$/.test(m.ticket||''), "
         "expires: m.expires_in};"},
        {"name": "打开 Desk", "js": "location.href='/desk/pearnly-home'; return 1;", "wait_ms": 6000},
        {"name": "Desk 的连接", "js": WAIT +
         "await until(()=>window.frappe&&frappe.pearnly&&frappe.pearnly.intake_targets,80); "
         "let opened=''; const real=window.open; window.open=(u)=>{opened=String(u); return null;}; "
         "const d=frappe.pearnly.intake_targets.connect_dialog({connection: null, on_done: async()=>{}}); window.open=real; "
         "return {opened, dialog: !!d, modals: document.querySelectorAll('.modal.show').length};"},
        {"name": "打开设置的连接一节", "js": "location.href='/assistant?settings=connections'; return 1;", "wait_ms": 6000},
        {"name": "设置的连接一节", "js": WAIT +
         "const ok=await until(()=>q('[data-slot=\"cc-mrerp-row\"]'),80); "
         "return {ok, search: location.search, nav: q('[role=\"dialog\"] .cc-nav-item[data-active]')?.innerText.trim()||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    dialog, saved, ticket, desk, settings = (by.get(k) or {} for k in ("新建任务、点 MR.ERP 的连接", "改网址、点连接", "页面要一次性票",
                                                                      "Desk 的连接", "设置的连接一节"))
    return [
        {"case": "定时任务的 MR.ERP「连接」打开设置 → 连接，账本组第一行 MR.ERP 名下写授权人 flow-old、右边「● 已连接 + ···」",
         "seen": dialog, "ok": bool(dialog.get("settled")) and bool(dialog.get("first")) and dialog.get("group") == "账本"
         and dialog.get("status") == OLD_USER and dialog.get("online") == "已连接" and dialog.get("more") is True},
        {"case": "这一行「···」→「重新登录」弹窗打开、用户名与密码两格在浏览器里不可用（整页只有这一个密码框）", "seen": dialog,
         "ok": bool(dialog.get("opened")) and dialog.get("passwords") == 1 and dialog.get("login_disabled") is True},
        {"case": "改网址点连接后弹窗关上，设置里那一行还在", "seen": saved, "ok": bool(saved.get("closed")) and bool(saved.get("row"))},
        {"case": "页面会话拿得到一次性票", "seen": ticket,
         "ok": ticket.get("status") == 200 and ticket.get("ok") is True and ticket.get("shape") is True},
        {"case": "Desk 的 MR.ERP「连接」新开一页到 CC 设置的连接一节，不再自己弹窗", "seen": desk,
         "ok": desk.get("opened") == "/assistant?settings=connections" and not desk.get("dialog") and desk.get("modals") == 0},
        {"case": "打开 /assistant?settings=connections 就是设置 → 连接（有 MR.ERP 一行），地址里的参数去掉", "seen": settings,
         "ok": bool(settings.get("ok")) and "settings=" not in str(settings.get("search"))},
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
