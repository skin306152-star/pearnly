"""流程用例：CC 对话页 `/assistant` 左侧栏两种模式共用一个框架（主会话 11-03 审定；11-04 统一对话框架：代录任务就是一段会话，
同一个侧栏按模式筛，ERP、Cowork 同一个模块，电脑与手机宽度）——
对话模式与代录模式里共有的入口（新建、公司）位置、顺序一样，切模式时一格不挪；定时任务只在对话模式（只 Cowork 声明了）；没有
「待处理」入口（11-04 并进对话）；侧栏同一个接口按模式列：对话模式只列直写的对话、代录模式只列代录会话（准备的两段各只在自己的
模式出现）；代录模式下点「公司」留在代录（主栏换成公司页），在公司列表选一家 = 直写的新建页、下巴选好这家（同组头「＋」，用户
11-03），没说话不建任务；侧栏点那段「要你处理」的代录会话：在代录打开它，代录事件源出那张没录成的单的卡片（单号、原因）；
对话模式下选公司照旧进公司页。

准备（都在站点里用产品自己的函数）：一段这家这个月的直写对话（`sessions.create`，假智能体后端）、一段代录会话（`sessions.create`
带 mode=operator，同一事务建任务），把任务记成一张没录成、要人选公司的单（需要你处理）。Cowork 的旧数据集没有公司：用代录
流程的造数据（`cowork_operator_lib`）建一条合成账套目录的连接、从目录建一家，Cowork 另走一步「添加公司」：弹窗候选里这家是
「已添加」，点了打开它的公司页（ERP 的添加公司在 `cc_companies.py`）。verify 删掉准备的会话与任务、按连接清掉造的数据。
设了 `PEARNLY_FLOWS_SHOTS` 时每一步截图（`run.py`）。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cowork_operator_lib as operator_lib  # noqa: E402
from cc_lib import JS  # noqa: E402

TITLE = "CC 侧栏两种模式同一个框架：共有入口不挪、定时任务只对话模式、没有待处理入口、侧栏按模式列会话、代录下选公司进新建页、点代录会话打开它的卡片"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"

WAIT = JS + (
    "const phone=()=>innerWidth<768;"
    "const root=()=>phone()?q('.cc-mdrawer .cc-drawer'):q('[data-slot=\"sidebar\"]');"
    "const openDrawer=async()=>{if(phone()&&q('.cc-mshell')?.dataset.open!=='true'){q('[data-slot=\"cc-nav-toggle\"]').click();"
    "await until(()=>q('.cc-mshell')?.dataset.open==='true'); await sleep(600);}};"
    "const nav=()=>{const r=root(); return r?[...r.querySelectorAll('.cc-nav-item:is([data-slot=\"aui_thread-list-new\"],"
    "[data-slot=\"cc-nav-pending\"],[data-slot=\"cc-nav-companies\"],[data-slot=\"cc-nav-tasks\"])')].map(e=>{const b=e.getBoundingClientRect();"
    "return {slot:e.dataset.slot, text:e.innerText.trim().replace(/\\s+/g,' '), top:Math.round(b.top), left:Math.round(b.left)};}):[];};"
    # 侧栏里带这个标签的、看得见的对话行（标题）
    "const shownRows=tag=>{const r=root(); return r?[...r.querySelectorAll('[data-slot=\"aui_thread-list-item\"]')].filter(seen)"
    ".map(e=>e.innerText.trim().split('\\n')[0]).filter(t=>t.includes(tag)):[];};"
    "const switchTo=async m=>{await openDrawer(); const b=(root()?.closest('.cc-mdrawer, [data-slot=\"sidebar\"]')||document)"
    ".querySelector(`[data-mode=\"${m}\"]`)||q(`[data-mode=\"${m}\"]`); b?.click(); return await until(()=>mode()===m,40);};")

# 共有入口（两种模式都有的）在侧栏里的样子：位置、顺序
SHARED = ("aui_thread-list-new", "cc-nav-companies")
REASON = "[OPERATOR_COMPANY_PICK] flow"


def prepare(lab, manifest):
    product = manifest.get("product", "erp")
    user = f"admin@{product}.regression"
    lab.python(manifest["site"], f'frappe.db.set_value("User", {user!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "flow" + uuid.uuid4().hex[:6]
    # Cowork：用代录流程的造数据建一条 Express 连接（合成账套目录）并从目录建一家公司（旧数据集没有 Cowork 公司），公司列表与
    # 「添加公司」弹窗里才有这家（弹窗里是「已添加」）；verify 按连接清掉
    seed = (operator_lib.CLEANUP + operator_lib.BASE + "\nseeded = company\n") if product == "cowork" else ""
    erp_company = manifest.get("company") or (manifest.get("companies") or [""])[0]
    out = lab.python(manifest["site"], f"""
