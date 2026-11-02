"""流程用例：CC「实操」工作区 `/assistant`（代录 Express 的任务页，网页上没有 CC 桌面版的桥）——切到实操、选公司就建任务、
右侧栏列出这家这个月等着录的单（与站点 `operator.sources` 一致）、在任务页底部输入框说一句（任务对话，实验室假智能体后端）、
对话框里的工作状态行在等回复时出现、回复画在任务记录下面、侧栏「···」删除任务（先问一次）、落库与删除后的清理。

模型调用换成实验室的假智能体服务（`scripts/flows/lab_agents.py`）：回复是「[lab-agent] + 用户原文前 40 个字」，断言的是收发与落库。
网页上没有桥：不开始录单（开始要执行的电脑在线，录单在 CC 桌面版里，另有离线测试 `tests/test_cc_operator.py` 与实机验收）。
"""
from __future__ import annotations

import json
import uuid

TITLE = "CC 实操：选公司建任务、右栏资料、任务对话一轮与状态行、删除任务（假智能体后端）"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
REPLY = "[lab-agent]"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const q=s=>document.querySelector(s); const qa=s=>[...document.querySelectorAll(s)];"
        "const say=async t=>{const ta=q('.cc-op-foot textarea[aria-label]'); "
        "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,t); ta.dispatchEvent(new Event('input',{bubbles:true})); "
        "await new Promise(r=>setTimeout(r,300)); q('.cc-op-foot button[aria-label=\"发送\"]').click();};"
        "const api=async(m,args)=>{const r=await fetch('/api/method/pearnly_core.express_operator.api.'+m+'?'+new URLSearchParams(args||{}),"
        "{headers:{'X-Frappe-CSRF-Token':window.csrf_token||''}}); return (await r.json()).message;};"
        # 下拉菜单（Radix）按下就开，单发 click 不开
        "const press=e=>{e.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'})); e.click();};")


def prepare(lab, manifest):
    lab.python(manifest["site"], 'frappe.db.set_value("User", "admin@erp.regression", "language", "zh")\nfrappe.db.commit()\nprint("{}")')
    company = manifest.get("company") or manifest["companies"][0]
    out = lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
from pearnly_erp_portal.express import contracts, operator, push
frappe.set_user("admin@erp.regression")
period = frappe.utils.add_months(frappe.utils.today(), -1)[:7]
first, last = operator._month(period)
# 这家这个月已提交、还没交进写账队列的一张采购单：临时标成「电脑离线、停下」（真实的停下状态之一），它就该出现在资料里；verify 还原
marked = frappe.get_all(push.PURCHASE, filters={{"company": {company!r}, "status": "submitted", "doc_date": ["between", [first, last]],
                                                 "ledger_state": ["in", ["", None]], "ledger_job": ["is", "not set"]}},
                        pluck="name", order_by="name asc", limit_page_length=1)
for name in marked:
    frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": contracts.HELD, "ledger_reason": contracts.OFFLINE}}, update_modified=False)
