"""流程用例：CC「代录」在统一对话框架里（方案 cc-unified-plan 14，主会话 11-04 审定）——代录任务就是一段标准对话会话：
切到代录，新建页就是直写的新建页（下巴的公司 / 月份胶囊），没选公司发不出；选了公司说一句 = 同一事务建代录会话与任务
（`assistant.api.create` 带 `mode=operator`），回复照对话画；会话进侧栏同一个列表（`listing` 按 `mode` 筛，直写列表里没有它）；
代录事件源出「开始前」卡片（写入这家的 Express）与资料那一步，资料点开与站点这家这个月等着录的单一致；侧栏「···」删除（先问一次），
任务与会话一起删掉。

模型调用换成实验室的假智能体服务（`scripts/flows/lab_agents.py`）：回复是「[lab-agent] + 用户原文前 40 个字」，断言的是收发与落库。
网页上没有 CC 桌面版的桥：「开始」只截下交给站点的 start（核对点「开始」前先重取资料、交的单齐全），不真交单（录单在 CC 桌面版里，
另有离线测试 `tests/test_cc_operator.py`、`tests/test_express_operator_feed.py` 与实机验收）。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "CC 代录（统一对话框架）：新建页没选公司发不出、选公司说一句建代录会话与任务、侧栏按模式列、开始前卡片与资料一致、删除（假智能体后端）"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
REPLY = "[lab-agent]"
USER = "admin@erp.regression"


def prepare(lab, manifest):
    lab.python(manifest["site"], f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    company = manifest.get("company") or manifest["companies"][0]
    out = lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
from pearnly_erp_portal.express import contracts, operator, push
frappe.set_user({USER!r})
period = frappe.utils.add_months(frappe.utils.today(), -1)[:7]
first, last = operator._month(period)
# 这家这个月已提交、还没交进写账队列的一张采购单：临时标成「电脑离线、停下」（真实的停下状态之一），它就该出现在资料里；verify 还原
marked = frappe.get_all(push.PURCHASE, filters={{"company": {company!r}, "status": "submitted", "doc_date": ["between", [first, last]],
                                                 "ledger_state": ["in", ["", None]], "ledger_job": ["is", "not set"]}},
                        pluck="name", order_by="name asc", limit_page_length=1)
for name in marked:
    frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": contracts.HELD, "ledger_reason": contracts.OFFLINE}}, update_modified=False)
frappe.db.commit()
docs = operator.sources({company!r}, period, 0, 500)["documents"]
print(json.dumps({{"title": frappe.db.get_value("ERP Company", {company!r}, "company_name") or {company!r}, "period": period,
                  "marked": marked, "refs": [d["ref"] for d in docs],
                  "docs": len(docs), "tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": {USER!r}}}),
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"user": {USER!r}}})}}))
""")
    tag = "flow" + uuid.uuid4().hex[:6]
    return {**out, "company": company, "tag": tag, "line": f"{tag} 这次先录采购", "cases": []}


