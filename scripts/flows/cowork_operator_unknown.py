"""流程用例：Cowork 的 CC「实操」（代录 Express）开始前确认里的「推送结果不明」——上次推送电脑领单途中断了（领单超时，
推送记录转人工、结果不明），选这家公司建任务时右侧栏「开始前确认」列出这张单与站点那一句，点「没有入账」后照复核页「核对」
同一条路放开（推送记录 manual / confirmed_not_entered、锁放掉、单据原因跟着改），再预检时它不再算结果不明。

执行到的服务端：`pearnly_core.express_operator.api`（documents / precheck / resolve / task_*）→ Cowork 的
`assistant.operator_source`（sources、precheck、resolve）→ `push.application.service.operator_check` 与 `resolve_unknown`
→ `push.lifecycle.result_unknown`（预检与核对两处判结果不明）。造数据见 `cowork_operator_lib.py`（产品自己的函数 + 实验室的合成
写入端，不连 Express）。网页上没有 CC 桌面版的桥：不开始录单。
"""
from __future__ import annotations

import json
import uuid

from cowork_operator_lib import BASE, CLEANUP

TITLE = "Cowork CC 实操：开始前确认列出推送结果不明的单，点「没有入账」放开（代录预检 / 核对，产品账本，网页无桥不开录）"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
API = "pearnly_core.express_operator.api."

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const q=s=>document.querySelector(s); const qa=s=>[...document.querySelectorAll(s)];"
        "const call=async(m,args,post)=>{const body=new URLSearchParams(args||{}); const r=await fetch('/api/method/" + API + "'+m+(post?'':'?'+body),"
        "{method:post?'POST':'GET',headers:{'X-Frappe-CSRF-Token':window.csrf_token||'','Content-Type':'application/x-www-form-urlencoded'},body:post?body:undefined});"
        " return (await r.json()).message;};")


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, 'frappe.db.set_value("User", "admin@cowork.regression", "language", "zh")\nfrappe.db.commit()\nprint("{}")')
    ref = "FLOW-UNK-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, CLEANUP + BASE + f"""
name = recognized({ref!r})
record = prepared(name)
claim = native.lease(record, context=context_of(record), adapter=writer, now=datetime.now(timezone.utc) - timedelta(hours=1))
frappe.db.commit()
# 领单后电脑断了：租约过期 → 转人工、结果不明（锁保留，不自动重试）
native.recover_expired(record, context=context_of(record), adapter=writer, now=datetime.now(timezone.utc))
frappe.db.commit()
row = frappe.db.get_value("Cowork Push Record", record, ["status", "result_unknown", "reason"], as_dict=True)
doc = frappe.db.get_value("Cowork OCR History", name, ["last_push_status", "last_push_reason", "last_push_record"], as_dict=True)
from pearnly_core.express_operator import tasks
frappe.local.lang = "zh"
print(json.dumps({{"company": company["id"], "title": company["title"], "period": period, "doc": name, "record": record,
                  "leased": bool(claim.get("leased")), "record_state": dict(row), "doc_state": dict(doc),
                  "message": frappe._("intake.error.ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"),
                  "docs": len(frappe.get_attr("pearnly_cowork.assistant.operator_source.sources")(company["id"], period, 0, 100)["documents"]),
                  "tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": "admin@cowork.regression"}})}}, default=str))
