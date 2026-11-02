"""流程用例：ERP 版 CC 按公司（ADR-020 的 ERP 一半，2026-11-02）——侧栏「公司」「待处理 N」、公司列表只见本人可见的公司、
公司页只出 ERP 声明的分区（公司、待处理、知识库；没有月结、文件夹、定时任务、记住的选择、草稿）、知识库新增落库、
待处理点开到 ERP 单据页（不交给 CC）、页面不调 Cowork 接口。

准备：旧数据集（两家公司）里把 A 公司那张已提交的采购单标成「停下等人选」（同电脑回报的状态），给采购员只分 A 公司；
落库检查后还原单据状态、删掉这次记的知识库、撤掉分公司。只在实验室站点上改，不碰外部系统，不调模型。
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 版 CC 按公司：侧栏公司与待处理、公司页只出 ERP 分区、知识库落库、待处理点开到单据页"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-slot=\"cc-company-rows\"]')"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const calls=()=>performance.getEntriesByType('resource').map(e=>e.name).filter(n=>n.includes('/api/method/'));"
        "const set=(el,v)=>{const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
        "Object.getOwnPropertyDescriptor(proto,'value').set.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true}));};"
        "const navText=()=>[...document.querySelectorAll('.cc-nav-item')].map(e=>e.innerText.trim());")


def prepare(lab, manifest):
    a, b = manifest["companies"][:2]
    return lab.python(manifest["site"], f"""
frappe.db.set_value("User", "admin@erp.regression", "language", "zh")
row = frappe.get_all("Purchase Document", filters={{"company": {a!r}, "status": "submitted"}},
                     fields=["name", "system_no", "ledger_state", "ledger_reason"], order_by="creation asc", limit_page_length=1)[0]
frappe.db.set_value("Purchase Document", row.name, {{"ledger_state": "held", "ledger_reason": "choice_needed:party"}},
                    update_modified=False)
if not frappe.db.exists("User Permission", {{"user": "purchase@erp.regression", "allow": "ERP Company", "for_value": {a!r}}}):
    frappe.get_doc({{"doctype": "User Permission", "user": "purchase@erp.regression", "allow": "ERP Company",
                    "for_value": {a!r}, "apply_to_all_doctypes": 1}}).insert(ignore_permissions=True)
titles = {{name: frappe.db.get_value("ERP Company", name, "company_name") for name in ({a!r}, {b!r})}}
frappe.db.commit()
print(json.dumps({{"a": {a!r}, "b": {b!r}, "titles": titles, "document": row.name, "number": row.system_no, "before": [row.ledger_state or "",
                  row.ledger_reason or ""], "note": "flow note " + {uuid.uuid4().hex[:6]!r}, "cases": []}}))
