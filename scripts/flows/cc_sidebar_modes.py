"""流程用例：CC 对话页 `/assistant` 左侧栏两种模式共用一个框架（主会话 11-03 审定，ERP、Cowork 同一个模块，电脑与手机宽度）——
对话模式与代录模式里共有的入口（新建、待处理 N、公司）位置、顺序一样，切模式时一格不挪；定时任务只在对话模式（只 Cowork 声明了）；
代录模式下点「公司」「待处理」留在代录（主栏换成那一页，侧栏仍是任务）；「待处理 N」= 产品的待处理 + 需要你处理的代录任务（按单据
去重：任务里没录成的单都已在产品待处理里列着的不另算），待处理页的「任务」一组点了切到代录、打开那个任务。

准备两条「需要你处理」的任务：一条没录成的单不在产品待处理里（算），一条没录成的单就是产品待处理里列着的一张（不另算）。ERP 那张
是临时把一张已提交的采购单标成「写账失败」放进待处理的（verify 还原）；Cowork 的回归数据里没有识别单据、待处理是空的，只建第一条
（Cowork 的去重由 `tests/test_cowork_companies.py` 与 `tests/test_express_operator_tasks.py` 守）。verify 删掉准备的任务。
设了 `PEARNLY_FLOWS_SHOTS` 时每一步截图（`run.py`）。
"""
from __future__ import annotations

import json
import uuid

TITLE = "CC 侧栏两种模式同一个框架：共有入口不挪、定时任务只对话模式、代录下点公司 / 待处理不切模式、待处理数含代录任务并按单据去重"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const sleep=ms=>new Promise(r=>setTimeout(r,ms)); const q=s=>document.querySelector(s); const qa=s=>[...document.querySelectorAll(s)];"
        "const phone=()=>innerWidth<768;"
        "const root=()=>phone()?q('.cc-mdrawer .cc-drawer'):q('[data-slot=\"sidebar\"]');"
        "const openDrawer=async()=>{if(phone()&&q('.cc-mshell')?.dataset.open!=='true'){q('[data-slot=\"cc-nav-toggle\"]').click();"
        "await until(()=>q('.cc-mshell')?.dataset.open==='true'); await sleep(600);}};"
        "const nav=()=>{const r=root(); return r?[...r.querySelectorAll('.cc-nav-item:is([data-slot=\"aui_thread-list-new\"],[data-slot=\"cc-operator-new\"],"
        "[data-slot=\"cc-nav-pending\"],[data-slot=\"cc-nav-companies\"],[data-slot=\"cc-nav-tasks\"])')].map(e=>{const b=e.getBoundingClientRect();"
        "return {slot:e.dataset.slot, text:e.innerText.trim().replace(/\\s+/g,' '), top:Math.round(b.top), left:Math.round(b.left)};}):[];};"
        "const mode=()=>q('[data-slot=\"cc-mode-switch\"] [aria-checked=\"true\"]')?.dataset.mode;"
        "const api=async(m,args)=>{const r=await fetch('/api/method/'+m+'?'+new URLSearchParams(args||{}),"
        "{headers:{'X-Frappe-CSRF-Token':window.csrf_token||''}}); return (await r.json()).message;};")

# 共有入口（两种模式都有的）在侧栏里的样子：位置、顺序（新建的字按模式换）
SHARED = ("cc-nav-pending", "cc-nav-companies")