frappe.db.commit()
docs = operator.sources({company!r}, period, 0, 200)["documents"]
print(json.dumps({{"title": frappe.db.get_value("ERP Company", {company!r}, "company_name") or {company!r}, "period": period,
                  "marked": marked, "refs": [d["ref"] for d in docs],
                  "docs": len(docs), "tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": "admin@erp.regression"}}),
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"user": "admin@erp.regression"}})}}))
""")
    tag = "flow" + uuid.uuid4().hex[:6]
    return {**out, "company": company, "tag": tag, "line": f"{tag} 这次先录采购", "cases": []}


def steps(params, camera_code=None):
    title, line = params["title"], params["line"]
    return [
        {"name": "切到实操", "js": WAIT + "q('[data-mode=\"operator\"]').click(); "
         "const ok=await until(()=>q('[data-slot=\"cc-chin-company\"]')); return {ok, new: !!q('[data-slot=\"cc-op-new\"]')};"},
        {"name": "选公司建任务", "js": WAIT + "press(q('[data-slot=\"cc-chin-company\"]')); "
         f"await until(()=>qa('[role=\"menuitem\"]').some(e=>e.innerText.includes({json.dumps(title)})),60); "
         f"const item=qa('[role=\"menuitem\"]').find(e=>e.innerText.includes({json.dumps(title)})); if(!item) return {{error:'company not listed', menu: qa('[role=\"menuitem\"]').map(e=>e.innerText).slice(0,10)}}; "
         "item.click(); const ok=await until(()=>q('[data-slot=\"cc-op-title\"]')&&q('[data-slot=\"cc-op-panel\"]'),80); "
         "const list=await api('task_list',{start:0,limit:5}); const task=(list.tasks||[])[0]||{}; "
         f"await until(()=>q(`[data-row-id=\"${{task.name}}\"]`),40); "
         "return {ok, title: q('[data-slot=\"cc-op-title\"]')?.innerText.trim(), task: task.name, status: task.status, "
         "row: !!q(`[data-slot=\"cc-operator-sidebar\"] [data-row-id=\"${task.name}\"]`), panel: q('[data-slot=\"cc-op-panel\"]')?.innerText.slice(0,400)};"},
        {"name": "资料", "js": WAIT + "await until(()=>q('[data-slot=\"cc-op-panel\"] [data-section=\"files\"]'),60); "
         "await new Promise(r=>setTimeout(r,1500)); const rows=qa('[data-slot=\"cc-op-row\"]').length; "
         "return {rows, files: !!q('[data-section=\"files\"]'), meta: q('.cc-op-meta')?.innerText.trim()||'', "
         "text: qa('[data-slot=\"cc-op-row\"]').map(e=>e.innerText).join('\\n').slice(0,1500)};"},
        {"name": "任务对话", "js": WAIT + f"await say({json.dumps(line)}); let line=false; "
         f"const got=await until(()=>{{ if(q('.cc-op-log [data-slot=\"cc-run-indicator\"]')) line=true; "
         f"return qa('.cc-op-log [data-role=\"assistant\"]').some(e=>e.innerText.includes({json.dumps(REPLY)})); }},160); "
         "await until(()=>!q('.cc-op-log [data-slot=\"cc-run-indicator\"]'),40); "
         "return {got, line, after: qa('.cc-op-log [data-slot=\"cc-run-indicator\"]').length, user: qa('.cc-op-log [data-role=\"user\"]').map(e=>e.innerText.trim()), "
         "assistant: qa('.cc-op-log [data-role=\"assistant\"]').map(e=>e.innerText.trim().slice(0,120))};"},
        {"name": "删除任务", "js": WAIT + "const list=await api('task_list',{start:0,limit:5}); const name=((list.tasks||[])[0]||{}).name; "
         "const row=q(`[data-row-id=\"${name}\"]`); if(!row) return {error:'row missing', name}; "
         "row.dispatchEvent(new PointerEvent('pointerover',{bubbles:true})); const more=row.querySelector('[data-slot=\"aui_thread-list-item-more\"]'); "
         "if(!more) return {error:'no menu button'}; press(more); "
         "await until(()=>q('[data-slot=\"aui_thread-list-item-more-item\"][data-danger]'),40); "
         "const del=q('[data-slot=\"aui_thread-list-item-more-item\"][data-danger]'); if(!del) return {error:'no delete item'}; del.click(); "
         "const asked=await until(()=>q('[data-slot=\"cc-delete-chats-yes\"]'),40); if(!asked) return {error:'not asked'}; "
         "q('[data-slot=\"cc-delete-chats-yes\"]').click(); const gone=await until(()=>!q(`[data-row-id=\"${name}\"]`),80); "
         "return {name, asked, gone};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    switch, made, files, talk, removed = (by.get(k) or {} for k in ("切到实操", "选公司建任务", "资料", "任务对话", "删除任务"))
    want_title = params["title"]
    return [
        {"case": "切到实操：新建页（选公司的下巴）", "seen": switch, "ok": bool(switch.get("ok")) and bool(switch.get("new"))},
        {"case": "选了公司就建一个准备中的任务：标题是公司、侧栏有这一行、右侧栏开着", "seen": made,
         "ok": bool(made.get("ok")) and want_title in (made.get("title") or "") and made.get("status") == "preparing" and bool(made.get("row"))},
        {"case": "右侧栏资料与站点这家这个月等着录的单一致（单号逐张在任务记录里）", "seen": {**files, "site": params["docs"], "refs": params["refs"]},
         "ok": bool(files.get("files")) and files.get("rows") == params["docs"]
         and all(ref in (files.get("text") or "") for ref in params["refs"])},
        {"case": "任务页说一句：交给任务对话、回复画在任务记录下面", "seen": talk,
         "ok": bool(talk.get("got")) and params["line"] in (talk.get("user") or [])},
        {"case": "等回复时对话框里出工作状态行，回复到了就收起", "seen": {k: talk.get(k) for k in ("line", "after")},
         "ok": bool(talk.get("line")) and talk.get("after") == 0},
        {"case": "侧栏「···」删除：先问一次，确认后这一行消失", "seen": removed, "ok": bool(removed.get("asked")) and bool(removed.get("gone"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
from pearnly_erp_portal.express import push
for name in {params["marked"]!r}:
    frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": "", "ledger_reason": ""}}, update_modified=False)
frappe.db.commit()
print(json.dumps({{"tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": "admin@erp.regression"}}),
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"user": "admin@erp.regression"}}),
                  "tagged": frappe.db.count("Pearnly Assistant Session", {{"user": "admin@erp.regression", "title": ["like", "%{params['tag']}%"]}})}}))
""")
    return [
        {"case": "删除后任务记录回到流程前的条数（建过一条、删掉了）", "saved": {"before": params["tasks"], "after": rows["tasks"]},
         "ok": rows["tasks"] == params["tasks"]},
        {"case": "任务对话随任务一起删掉、不进对话列表", "saved": {"before": params["sessions"], "after": rows["sessions"], "tagged": rows["tagged"]},
         "ok": rows["sessions"] == params["sessions"] and rows["tagged"] == 0},
    ]