""")


def steps(params, camera_code=None):
    a_title = params["titles"][params["a"]]
    return [
        {"name": "公司列表", "js": WAIT + "await until(()=>document.querySelectorAll('.cc-grid-card-title').length>=2,80); "
         "const titles=[...document.querySelectorAll('.cc-grid-card-title')].map(e=>e.innerText.trim()); "
         "const add=[...document.querySelectorAll('button')].some(b=>b.innerText.trim()==='添加公司'); "
         "await until(()=>navText().some(t=>t.startsWith('待处理')),40); "
         "return {titles, add, nav: navText()};"},
        {"name": "公司页", "js": WAIT + f"const card=[...document.querySelectorAll('.cc-grid-card')].find(e=>e.innerText.includes({json.dumps(a_title)})); "
         "if(!card) return {error:'company card not found'}; card.click(); "
         "await until(()=>document.querySelector('[data-slot=\"cc-company-page\"]'),80); "
         "await until(()=>document.querySelector('[data-card=\"notes\"]'),80); "
         "const cards=[...document.querySelectorAll('[data-card]')].map(e=>e.dataset.card); "
         "return {cards, title: document.querySelector('.cc-page-title')?.innerText.trim(), "
         "menu: !!document.querySelector('.cc-page-head button[aria-label=\"更多\"]')};", "wait_ms": 500},
        {"name": "知识库新增", "js": WAIT + "document.querySelector('[data-card=\"notes\"] button[aria-label=\"添加\"]').click(); "
         "await until(()=>document.querySelector('[data-slot=\"cc-note-editor\"] textarea'),40); "
         f"set(document.querySelector('[data-slot=\"cc-note-editor\"] textarea'), {json.dumps(params['note'])}); "
         "await new Promise(r=>setTimeout(r,300)); "
         "[...document.querySelectorAll('[data-slot=\"cc-note-editor\"] button')].find(b=>b.innerText.trim()==='保存').click(); "
         f"const got=await until(()=>document.querySelector('[data-card=\"notes\"]').innerText.includes({json.dumps(params['note'])})&&!document.querySelector('[data-slot=\"cc-note-editor\"]'),80); "
         "return {got, text: document.querySelector('[data-card=\"notes\"]').innerText.slice(0,300)};"},
        {"name": "待处理", "js": WAIT + "const nav=[...document.querySelectorAll('.cc-nav-item')].find(e=>e.innerText.trim().startsWith('待处理')); "
         "if(!nav) return {error:'no pending nav', nav: navText()}; nav.click(); "
         # 公司页会话栏里本来就有「待处理」卡（同一种行）：先等换成待处理页，再等它的行
         "await until(()=>document.querySelector('.cc-page-title')?.innerText.trim()==='待处理'&&!document.querySelector('[data-slot=\"cc-company-page\"]'),80); "
         "await until(()=>document.querySelector('[data-slot=\"cc-pending-row\"]'),80); "
         "const rows=[...document.querySelectorAll('[data-slot=\"cc-pending-row\"]')]; let opened=''; window.open=(u)=>{opened=String(u); return null;}; "
         f"(rows.find(r=>r.innerText.includes({json.dumps(params['number'])}))||rows[0])?.click(); "
         "return {rows: rows.map(r=>[r.tagName, r.innerText.trim().slice(0,120)]), opened, "
         "hand: !!document.querySelector('[data-slot=\"cc-hand-to-cc\"]'), "
         "cowork: calls().filter(n=>n.includes('pearnly_cowork')).slice(0,5), "
         "erp: calls().filter(n=>n.includes('pearnly_erp_portal.masters.cc_companies')).length};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    listing, page, note, pending = (by.get(k) or {} for k in ("公司列表", "公司页", "知识库新增", "待处理"))
    titles = set(params["titles"].values())
    return [
        {"case": "公司列表是本人可见的两家公司、管理员有「添加公司」", "seen": listing,
         "ok": titles <= set(listing.get("titles") or []) and bool(listing.get("add"))},
        {"case": "侧栏有「公司」与「待处理 N」", "seen": listing.get("nav"),
         "ok": "公司" in (listing.get("nav") or []) and any(t.startswith("待处理") for t in listing.get("nav") or [])},
        {"case": "公司页只出 ERP 的分区（公司、知识库、待处理），有「···」", "seen": page,
         "ok": page.get("title") == params["titles"][params["a"]] and "notes" in (page.get("cards") or [])
         and not {"month", "folders", "tasks", "choices", "drafts"} & set(page.get("cards") or []) and bool(page.get("menu"))},
        {"case": "知识库新增一条", "seen": note, "ok": bool(note.get("got"))},
        {"case": "待处理点开到 ERP 单据页、不交给 CC", "seen": pending,
         "ok": pending.get("opened") == f"/desk/pearnly-purchase/{params['document']}" and not pending.get("hand")
         and all(tag == "BUTTON" for tag, _text in pending.get("rows") or [["x", ""]])},
        {"case": "页面不调 Cowork 接口、公司接口走 ERP 声明的前缀", "seen": pending,
         "ok": not pending.get("cowork") and (pending.get("erp") or 0) >= 3},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import cc_projects, cc_service
notes = frappe.get_all("ERP Company Note", filters={{"company": {params['a']!r}, "text": {params['note']!r}}},
                       fields=["name", "origin", "created_by"], limit_page_length=5)
seen = {{}}
for user in ("admin@erp.regression", "purchase@erp.regression"):
    frappe.set_user(user)
    seen[user] = {{"allowed": [c for c in ({params['a']!r}, {params['b']!r}) if cc_projects.allowed(c, user)],
                  "listed": [row["id"] for row in cc_service.listing()]}}
frappe.set_user("Administrator")
frappe.db.delete("ERP Company Note", {{"company": {params['a']!r}, "text": {params['note']!r}}})
frappe.db.set_value("Purchase Document", {params['document']!r}, {{"ledger_state": {params['before'][0]!r} or None,
                    "ledger_reason": {params['before'][1]!r}}}, update_modified=False)
frappe.db.delete("User Permission", {{"user": "purchase@erp.regression", "allow": "ERP Company", "for_value": {params['a']!r}}})
frappe.db.commit()
print(json.dumps({{"notes": notes, "seen": seen}}, default=str))
""")
    a, b = params["a"], params["b"]
    return [
        {"case": "知识库落库一条（人记的，记了谁）", "saved": out["notes"],
         "ok": len(out["notes"]) == 1 and out["notes"][0]["origin"] == "person" and out["notes"][0]["created_by"] == "admin@erp.regression"},
        {"case": "员工只分到一家：只认、只列那一家；管理员两家都在", "saved": out["seen"],
         "ok": out["seen"]["purchase@erp.regression"] == {"allowed": [a], "listed": [a]}
         and set(out["seen"]["admin@erp.regression"]["allowed"]) == {a, b}},
    ]