def prepare(lab, manifest):
    product = manifest.get("product", "erp")
    user = f"admin@{product}.regression"
    lab.python(manifest["site"], f'frappe.db.set_value("User", {user!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "flow" + uuid.uuid4().hex[:6]
    out = lab.python(manifest["site"], f"""
from pearnly_core.assistant import projects
from pearnly_core.express_operator import products, tasks
frappe.set_user({user!r})
marked = []
if {product!r} == "erp":
    from pearnly_erp_portal.express import contracts, push
    company = {(manifest.get("company") or (manifest.get("companies") or [""])[0])!r}
    title = frappe.db.get_value("ERP Company", company, "company_name") or company
    if not products.pending_documents():
        marked = frappe.get_all(push.PURCHASE, filters={{"company": company, "status": "submitted"}}, pluck="name",
                                order_by="name asc", limit_page_length=1)
        for name in marked:
            frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": contracts.FAILED, "ledger_reason": "flow"}}, update_modified=False)
        frappe.db.commit()
    counts = __import__("pearnly_erp_portal.masters.cc_companies", fromlist=["x"]).pending_counts()
else:
    row = (frappe.get_all("Cowork Company", fields=["name", "title"], order_by="creation asc", limit_page_length=1) or [None])[0]
    company, title = (row.name, row.title or row.name) if row else ("CO-FLOW", "Flow Co")
    counts = __import__("pearnly_cowork.companies.pending", fromlist=["x"]).pending_counts()
listed = sorted(products.pending_documents())
before = tasks.attention()["total"]
made = []
def task(doc_id, label):
    doc = frappe.get_doc({{"doctype": tasks.DOCTYPE, "product": tasks.site_product(), "company": company, "company_title": title,
                          "period": frappe.utils.add_months(frappe.utils.today(), -1)[:7], "title": label, "status": "attention",
                          "documents": [{{"doc_id": doc_id, "state": "not_done", "reason": "flow"}}]}})
    doc.flags.ignore_permissions = True
    doc.insert()
    made.append(doc.name)
task({tag!r} + "-doc", {tag!r} + " 要你处理")
if listed:
    task(listed[0], {tag!r} + " 已在待处理")
frappe.db.commit()
after = tasks.attention()
print(json.dumps({{"product": {product!r}, "company": company, "marked": marked, "title": title, "made": made, "listed": len(listed),
                  "pending": counts["total"], "before": before, "attention": after["total"],
                  "counted": [t["name"] for t in after["tasks"]], "kind": projects.kind(),
                  "sections": projects.declared(projects.provider()).get("sections", []),
                  "own_tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": {user!r}, "product": tasks.site_product()}}),
                  "chats": len(projects.mine({user!r}))}}))
""")
    return {**out, "tag": tag, "shot": out["product"]}


def _shot(params, name):
    return {"shot": True, "file": f"{params['shot']}-{name}"}


def steps(params, camera_code=None):
    tag = json.dumps(params["tag"])
    return [
        {"name": "对话模式", **_shot(params, "1-对话模式"), "js": WAIT + "await until(()=>mode()==='chat'); await openDrawer(); "
         "await until(()=>root()&&q('[data-slot=\"cc-nav-companies\"]')); await sleep(800); return {mode: mode(), nav: nav()};"},
        {"name": "切到代录", **_shot(params, "2-代录模式"), "js": WAIT + "await openDrawer(); root().closest('.cc-mdrawer, [data-slot=\"sidebar\"]')"
         "?.querySelector('[data-mode=\"operator\"]')?.click() || q('[data-mode=\"operator\"]').click(); "
         "await until(()=>mode()==='operator'&&q('[data-slot=\"cc-operator-sidebar\"]')); await sleep(1200); "
         "const groups=qa('[data-slot=\"cc-operator-sidebar\"] [data-slot=\"cc-project\"]').length; "
         "return {mode: mode(), nav: nav(), groups, empty: !!q('[data-slot=\"cc-operator-sidebar\"] .cc-empty-line')};"},
        {"name": "代录下点公司", **_shot(params, "3-代录下公司页"), "js": WAIT + "await openDrawer(); q('[data-slot=\"cc-nav-companies\"]').click(); "
         "const page=await until(()=>q('.cc-page')&&!q('[data-slot=\"cc-op-work\"]')); await sleep(800); await openDrawer(); await sleep(300); "
         "return {page, mode: mode(), tasks: !!q('[data-slot=\"cc-operator-sidebar\"]'), active: q('[data-slot=\"cc-nav-companies\"]')?.hasAttribute('data-active'), nav: nav()};"},
        {"name": "待处理", **_shot(params, "4-待处理页"), "js": WAIT + "await openDrawer(); const item=q('[data-slot=\"cc-nav-pending\"]'); "
         "if(!item) return {error:'no pending nav', nav: nav()}; const count=Number(item.querySelector('.cc-count')?.innerText||0); item.click(); "
         "await until(()=>q('[data-slot=\"cc-pending-tasks\"]'),80); await sleep(800); "
         "const rows=qa('[data-slot=\"cc-pending-task\"]').map(e=>e.innerText.trim().split('\\n')[0]); "
         "return {mode: mode(), count, rows, tasks: !!q('[data-slot=\"cc-operator-sidebar\"]')};"},
        {"name": "打开任务", **_shot(params, "5-打开任务"), "js": WAIT +
         f"const row=qa('[data-slot=\"cc-pending-task\"]').find(e=>e.innerText.includes({tag})); if(!row) return {{error:'task row missing'}}; row.click(); "
         "const ok=await until(()=>q('[data-slot=\"cc-op-title\"]')); await sleep(600); return {ok, mode: mode(), title: q('[data-slot=\"cc-op-title\"]')?.innerText.trim()};"},
        {"name": "切回对话", **_shot(params, "6-切回对话"), "js": WAIT + "await openDrawer(); q('.cc-mdrawer [data-mode=\"chat\"]')?.click() || "
         "q('[data-slot=\"sidebar\"] [data-mode=\"chat\"]')?.click() || q('[data-mode=\"chat\"]').click(); "
         "await until(()=>mode()==='chat'&&!q('[data-slot=\"cc-operator-sidebar\"]')); await sleep(800); return {mode: mode(), nav: nav()};"},
    ]


def _shared(nav):
    return [(item["slot"], item["text"], item["top"], item["left"]) for item in nav or [] if item["slot"] in SHARED]


def _new(nav):
    return next(((item["top"], item["left"]) for item in nav or [] if item["slot"] in ("aui_thread-list-new", "cc-operator-new")), None)


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    chat, operator, companies, pending, opened, back = (by.get(k) or {} for k in ("对话模式", "切到代录", "代录下点公司", "待处理", "打开任务",
                                                                               "切回对话"))
    scheduled = "tasks" in params["sections"]
    slots = lambda step: [item["slot"] for item in step.get("nav") or []]
    want = params["pending"] + params["attention"]
    return [
        {"case": "共有入口（待处理、公司）切到代录时位置、顺序、文字一样", "seen": {"chat": _shared(chat.get("nav")), "operator": _shared(operator.get("nav"))},
         "ok": bool(_shared(chat.get("nav"))) and _shared(chat.get("nav")) == _shared(operator.get("nav"))},
        {"case": "新建在同一个位置（电脑：对话「新建」/ 代录「新建任务」；手机在底部胶囊）", "seen": {"chat": _new(chat.get("nav")), "operator": _new(operator.get("nav"))},
         "ok": _new(chat.get("nav")) == _new(operator.get("nav"))},
        {"case": "定时任务只在对话模式、只在声明了的产品，且在共有入口下面", "seen": {"chat": slots(chat), "operator": slots(operator), "sections": params["sections"]},
         "ok": ("cc-nav-tasks" in slots(chat)) == scheduled and "cc-nav-tasks" not in slots(operator)
         and (not scheduled or slots(chat)[-1] == "cc-nav-tasks")},
        {"case": "代录侧栏的公司组同对话侧栏：有对话或有任务的公司都出组头，不写「暂无任务」",
         "seen": {**{k: operator.get(k) for k in ("groups", "empty")}, "own_tasks": params["own_tasks"], "chats": params["chats"]},
         "ok": operator.get("groups", 0) >= 1 and not operator.get("empty")},
        {"case": "代录模式下点「公司」：主栏换成公司页，仍在代录（侧栏仍是任务）", "seen": companies,
         "ok": bool(companies.get("page")) and companies.get("mode") == "operator" and bool(companies.get("tasks")) and bool(companies.get("active"))},
        {"case": "待处理 N = 产品待处理 + 需要你处理的代录任务（按单据去重），页面「任务」一组条数与之对上", "seen": {**pending, "want": want,
         "pending": params["pending"], "attention": params["attention"], "made": params["made"], "counted": params["counted"]},
         "ok": pending.get("count") == want and len(pending.get("rows") or []) == params["attention"]
         and params["made"][0] in params["counted"] and (len(params["made"]) < 2 or params["made"][1] not in params["counted"])
         and pending.get("mode") == "operator" and bool(pending.get("tasks"))},
        {"case": "待处理页点任务：打开那个任务的代录页", "seen": opened,
         "ok": bool(opened.get("ok")) and opened.get("mode") == "operator" and params["tag"] in (opened.get("title") or "")},
        {"case": "切回对话：回到对话的侧栏，共有入口还在原位", "seen": {"mode": back.get("mode"), "nav": _shared(back.get("nav"))},
         "ok": back.get("mode") == "chat" and _shared(back.get("nav")) == _shared(chat.get("nav"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    product = params["product"]
    out = lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
if {product!r} == "erp":
    from pearnly_erp_portal.express import push
    for name in {params["marked"]!r}:
        frappe.db.set_value(push.PURCHASE, name, {{"ledger_state": "", "ledger_reason": ""}}, update_modified=False)
for name in {params["made"]!r}:
    frappe.delete_doc(tasks.DOCTYPE, name, ignore_permissions=True, force=True)
frappe.db.commit()
frappe.set_user("admin@{product}.regression")
print(json.dumps({{"attention": tasks.attention()["total"]}}))
""")
    return [{"case": "删掉准备的任务后「需要你处理」回到流程前", "saved": {"before": params["before"], "after": out["attention"]},
             "ok": out["attention"] == params["before"]}]