""")
    if not out["leased"] or out["record_state"].get("status") != "manual" or not out["record_state"].get("result_unknown"):
        raise RuntimeError(f"setup did not reach result unknown: {out}")
    return {**out, "ref": ref, "cases": []}


def steps(params, camera_code=None):
    title, ref = params["title"], params["ref"]
    unknown = f"qa('[data-slot=\"cc-op-unknown\"]').find(e=>e.innerText.includes({json.dumps(ref)}))"
    return [
        {"name": "切到实操", "js": WAIT + "q('[data-mode=\"operator\"]').click(); "
         "const ok=await until(()=>q('[data-slot=\"cc-chin-company\"]')); return {ok};"},
        {"name": "选公司建任务", "js": WAIT + "const chin=q('[data-slot=\"cc-chin-company\"]'); "
         "chin.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'})); "  # Radix 菜单按下就开
         "if(!await until(()=>chin.getAttribute('aria-expanded')==='true',20)) chin.click(); "
         f"await until(()=>qa('[role=\"menuitem\"]').some(e=>e.innerText.includes({json.dumps(title)})),60); "
         f"const item=qa('[role=\"menuitem\"]').find(e=>e.innerText.includes({json.dumps(title)})); "
         "if(!item) return {error:'company not listed', menu: qa('[role=\"menuitem\"]').map(e=>e.innerText).slice(0,10)}; "
         "item.click(); const ok=await until(()=>q('[data-slot=\"cc-op-title\"]')&&q('[data-slot=\"cc-op-panel\"]'),80); "
         "const list=await call('task_list',{start:0,limit:5}); const task=(list.tasks||[])[0]||{}; "
         "return {ok, title: q('[data-slot=\"cc-op-title\"]')?.innerText.trim(), task: task.name, status: task.status};"},
        {"name": "开始前确认", "js": WAIT + f"const seen=await until(()=>{unknown},120); const row={unknown}; "
         "await new Promise(r=>setTimeout(r,500)); "
         "return {seen, text: row?.innerText.trim()||'', rows: qa('[data-slot=\"cc-op-row\"]').length, "
         "unknown: qa('[data-slot=\"cc-op-unknown\"]').length, buttons: row?[...row.querySelectorAll('button')].map(b=>b.innerText.trim()):[], "
         "section: q('[data-section=\"confirm\"]')?.innerText.slice(0,300)||''};"},
        {"name": "没有入账", "js": WAIT + f"const row={unknown}; if(!row) return {{error:'no unknown row'}}; "
         "row.querySelector('[data-slot=\"cc-op-not-entered\"]').click(); "
         f"const gone=await until(()=>!({unknown}),120); await new Promise(r=>setTimeout(r,800)); "
         "return {gone, unknown: qa('[data-slot=\"cc-op-unknown\"]').length, section: q('[data-section=\"confirm\"]')?.innerText.slice(0,300)||''};"},
        {"name": "删除任务", "js": WAIT + "const list=await call('task_list',{start:0,limit:5}); const name=((list.tasks||[])[0]||{}).name; "
         "if(!name) return {error:'no task'}; await call('task_delete',{name},true); "
         "const after=await call('task_list',{start:0,limit:5}); return {name, left: (after.tasks||[]).some(t=>t.name===name)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    switch, made, ask, resolved, removed = (by.get(k) or {} for k in ("切到实操", "选公司建任务", "开始前确认", "没有入账", "删除任务"))
    text = ask.get("text") or ""
    return [
        {"case": "切到实操、选这家公司就建一个准备中的任务（标题是公司）", "seen": {**switch, **made},
         "ok": bool(switch.get("ok")) and bool(made.get("ok")) and params["title"] in (made.get("title") or "")
         and made.get("status") == "preparing"},
        {"case": "右侧栏资料与站点这家这个月等着录的单一致", "seen": {"rows": ask.get("rows"), "site": params["docs"]},
         "ok": ask.get("rows") == params["docs"]},
        {"case": "开始前确认列出推送结果不明的那张：单号、站点那一句、「已入账」「没有入账」两个按钮", "seen": ask,
         "ok": bool(ask.get("seen")) and params["ref"] in text and params["message"] in text
         and ask.get("buttons") == ["已入账", "没有入账"]},
        {"case": "点「没有入账」后这张不再列为结果不明", "seen": resolved,
         "ok": bool(resolved.get("gone")) and resolved.get("unknown") == 0},
        {"case": "任务删掉了（不留在任务列表）", "seen": removed, "ok": bool(removed.get("name")) and removed.get("left") is False},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], CLEANUP + f"""
from pearnly_core.express_operator import tasks
frappe.set_user("admin@cowork.regression")
record = frappe.db.get_value("Cowork Push Record", {params['record']!r}, ["status", "reason", "result_unknown"], as_dict=True)
doc = frappe.db.get_value("Cowork OCR History", {params['doc']!r}, ["last_push_status", "last_push_reason", "last_push_record"], as_dict=True)
check = frappe.get_attr("pearnly_cowork.push.application.service.operator_check")({params['doc']!r})
locks = frappe.db.count("Cowork Push Writer Lock", {{"holder": {params['record']!r}}})
attempts = frappe.get_all("Cowork Push Attempt", filters={{"push_record": {params['record']!r}}}, pluck="status")
records = frappe.db.count("Cowork Push Record", {{"history": {params['doc']!r}}})
count = frappe.db.count(tasks.DOCTYPE, {{"owner": "admin@cowork.regression"}})
gone = cleanup()
print(json.dumps({{"record": dict(record or {{}}), "doc": dict(doc or {{}}), "check": check, "locks": locks, "attempts": attempts,
                  "records": records, "tasks": count, "gone": gone, "left": leftovers()}}, default=str))
""")
    record, doc = rows["record"], rows["doc"]
    return [
        {"case": "推送记录照「核对」落成没有入账：manual / confirmed_not_entered、不再结果不明，领单的那次结束、写者锁放掉",
         "saved": {k: rows[k] for k in ("record", "attempts", "locks")},
         "ok": record.get("status") == "manual" and record.get("reason") == "confirmed_not_entered" and not int(record.get("result_unknown") or 0)
         and rows["attempts"] == ["finished"] and rows["locks"] == 0},
        {"case": "单据上的推送原因跟着改（不再是结果不明），没有新建推送记录", "saved": {"doc": doc, "records": rows["records"]},
         "ok": doc.get("last_push_record") == params["record"] and doc.get("last_push_reason") == "confirmed_not_entered"
         and rows["records"] == 1},
        {"case": "再预检这张不再是结果不明", "saved": rows["check"], "ok": rows["check"].get("reason") != "push.result_unknown"},
        {"case": "任务记录回到流程前的条数", "saved": {"before": params["tasks"], "after": rows["tasks"]}, "ok": rows["tasks"] == params["tasks"]},
        {"case": "造的连接、公司、单据、锁都清掉", "saved": {"gone": rows["gone"], "left": rows["left"]},
         "ok": rows["gone"]["connections"] == 1 and not any(rows["left"].values())},
    ]