from pearnly_core.assistant import projects, sessions
from pearnly_core.express_operator import tasks
frappe.set_user({user!r})
{seed}
# 没录成的那张用产品里真有的单据（卡片点开要按它预检）：ERP 取这家一张已提交的采购单（只读），Cowork 用造数据建一张识别单
if {product!r} == "erp":
    from pearnly_erp_portal.express import push
    company = {erp_company!r}
    title = frappe.db.get_value("ERP Company", company, "company_name") or company
    doc_id = (frappe.get_all(push.PURCHASE, filters={{"company": company, "status": "submitted"}}, pluck="name",
                             order_by="name asc", limit_page_length=1) or [{tag!r} + "-doc"])[0]
else:
    company, title = seeded["id"], seeded["title"]
    doc_id = recognized({tag!r} + "-REF")
period = frappe.utils.add_months(frappe.utils.today(), -1)[:7]
tasks_before = frappe.get_all(tasks.DOCTYPE, filters={{"owner": {user!r}}}, pluck="name", limit_page_length=1000)
chat = sessions.create(project=company, period=period)
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": chat["id"]}}, "title", {tag!r} + " 对话")
made = sessions.create(project=company, period=period, mode="operator")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {tag!r} + " 要你处理")
doc = frappe.get_doc(tasks.DOCTYPE, made["task"])
doc.title = {tag!r} + " 要你处理"
doc.status = "attention"
doc.append("documents", {{"doc_id": doc_id, "ref": {tag!r} + "-REF", "party": "Flow party", "state": "not_done",
                          "reason": {REASON!r}}})
doc.flags.ignore_permissions = True
doc.save()
frappe.db.commit()
print(json.dumps({{"product": {product!r}, "company": company, "title": title, "period": period, "chat": chat["id"],
                  "operator": made["id"], "task": made["task"], "tasks_before": tasks_before,
                  "sections": projects.declared(projects.provider()).get("sections", [])}}))
