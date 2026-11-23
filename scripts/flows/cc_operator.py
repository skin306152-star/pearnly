"""流程用例：CC「代录」在统一对话框架里（方案 cc-unified-plan 14，主会话 11-04 审定）——代录任务就是一段标准对话会话：
切到代录，新建页就是直写的新建页（下巴的公司 / 月份胶囊），没选公司发不出；选了公司说一句 = 同一事务建代录会话与任务
（`assistant.api.create` 带 `mode=operator`），回复照对话画；会话进侧栏同一个列表（`listing` 按 `mode` 筛，直写列表里没有它）；
代录事件源出「开始前」卡片（写入这家的 Express）与资料那一步，资料点开与站点这家这个月等着录的单一致；侧栏「···」删除（先问一次），
任务与会话一起删掉。

模型调用换成实验室的假智能体服务（`scripts/flows/lab_agents.py`）：回复是「[lab-agent] + 用户原文前 40 个字」，断言的是收发与落库。
开始前卡片没有「开始」（11-11 一句话下令）：说一句让假智能体调写入计划，核计划按完整清单算、还没开始；网页上没有 CC 桌面版的桥，
不真开始（三道保险在 `tests/test_write_plan.py`，录单在 CC 桌面版里，另有 `tests/test_cc_operator.py`、`tests/test_express_operator_feed.py`
与实机验收）。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from erp_lib import ensure_documents

TITLE = "CC 代录（统一对话框架）：新建页没选公司发不出、选公司说一句建代录会话与任务、侧栏按模式列、开始前卡片与资料一致、删除（假智能体后端）"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
REPLY = "[lab-agent]"
USER = "admin@erp.regression"


def prepare(lab, manifest):
    lab.python(manifest["site"], f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    company = manifest.get("company") or manifest["companies"][0]
    ensure_documents(lab, manifest)  # 新开户那套数据没有已提交的采购单：按正式函数补一张（已有就不动，可重复跑）
    out = lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
from pearnly_erp_portal.express import contracts, operator, push
frappe.set_user({USER!r})
period = frappe.utils.today()[:7]  # 对话月份：CC 新对话默认当月（11-16）
first, last = operator._month(period)
# 这家已提交、还没交进写账队列（没有写账件；状态同代录资料的范围）、不带按台号码（带号码的不交代录）的采购单：先找这个月的；
# 新开户那套数据这个月没有，就挑一张别的
# 月份的、临时把单据日期挪进这个月（11-05：W4 加了「开始前重取」断言后，new 数据集这一格因为没有等着录的单必挂）。
# 挑中的临时标成「电脑离线、停下」（真实的停下状态之一），它就该出现在资料里；原来的日期与账本状态记进 restore，cleanup 原样还原
waiting = \"\"\"SELECT name, doc_date, ledger_state, ledger_reason FROM `tabPurchase Document` p
    WHERE company = %s AND status = 'submitted' AND COALESCE(ledger_job, '') = ''
    AND COALESCE(ledger_state, '') IN ('', 'queued', 'held', 'failed')
    AND NOT EXISTS (SELECT 1 FROM `tabERP Line Serial` s WHERE s.parenttype = 'Purchase Document' AND s.parent = p.name)
    ORDER BY (doc_date BETWEEN %s AND %s) DESC, name ASC LIMIT 8\"\"\"
found = frappe.db.sql(waiting, ({company!r}, first, last), as_dict=True)
if not found:
    raise RuntimeError("no submitted purchase document waiting to be written for {company}: the dataset needs one")
restore, marked = [], []
for row in found:  # 最多 8 张：多过开始前卡片先列的行数，卡片要收起其余的（11-09 发布前：45 张逐行列出，把用户的话和回复挤出视口）
    restore.append({{"name": row.name, "doc_date": str(row.doc_date), "ledger_state": row.ledger_state or "",
                    "ledger_reason": row.ledger_reason or ""}})
    values = {{"ledger_state": contracts.HELD, "ledger_reason": contracts.OFFLINE}}
    if not (first <= str(row.doc_date) <= last):
        values["doc_date"] = first
    frappe.db.set_value(push.PURCHASE, row.name, values, update_modified=False)
    marked.append(row.name)
frappe.db.commit()
docs = operator.sources({company!r}, period, 0, 500)["documents"]
print(json.dumps({{"title": frappe.db.get_value("ERP Company", {company!r}, "company_name") or {company!r}, "period": period,
                  "marked": marked, "restore": restore, "refs": [d["ref"] for d in docs],
                  "docs": len(docs), "tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": {USER!r}}}),
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"user": {USER!r}}})}}))
""")
    tag = "flow" + uuid.uuid4().hex[:6]
    return {**out, "company": company, "tag": tag, "line": f"{tag} 这次先录采购", "cases": []}