def steps(params, camera_code=None):
    title, line, company = params["title"], params["line"], params["company"]
    return [
        {"name": "切到代录", "js": JS + "const switched=await toMode('operator'); const ok=await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         "const before=(await listed('operator')).map(s=>s.id); "
         # 没选公司：写了字也发不出（代录任务要公司和月份，同直写「先选公司」的下巴）
         "const ta=q('textarea.aui-composer-input'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,'x'); "
         "ta.dispatchEvent(new Event('input',{bubbles:true})); await sleep(400); const blocked=!!q('button.aui-composer-send')?.disabled; "
         "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,''); ta.dispatchEvent(new Event('input',{bubbles:true})); "
         "return {switched, ok, mode: mode(), welcome: !!q('.aui-thread-welcome-root'), blocked, before};"},
        {"name": "选公司说一句", "js": JS + f"const picked=await pickCompany({json.dumps(title)}); "
         "const period=q('[data-slot=\"cc-period-chip\"]')?.innerText.trim()||''; "
         f"const sent=await say({json.dumps(line)}); let indicator=false; "
         f"const got=await until(()=>{{ if(q('[data-slot=\"cc-run-indicator\"]')) indicator=true; "
         f"return qa('[data-role=\"assistant\"]').some(e=>e.innerText.includes({json.dumps(REPLY)})); }},160); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),40); "
         "const card=await until(()=>q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-target\"]'),120); "
         "return {picked, period, sent, got, indicator, after: qa('[data-slot=\"cc-run-indicator\"]').length, mode: mode(), "
         "user: qa('[data-role=\"user\"]').map(e=>e.innerText.trim()), assistant: qa('[data-role=\"assistant\"]').map(e=>e.innerText.trim().slice(0,120)), "
         "card, target: q('[data-slot=\"cc-op-target\"]')?.innerText.trim()||'', "
         "start: !!q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-start\"]')};"},
        {"name": "侧栏按模式", "js": JS + "const operator=await listed('operator'); const chat=await listed('chat'); "
         f"const mine=operator.filter(s=>s.project==={json.dumps(company)}&&s.mode==='operator'&&s.task); "
         "return {operator: operator.map(s=>({id:s.id, mode:s.mode, task:s.task, project:s.project, period:s.period, title:s.title})), "
         "chat: chat.map(s=>s.id), mine: mine.map(s=>s.id), "
         "active: q('[data-slot=\"aui_thread-list-item\"][data-active=\"true\"]')?.innerText.trim()||''};"},
        {"name": "资料", "js": JS + "const opened=await openSteps('cc-op-sources'); if(!opened) return {error:'no sources step'}; "
         "q('[data-slot=\"cc-op-sources\"] button.cc-step-row').click(); "
         "await until(()=>q('[data-slot=\"cc-op-sources-list\"] [data-slot=\"cc-op-doc\"]'),80); "
         "for(let k=0;k<20;k++){const more=q('[data-slot=\"cc-op-sources-list\"] .cc-show-more'); if(!more) break; more.click(); await sleep(800);} "
         "await sleep(500); const docs=qa('[data-slot=\"cc-op-sources-list\"] [data-slot=\"cc-op-doc\"]'); "
         "return {opened, rows: docs.length, line: q('[data-slot=\"cc-op-sources\"] .cc-step-row')?.innerText.trim()||'', "
         "text: docs.map(e=>e.innerText).join('\\n').slice(0,3000)};"},
        # 点「开始」前卡片再取一次资料（测试机 11-05：卡片出现后才上传、识别完的单，第一次点「开始」报「没有要处理的单据」）。
        # 网页上没有执行电脑：只截下交给站点的 start（记下交的单、本页回一个空结果），不真交单，实验室的单据状态不变
        {"name": "开始前重取", "js": JS + "const calls=[]; const orig=window.fetch; let body=null; "
         "window.fetch=async(u,i={})=>{const m=(String(u).split('express_operator.api.')[1]||'').split('?')[0]; if(m) calls.push(m); "
         "if(m==='start'){ body=JSON.parse(i.body||'{}'); return new Response(JSON.stringify({message:{batch:'flow',results:{}}}),"
         "{status:200,headers:{'Content-Type':'application/json'}}); } return orig(u,i);}; "
         "const b=q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-start\"]'); if(!b){window.fetch=orig; return {error:'no start'};} b.click(); "
         "await until(()=>body,80); window.fetch=orig; "
         "await until(()=>q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-ask-answer\"]'),40); "
         "return {calls, ids: body?JSON.parse(body.doc_ids||'[]'):[], notice: q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-ask-failure\"]')?.innerText.trim()||'', "
         "answer: q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-ask-answer\"]')?.innerText.trim()||''};"},
        {"name": "删除", "js": JS + "const row=q('[data-slot=\"aui_thread-list-item\"][data-active=\"true\"]'); if(!row) return {error:'row missing'}; "
         "const text=row.innerText.trim(); row.dispatchEvent(new PointerEvent('pointerover',{bubbles:true})); "
         "const more=row.querySelector('[data-slot=\"aui_thread-list-item-more\"]'); if(!more) return {error:'no menu button'}; press(more); "
         "await until(()=>q('[data-slot=\"aui_thread-list-item-more-item\"][data-danger]'),40); "
         "const del=q('[data-slot=\"aui_thread-list-item-more-item\"][data-danger]'); if(!del) return {error:'no delete item'}; del.click(); "
         "const asked=await until(()=>q('[data-slot=\"cc-delete-chats-yes\"]'),40); if(!asked) return {error:'not asked'}; "
         "q('[data-slot=\"cc-delete-chats-yes\"]').click(); "
         "const gone=await until(()=>!qa('[data-slot=\"aui_thread-list-item\"]').some(e=>e.innerText.trim()===text),80); "
         "await sleep(500); return {text, asked, gone, listed: (await listed('operator')).map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    switch, made, side, files, removed, started = (by.get(k) or {} for k in ("切到代录", "选公司说一句", "侧栏按模式", "资料", "删除", "开始前重取"))
    calls = started.get("calls") or []
    new = [sid for sid in side.get("mine") or [] if sid not in (switch.get("before") or [])]
    row = next((s for s in side.get("operator") or [] if s["id"] in new), {})
    return [
        {"case": "切到代录：新建页就是直写的新建页（问候、下巴的公司胶囊），没选公司写了字也发不出", "seen": switch,
         "ok": bool(switch.get("ok")) and switch.get("mode") == "operator" and bool(switch.get("welcome")) and bool(switch.get("blocked"))},
        {"case": "选了公司（月份默认上个月）说一句：交给这段会话的 CC，回复照对话画，等回复时出状态行、回复到了收起", "seen": made,
         "ok": bool(made.get("picked")) and bool(made.get("sent")) and bool(made.get("got")) and params["line"] in (made.get("user") or [])
         and bool(made.get("indicator")) and made.get("after") == 0 and made.get("mode") == "operator"},
        {"case": "同一事务建了代录会话与任务：侧栏同一个接口按 mode=operator 列出这一段（挂着任务、这家这个月），直写列表里没有它",
         "seen": {"new": new, "row": row, "chat": len(side.get("chat") or []), "period": params["period"]},
         "ok": len(new) == 1 and row.get("mode") == "operator" and bool(row.get("task")) and row.get("period") == params["period"]
         and new[0] not in (side.get("chat") or []) and bool(side.get("active"))},
        {"case": "代录事件源出「开始前」卡片：写入这家的 Express、有「开始」", "seen": {k: made.get(k) for k in ("card", "target", "start")},
         "ok": bool(made.get("card")) and params["title"] in (made.get("target") or "") and bool(made.get("start"))},
        {"case": "资料那一步点开与站点这家这个月等着录的单一致（张数相同、单号逐张在列）", "seen": {**files, "site": params["docs"], "refs": params["refs"]},
         "ok": bool(files.get("opened")) and files.get("rows") == params["docs"]
         and all(ref in (files.get("text") or "") for ref in params["refs"])},
        {"case": "点「开始」先重取资料与预检（卡片出现后才有的单也交上去），再交 start：交的单与站点这家这个月等着录的一样多，卡片定下、不报「没有要处理的单据」",
         "seen": {**started, "site": params["docs"]},
         "ok": "start" in calls and "documents" in calls[:calls.index("start")] and "precheck" in calls[:calls.index("start")]
         and len(started.get("ids") or []) == params["docs"] and not started.get("notice") and bool(started.get("answer"))},
        {"case": "侧栏「···」删除：先问一次，确认后这一行消失、列表里也没有了", "seen": removed,
         "ok": bool(removed.get("asked")) and bool(removed.get("gone")) and bool(new) and new[0] not in (removed.get("listed") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
from pearnly_erp_portal.express import push
for name in {params["marked"]!r}:
    frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": "", "ledger_reason": ""}}, update_modified=False)
frappe.db.commit()
print(json.dumps({{"tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": {USER!r}}}),
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"user": {USER!r}}}),
                  "orphans": frappe.db.count("Pearnly Assistant Session", {{"user": {USER!r}, "operator_task": ["is", "set"],
                                             "title": ["like", "%{params['tag']}%"]}})}}))
""")
    return [
        {"case": "删除后任务记录回到流程前的条数（建过一条、连会话一起删掉了）", "saved": {"before": params["tasks"], "after": rows["tasks"]},
         "ok": rows["tasks"] == params["tasks"]},
        {"case": "代录会话随任务一起删掉", "saved": {"before": params["sessions"], "after": rows["sessions"], "tagged": rows["orphans"]},
         "ok": rows["sessions"] == params["sessions"] and rows["orphans"] == 0},
    ]