""")
    return {**out, "tag": tag, "shot": out["product"], "user": user}


# 公司列表页里名字是这家的那张卡（第一行是公司名）
PICK = "const card=()=>qa('[data-slot=\"cc-company-rows\"] button').find(e=>e.innerText.split('\\n')[0].trim()===TITLE);"


def _shot(params, name):
    return {"shot": True, "file": f"{params['shot']}-{name}"}


def steps(params, camera_code=None):
    tag = json.dumps(params["tag"])
    return [
        {"name": "对话模式", **_shot(params, "1-对话模式"), "js": WAIT + "await until(()=>mode()==='chat'); await openDrawer(); "
         f"await until(()=>root()&&q('[data-slot=\"cc-nav-companies\"]')); await until(()=>shownRows({tag}).length>0,60); await sleep(800); "
         f"return {{mode: mode(), nav: nav(), rows: shownRows({tag}), "
         "listed: {chat: (await listed('chat')).map(s=>s.id), operator: (await listed('operator')).map(s=>s.id)}};"},
        {"name": "切到代录", **_shot(params, "2-代录模式"), "js": WAIT + "await switchTo('operator'); await openDrawer(); "
         f"await until(()=>shownRows({tag}).some(t=>t.includes('要你处理')),60); await sleep(800); "
         f"return {{mode: mode(), nav: nav(), rows: shownRows({tag}), "
         "groups: (root()?.querySelectorAll('[data-slot=\"cc-project\"]')||[]).length};"},
        {"name": "代录下点公司", **_shot(params, "3-代录下公司页"), "js": WAIT + "await openDrawer(); q('[data-slot=\"cc-nav-companies\"]').click(); "
         "const page=await until(()=>q('.cc-page')&&q('[data-slot=\"cc-company-rows\"]')); await sleep(800); await openDrawer(); await sleep(300); "
         f"return {{page, mode: mode(), active: q('[data-slot=\"cc-nav-companies\"]')?.hasAttribute('data-active'), nav: nav(), rows: shownRows({tag})}};"},
        # 用户 11-03 / 11-04：代录模式下从「公司」选一家 = 直写的新建页、下巴选好这家（月份上个月），不是对话公司页；没说话不建任务
        {"name": "代录下选公司", **_shot(params, "3b-代录下选公司"), "js": WAIT + f"const TITLE={json.dumps(params['title'])}; " + PICK +
         "if(!await until(card)) return {error:'company card missing'}; card().click(); "
         "const ok=await until(()=>(q('[data-slot=\"cc-chin-company\"]')?.innerText||'').includes(TITLE)&&q('.aui-thread-welcome-root')); await sleep(800); "
         "return {ok, mode: mode(), chin: q('[data-slot=\"cc-chin-company\"]')?.innerText.trim()||'', "
         "period: q('[data-slot=\"cc-period-chip\"]')?.innerText.trim()||'', page: !!q('[data-slot=\"cc-company-page\"]')};"},
        {"name": "打开代录会话", **_shot(params, "5-打开代录会话"), "js": WAIT + "await openDrawer(); "
         f"const row=(root()?[...root().querySelectorAll('[data-slot=\"aui_thread-list-item\"]')]:[]).find(e=>e.innerText.includes({tag})&&e.innerText.includes('要你处理')); "
         "if(!row) return {error:'operator row missing'}; (row.querySelector('[data-slot=\"aui_thread-list-item-trigger\"]')||row).click(); "
         "const ok=await until(()=>q('[data-slot=\"cc-op-choose\"]'),120); await sleep(600); const c=q('[data-slot=\"cc-op-choose\"]'); "
         "return {ok, mode: mode(), card: c?.innerText.trim().slice(0,300)||'', reason: c?.querySelector('[data-slot=\"cc-op-reason\"]')?.innerText.trim()||'', "
         "text: qa('[data-role=\"assistant\"]').map(e=>e.innerText.trim()).join('\\n').slice(0,600)};"},
        {"name": "切回对话", **_shot(params, "6-切回对话"), "js": WAIT + "await switchTo('chat'); await openDrawer(); "
         f"await until(()=>shownRows({tag}).some(t=>t.includes('对话')),60); await sleep(800); "
         f"return {{mode: mode(), nav: nav(), rows: shownRows({tag})}};"},
        {"name": "对话下选公司", **_shot(params, "7-对话下选公司"), "js": WAIT + f"const TITLE={json.dumps(params['title'])}; " + PICK +
         "await openDrawer(); q('[data-slot=\"cc-nav-companies\"]').click(); if(!await until(card)) return {error:'company card missing'}; card().click(); "
         "const ok=await until(()=>q('[data-slot=\"cc-company-page\"]')); await sleep(600); "
         "return {ok, mode: mode()};"},
    ] + ([] if params["product"] != "cowork" else [
        {"name": "添加公司", **_shot(params, "8-添加公司"), "js": WAIT + f"const TITLE={json.dumps(params['title'])}; " + PICK +
         "let opened=''; window.open=(u)=>{opened=String(u); return null;}; await openDrawer(); q('[data-slot=\"cc-nav-companies\"]').click(); "
         "if(!await until(()=>qa('button').some(b=>b.innerText.trim()==='添加公司'))) return {error:'no add button'}; "
         "qa('button').find(b=>b.innerText.trim()==='添加公司').click(); "
         "if(!await until(()=>q('[data-slot=\"cc-candidates\"] .cc-list-row'))) return {error:'candidates missing'}; "
         "const all=qa('[data-slot=\"cc-candidates\"] .cc-list-row'); const cands=all.map(r=>r.innerText.trim()); "
         "const row=all.find(r=>r.innerText.split('\\n')[0].trim()===TITLE); if(!row) return {rows: cands, error:'company row missing'}; row.click(); "
         "const ok=await until(()=>q('[data-slot=\"cc-company-page\"]')&&q('.cc-page-title')?.innerText.trim()===TITLE); "
         "return {ok, rows: cands, opened, dialog: !!q('[data-slot=\"cc-add-company\"]'), mode: mode()};"},
    ])


def _shared(nav):
    return [(item["slot"], item["top"], item["left"]) for item in nav or [] if item["slot"] in SHARED]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    chat, operator, companies, picked, opened, back = (by.get(k) or {} for k in ("对话模式", "切到代录", "代录下点公司", "代录下选公司",
                                                                              "打开代录会话", "切回对话"))
    chat_pick, added = by.get("对话下选公司") or {}, by.get("添加公司") or {}
    scheduled = "tasks" in params["sections"]
    slots = lambda step: [item["slot"] for item in step.get("nav") or []]
    tag = params["tag"]
    has = lambda step, word: any(word in row for row in step.get("rows") or [])
    listed = chat.get("listed") or {}
    in_chat, in_operator = listed.get("chat") or [], listed.get("operator") or []
    return [
        {"case": "共有入口（新建、公司）切到代录时位置、顺序一样（手机抽屉里没有新建那一行，只比公司）",
         "seen": {"chat": _shared(chat.get("nav")), "operator": _shared(operator.get("nav"))},
         "ok": bool(_shared(chat.get("nav"))) and _shared(chat.get("nav")) == _shared(operator.get("nav"))},
        {"case": "没有「待处理」入口（11-04 并进对话），两种模式都没有", "seen": {"chat": slots(chat), "operator": slots(operator)},
         "ok": "cc-nav-pending" not in slots(chat) + slots(operator)},
        {"case": "定时任务只在对话模式、只在声明了的产品，且在共有入口下面", "seen": {"chat": slots(chat), "operator": slots(operator), "sections": params["sections"]},
         "ok": ("cc-nav-tasks" in slots(chat)) == scheduled and "cc-nav-tasks" not in slots(operator)
         and (not scheduled or slots(chat)[-1] == "cc-nav-tasks")},
        {"case": "同一个列表接口按模式筛：直写的那段只在 mode=chat、代录那段只在 mode=operator",
         "seen": {"chat": params["chat"], "operator": params["operator"], "mode_chat": [params[k] in in_chat for k in ("chat", "operator")],
                  "mode_operator": [params[k] in in_operator for k in ("chat", "operator")]},
         "ok": params["chat"] in in_chat and params["operator"] not in in_chat
         and params["operator"] in in_operator and params["chat"] not in in_operator},
        {"case": "侧栏跟着模式换：对话模式只见直写的那段，代录模式只见代录那段（同一个侧栏、公司分组）",
         "seen": {"chat": chat.get("rows"), "operator": operator.get("rows"), "back": back.get("rows"), "groups": operator.get("groups")},
         "ok": has(chat, "对话") and not has(chat, "要你处理") and has(operator, "要你处理") and not has(operator, f"{tag} 对话")
         and has(back, "对话") and not has(back, "要你处理") and (operator.get("groups") or 0) >= 1},
        {"case": "代录模式下点「公司」：主栏换成公司页，仍在代录（侧栏仍只列代录会话）", "seen": companies,
         "ok": bool(companies.get("page")) and companies.get("mode") == "operator" and bool(companies.get("active"))
         and not has(companies, f"{tag} 对话")},
        {"case": "代录模式下从「公司」选一家：直写的新建页、下巴选好这家，不出对话公司页，仍在代录", "seen": {**picked, "want": params["title"]},
         "ok": bool(picked.get("ok")) and picked.get("mode") == "operator" and not picked.get("page")
         and params["title"] in (picked.get("chin") or "")},
        {"case": "侧栏点「要你处理」的代录会话：在代录打开，出那张没录成的单的卡片（单号、要人选公司的原因）", "seen": opened,
         "ok": bool(opened.get("ok")) and opened.get("mode") == "operator" and f"{tag}-REF" in (opened.get("card") or "")
         and bool(opened.get("reason"))},
        {"case": "对话模式下从「公司」选一家：照旧进公司页", "seen": chat_pick,
         "ok": bool(chat_pick.get("ok")) and chat_pick.get("mode") == "chat"},
        *([] if params["product"] != "cowork" else [
            {"case": "Cowork「添加公司」弹窗：候选里这家标「已添加」，点了打开它的公司页（不开新窗口）", "seen": {**added, "want": params["title"]},
             "ok": bool(added.get("ok")) and not added.get("opened") and not added.get("dialog")
             and any(r.startswith(params["title"]) and "已添加" in r for r in added.get("rows") or [])}]),
        {"case": "切回对话：回到对话的侧栏，共有入口还在原位", "seen": {"mode": back.get("mode"), "nav": _shared(back.get("nav"))},
         "ok": back.get("mode") == "chat" and _shared(back.get("nav")) == _shared(chat.get("nav"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    product = params["product"]
    keep = params["tasks_before"] + [params["task"], ""]
    out = lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
from pearnly_core.express_operator import tasks
frappe.set_user({params["user"]!r})
extra = frappe.get_all(tasks.DOCTYPE, filters={{"owner": {params["user"]!r}, "name": ["not in", {keep!r}]}},
                       fields=["name", "company", "status"], limit_page_length=50)
for sid in ({params["chat"]!r}, {params["operator"]!r}):
    if frappe.db.exists("Pearnly Assistant Session", {{"session_id": sid}}):
        sessions.delete(sid)
for row in extra:
    frappe.delete_doc(tasks.DOCTYPE, row.name, ignore_permissions=True, force=True)
{operator_lib.CLEANUP + "cleanup()" if product == "cowork" else ""}
frappe.db.commit()
print(json.dumps({{"extra": [dict(row) for row in extra], "task": frappe.db.exists(tasks.DOCTYPE, {params["task"]!r}) or "",
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"session_id": ["in", [{params["chat"]!r}, {params["operator"]!r}]]}})}}))
""")
    return [{"case": "代录下选公司没说话：站点没多建任务（任务随说第一句建）", "saved": out["extra"], "ok": out["extra"] == []},
            {"case": "删掉准备的两段会话，代录那段连任务一起删", "saved": {"task": out["task"], "sessions": out["sessions"]},
             "ok": not out["task"] and out["sessions"] == 0}]