def steps(params, camera_code=None):
    title, line, company = params["title"], params["line"], params["company"]
    plan_line = params["tag"] + " 写入 [lab-tool]" + json.dumps({"name": "pearnly_write_plan", "input": {}}) + "[/lab-tool]"
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
         # 实验室不起 socketio：页面的实时通道连不上，事件全靠补拉（11-23 Flows 手机格开始前卡片不出那一路）
         "const realtime=await fetch('/socket.io/?EIO=4&transport=polling').then(r=>r.ok).catch(()=>false); "
         f"const sent=await say({json.dumps(line)}); let indicator=false; "
         f"const got=await until(()=>{{ if(q('[data-slot=\"cc-run-indicator\"]')) indicator=true; "
         f"return qa('[data-role=\"assistant\"]').some(e=>e.innerText.includes({json.dumps(REPLY)})); }},160); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),40); const replied=Date.now(); "
         "const card=await until(()=>q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-target\"]'),120); const cardMs=Date.now()-replied; "
         "await until(()=>q('[data-slot=\"cc-op-before\"] .cc-op-before-body'),80); await sleep(500); "
         "const roles=qa('[data-role]'); const userAt=roles.findIndex(e=>e.dataset.role==='user'); "
         "const cardAt=roles.findIndex(e=>e.querySelector('[data-slot=\"cc-op-before\"]')); "
         "const lines=qa('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-handle\"], [data-slot=\"cc-op-before\"] [data-slot=\"cc-op-unknown\"]').length; "
         "const moreRows=!!q('[data-slot=\"cc-op-before\"] .cc-show-more'); "
         "return {picked, period, sent, got, indicator, after: qa('[data-slot=\"cc-run-indicator\"]').length, mode: mode(), "
         "user: qa('[data-role=\"user\"]').map(e=>e.innerText.trim()), assistant: qa('[data-role=\"assistant\"]').map(e=>e.innerText.trim().slice(0,120)), "
         "card, target: q('[data-slot=\"cc-op-target\"]')?.innerText.trim()||'', "
         "start: !!q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-start\"]'), send: !!q('button.aui-composer-send'), "
         "stop: !!q('button.aui-composer-cancel'), userAt, cardAt, lines, moreRows, realtime, cardMs};"},
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
        # 11-11 一句话下令：开始前卡片没有「开始」，用户在对话里说一句，CC 按服务端的写入计划复述、用户同意后才开始。实验室的假智能体
        # 认 [lab-tool]，照真实引擎调 pearnly_write_plan，回一条「[lab-agent] pearnly_write_plan <工具结果原文>」；这里只建计划不开始
        # （网页上没有执行电脑，开始的三道保险由离线测试 tests/test_write_plan.py 锁住）
        {"name": "说一句建写入计划", "js": JS + f"const sent=await say({json.dumps(plan_line)}); "
         # 等工具结果整段 JSON 画完、能取到值再读（toolResult，cc_lib）：只等 "status" 出现就读会读到没画完的（11-11、11-12 偶发）
         "await until(()=>!!toolResult('pearnly_write_plan'),240); const result=toolResult('pearnly_write_plan'); "
         "const bubble=qa('[data-role=\"assistant\"]').find(e=>e.innerText.includes('pearnly_write_plan {')); "
         "const text=bubble?bubble.innerText.slice(bubble.innerText.indexOf('pearnly_write_plan {')):''; "
         "const plan=result?{status: result.status, count: result.count, skipped_count: result.skipped_count, plan: result.plan, "
         "note: result.note}:null; "
         "await until(()=>!!q('button.aui-composer-send'),80); "
         "return {sent, replied: !!bubble, status: plan&&plan.status, count: plan&&plan.count, skipped: plan&&plan.skipped_count, "
         "plan: plan&&plan.plan, note: plan?String(plan.note||'').slice(0,80):'', send: !!q('button.aui-composer-send'), "
         "raw: text.slice(0,160)};"},
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
    switch, made, side, files, removed, planned = (by.get(k) or {} for k in ("切到代录", "选公司说一句", "侧栏按模式", "资料", "删除",
                                                                            "说一句建写入计划"))
    new = [sid for sid in side.get("mine") or [] if sid not in (switch.get("before") or [])]
    row = next((s for s in side.get("operator") or [] if s["id"] in new), {})
    return [
        {"case": "切到代录：新建页就是直写的新建页（问候、下巴的公司胶囊），没选公司写了字也发不出", "seen": switch,
         "ok": bool(switch.get("ok")) and switch.get("mode") == "operator" and bool(switch.get("welcome")) and bool(switch.get("blocked"))},
        {"case": "选了公司（月份默认当月）说一句：交给这段会话的 CC，回复照对话画，等回复时出状态行、回复到了收起", "seen": made,
         "ok": bool(made.get("picked")) and bool(made.get("sent")) and bool(made.get("got")) and params["line"] in (made.get("user") or [])
         and bool(made.get("indicator")) and made.get("after") == 0 and made.get("mode") == "operator"},
        {"case": "同一事务建了代录会话与任务：侧栏同一个接口按 mode=operator 列出这一段（挂着任务、这家这个月），直写列表里没有它",
         "seen": {"new": new, "row": row, "chat": len(side.get("chat") or []), "period": params["period"]},
         "ok": len(new) == 1 and row.get("mode") == "operator" and bool(row.get("task")) and row.get("period") == params["period"]
         and new[0] not in (side.get("chat") or []) and bool(side.get("active"))},
        {"case": "代录事件源出「开始前」卡片：写入这家的 Express；卡片上没有「开始」，不停着等人点，输入框是发送（11-11 一句话下令）",
         "seen": {k: made.get(k) for k in ("card", "target", "start", "send", "stop")},
         "ok": bool(made.get("card")) and params["title"] in (made.get("target") or "") and not made.get("start")
         and bool(made.get("send")) and not made.get("stop")},
        {"case": "实时通道连不上、页面只靠补拉：回复收起后开始前卡片照样出（11-23 Flows 手机格：卡片到下一句发出才出）",
         "seen": {k: made.get(k) for k in ("realtime", "card", "cardMs")},
         "ok": bool(made.get("card")) and not made.get("realtime")},
        {"case": "开始前卡片排在用户这句之后，逐张的行最多先列 5 行、其余收进「显示更多」（11-09：45 张逐行列出、卡片排在前面，用户的话和回复被挤出视口）",
         "seen": {k: made.get(k) for k in ("userAt", "cardAt", "lines", "moreRows")} | {"marked": len(params["marked"]), "site": params["docs"]},
         "ok": 0 <= int(made.get("userAt", -1)) < int(made.get("cardAt", -1))
         and 1 <= int(made.get("lines") or 0) <= 5 and (len(params["marked"]) <= 5 or bool(made.get("moreRows")))},
        {"case": "资料那一步点开与站点这家这个月等着录的单一致（张数相同、单号逐张在列）", "seen": {**files, "site": params["docs"], "refs": params["refs"]},
         "ok": bool(files.get("opened")) and files.get("rows") == params["docs"]
         and all(ref in (files.get("text") or "") for ref in params["refs"])},
        {"case": "说一句：CC 建写入计划，按服务端完整待录清单算（写的 + 跳过的 = 站点这家这个月等着录的），要先复述、等用户回话，"
                 "还没开始；之后输入框照常是发送",
         "seen": {**planned, "site": params["docs"]},
         "ok": bool(planned.get("replied")) and planned.get("status") in ("ready", "nothing_to_write")
         and int(planned.get("count") or 0) + int(planned.get("skipped") or 0) == params["docs"]
         and (planned.get("status") == "nothing_to_write" or "say back" in (planned.get("note") or ""))
         and bool(planned.get("send"))},
        {"case": "侧栏「···」删除：先问一次，确认后这一行消失、列表里也没有了", "seen": removed,
         "ok": bool(removed.get("asked")) and bool(removed.get("gone")) and bool(new) and new[0] not in (removed.get("listed") or [])},
    ]


def cleanup(lab, manifest, params):
    """跑完（成功、失败、步骤本身出错都一样，`run.py` 在 finally 里调）把 prepare 临时改的采购单原样还原：单据日期、账本状态与原因。"""
    lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import push
for row in {params.get("restore") or []!r}:
    if frappe.db.exists(push.PURCHASE, row["name"]):
        frappe.db.set_value(push.PURCHASE, row["name"], {{"doc_date": row["doc_date"], "ledger_state": row["ledger_state"],
                                                          "ledger_reason": row["ledger_reason"]}}, update_modified=False)
frappe.db.commit()
print("{{}}")
""")


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
print(json.dumps({{"tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": {USER!r}}}),
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"user": {USER!r}}}),
                  "orphans": frappe.db.count("Pearnly Assistant Session", {{"user": {USER!r}, "operator_task": ["is", "set"],
                                             "title": ["like", "%{params['tag']}%"]}}),
                  "plans": frappe.db.count("Pearnly Assistant Write Plan", {{"user": {USER!r}}})}}))
""")
    return [
        {"case": "删除后任务记录回到流程前的条数（建过一条、连会话一起删掉了）", "saved": {"before": params["tasks"], "after": rows["tasks"]},
         "ok": rows["tasks"] == params["tasks"]},
        {"case": "代录会话随任务一起删掉", "saved": {"before": params["sessions"], "after": rows["sessions"], "tagged": rows["orphans"]},
         "ok": rows["sessions"] == params["sessions"] and rows["orphans"] == 0},
        {"case": "写入计划随对话一起删掉", "saved": {"plans": rows["plans"]}, "ok": rows["plans"] == 0},
    ]
