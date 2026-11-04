"""流程用例：ERP 版 CC 按公司（ADR-020 的 ERP 一半，2026-11-02）——侧栏「公司」「待处理 N」、公司列表只见本人可见的公司、
公司页只出 ERP 声明的分区（公司、待处理、知识库；没有月结、文件夹、定时任务、记住的选择、草稿）、知识库新增落库、
待处理点开到 ERP 单据页（不交给 CC）、页面不调 Cowork 接口。待处理也列同样写账本的出入库单、收银日汇总（第 6 步账本列改通用时
补齐）：点开到入库 / 出库页带单号、收银记录页带日汇总单号。

准备：旧数据集（两家公司）里把 A 公司那张已提交的采购单标成「停下等人选」（同电脑回报的状态），给采购员只分 A 公司；
落库检查后还原单据状态、删掉这次记的知识库、撤掉分公司。只在实验室站点上改，不碰外部系统，不调模型。

「添加公司」（2026-11-04，同 Cowork 在 CC 里弹窗，不再开 ERP 新建公司页）：准备一台在用的假电脑报两份账套（A 公司的、一家还没有的），
对话模式在弹窗里点没有的那家 → 建好进它的公司页、页面没开新窗口；代录模式点已添加的 A → 进 A 的新建任务页。verify 删掉建的公司、
任务、假电脑与账套。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import ensure_documents  # noqa: E402

TITLE = "ERP 版 CC 按公司：侧栏公司与待处理、添加公司在 CC 里弹窗（对话 / 代录两种模式）、公司页只出 ERP 分区、知识库落库、待处理点开到单据页"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-slot=\"cc-company-rows\"]')"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const calls=()=>performance.getEntriesByType('resource').map(e=>e.name).filter(n=>n.includes('/api/method/'));"
        "const set=(el,v)=>{const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
        "Object.getOwnPropertyDescriptor(proto,'value').set.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true}));};"
        "const navText=()=>[...document.querySelectorAll('.cc-nav-item')].map(e=>e.innerText.trim());")


# 打开「添加公司」（同 Cowork 的 CC 弹窗）并点名字是 want 的那一行；记下页面有没有开新窗口（以前 ERP 开新建公司页）
ADD = ("let opened=''; window.open=(u)=>{opened=String(u); return null;}; "
       "[...document.querySelectorAll('button')].find(b=>b.innerText.trim()==='添加公司').click(); "
       "await until(()=>document.querySelector('[data-slot=\"cc-candidates\"] .cc-list-row'),80); "
       "const pick=(want)=>{const all=[...document.querySelectorAll('[data-slot=\"cc-candidates\"] .cc-list-row')]; "
       "const row=all.find(r=>r.innerText.split('\\n')[0].trim()===want); if(!row) return null; const seen=all.map(r=>r.innerText.trim()); "
       "row.click(); return seen;};")


def prepare(lab, manifest):
    a, b = manifest["companies"][:2]
    tag = str(uuid.uuid4().int)[:6]
    ensure_documents(lab, manifest)  # A 公司各类单据各有一张（出入库单从这里取）
    return lab.python(manifest["site"], f"""
frappe.db.set_value("User", "admin@erp.regression", "language", "zh")
# 同样写账本的单据：A 公司一张出入库单标成「停下等人选」、建一张停住的收银日汇总（同电脑回报 / 每晚汇总停住的状态），落库检查后还原 / 删掉
entry = frappe.get_all("ERP Stock Entry", filters={{"status": "submitted", "direction": ["in", ["in", "out"]],
                       "warehouse": ["in", frappe.get_all("ERP Warehouse", filters={{"company": {a!r}}}, pluck="name") or [""]]}},
                       fields=["name", "direction", "doc_no", "ledger_state", "ledger_reason"], order_by="creation asc",
                       limit_page_length=1)[0]
frappe.db.set_value("ERP Stock Entry", entry.name, {{"ledger_state": "held", "ledger_reason": "choice_needed:account"}},
                    update_modified=False)
day = frappe.get_doc({{"doctype": "ERP POS Day Summary", "company": {a!r}, "day": "2026-10-24", "seq": 99, "status": "submitted",
                      "doc_kind": "sale", "grand_total": 1, "ledger_state": "held", "ledger_reason": "express.pos_net_negative",
                      "ledger_target": "express"}}).insert(ignore_permissions=True)
row = frappe.get_all("Purchase Document", filters={{"company": {a!r}, "status": "submitted"}},
                     fields=["name", "system_no", "ledger_state", "ledger_reason"], order_by="creation asc", limit_page_length=1)[0]
frappe.db.set_value("Purchase Document", row.name, {{"ledger_state": "held", "ledger_reason": "choice_needed:party"}},
                    update_modified=False)
if not frappe.db.exists("User Permission", {{"user": "purchase@erp.regression", "allow": "ERP Company", "for_value": {a!r}}}):
    frappe.get_doc({{"doctype": "User Permission", "user": "purchase@erp.regression", "allow": "ERP Company",
                    "for_value": {a!r}, "apply_to_all_doctypes": 1}}).insert(ignore_permissions=True)
titles = {{name: frappe.db.get_value("ERP Company", name, "company_name") for name in ({a!r}, {b!r})}}
# 「添加公司」的候选：一台在用的电脑（CC 桌面版 1.12 起只报人选的数据根）报 A 公司的账套与一家还没有公司的账套
a_row = frappe.db.get_value("ERP Company", {a!r}, ["tax_id", "branch"], as_dict=True)
device = frappe.get_doc({{"doctype": "Pearnly CC Device", "device_label": "flow-pc", "token_digest": "flow", "companion_version": "1.12.12"}})
device.insert(ignore_permissions=True)
new_tax = "0105570" + {tag!r}[:6]
for key, company, tax, branch in (("flow-a", titles[{a!r}], a_row.tax_id, a_row.branch), ("flow-new", "Flow New " + {tag!r}, new_tax, "00000")):
    frappe.get_doc({{"doctype": "Pearnly CC Book", "device": device.name, "book_key": key + {tag!r}, "label": company, "company": company,
                    "tax_id": tax, "branch": branch, "period_start": "2026-01-01", "period_end": "2026-12-31", "writable": 1}}
                   ).insert(ignore_permissions=True)
tasks_before = frappe.get_all("Pearnly Express Operator Task", filters={{"owner": "admin@erp.regression"}}, pluck="name", limit_page_length=500) \
    if frappe.db.exists("DocType", "Pearnly Express Operator Task") else []
frappe.db.commit()
print(json.dumps({{"a": {a!r}, "b": {b!r}, "titles": titles, "document": row.name, "number": row.system_no, "before": [row.ledger_state or "",
                  row.ledger_reason or ""], "note": "flow note " + {uuid.uuid4().hex[:6]!r}, "cases": [],
                  "entry": entry.name, "entry_no": entry.doc_no or entry.name, "entry_direction": entry.direction,
                  "entry_before": [entry.ledger_state or "", entry.ledger_reason or ""], "day": day.name,
                  "device": device.name, "new_title": "Flow New " + {tag!r}, "new_tax": new_tax, "tasks_before": tasks_before}}))
""")


def steps(params, camera_code=None):
    a_title = params["titles"][params["a"]]
    return [
        {"name": "公司列表", "js": WAIT + "await until(()=>document.querySelectorAll('.cc-grid-card-title').length>=2,80); "
         "const titles=[...document.querySelectorAll('.cc-grid-card-title')].map(e=>e.innerText.trim()); "
         "const add=[...document.querySelectorAll('button')].some(b=>b.innerText.trim()==='添加公司'); "
         "await until(()=>navText().some(t=>t.startsWith('待处理')),40); "
         "return {titles, add, nav: navText()};"},
        {"name": "添加公司", "js": WAIT + f"const NEW={json.dumps(params['new_title'])}; " + ADD +
         "const rows=pick(NEW); if(!rows) return {error:'candidates missing'}; "
         "const page=await until(()=>document.querySelector('[data-slot=\"cc-company-page\"]')&&document.querySelector('.cc-page-title')?.innerText.trim()===NEW,80); "
         "return {rows, page, opened, dialog: !!document.querySelector('[data-slot=\"cc-add-company\"]')};"},
        {"name": "代录添加公司", "js": WAIT + f"const A={json.dumps(a_title)}; "
         "document.querySelector('[data-mode=\"operator\"]').click(); await until(()=>document.querySelector('[data-slot=\"cc-operator-sidebar\"]'),80); "
         "document.querySelector('[data-slot=\"cc-nav-companies\"]').click(); await until(()=>document.querySelector('[data-slot=\"cc-company-rows\"]'),80); " + ADD +
         "const rows=pick(A); if(!rows) return {error:'candidates missing'}; "
         "const work=await until(()=>document.querySelector('[data-slot=\"cc-op-work\"]')&&document.querySelector('[data-slot=\"cc-op-title\"]')?.innerText.includes(A),80); "
         "const title=document.querySelector('[data-slot=\"cc-op-title\"]')?.innerText.trim(); const page=!!document.querySelector('[data-slot=\"cc-company-page\"]'); "
         "document.querySelector('[data-mode=\"chat\"]').click(); await until(()=>!document.querySelector('[data-slot=\"cc-operator-sidebar\"]'),80); "
         "return {rows, work, title, page, opened};"},
        {"name": "公司页", "js": WAIT + "document.querySelector('[data-slot=\"cc-nav-companies\"]').click(); "
         "await until(()=>document.querySelectorAll('.cc-grid-card-title').length>=2,80); " + f"const card=[...document.querySelectorAll('.cc-grid-card')].find(e=>e.innerText.includes({json.dumps(a_title)})); "
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
         "const openOf=(text)=>{opened=''; const row=rows.find(r=>r.innerText.includes(text)); if(row) row.click(); return opened;}; "
         f"const entry=openOf({json.dumps(params['entry_no'])}); const day=openOf({json.dumps(params['day'])}); "
         f"(rows.find(r=>r.innerText.includes({json.dumps(params['number'])}))||rows[0])?.click(); "
         "return {rows: rows.map(r=>[r.tagName, r.innerText.trim().slice(0,120)]), opened, entry, day, "
         "hand: !!document.querySelector('[data-slot=\"cc-hand-to-cc\"]'), "
         "cowork: calls().filter(n=>n.includes('pearnly_cowork')).slice(0,5), "
         "erp: calls().filter(n=>n.includes('pearnly_erp_portal.masters.cc_companies')).length};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    listing, page, note, pending = (by.get(k) or {} for k in ("公司列表", "公司页", "知识库新增", "待处理"))
    added, hands_on = by.get("添加公司") or {}, by.get("代录添加公司") or {}
    a_title = params["titles"][params["a"]]
    titles = set(params["titles"].values())
    return [
        {"case": "公司列表是本人可见的两家公司、管理员有「添加公司」", "seen": listing,
         "ok": titles <= set(listing.get("titles") or []) and bool(listing.get("add"))},
        {"case": "对话模式「添加公司」在 CC 里弹窗（不开新窗口）：候选有已添加的 A 和电脑报来的新公司，点新公司建好进它的公司页",
         "seen": added,
         "ok": not added.get("opened") and bool(added.get("page")) and not added.get("dialog")
         and any(r.startswith(a_title) and "已添加" in r for r in added.get("rows") or [])
         and any(r.startswith(params["new_title"]) and "已添加" not in r for r in added.get("rows") or [])},
        {"case": "代录模式「添加公司」点已添加的 A：进 A 的新建任务页，不是对话公司页", "seen": hands_on,
         "ok": not hands_on.get("opened") and bool(hands_on.get("work")) and not hands_on.get("page") and a_title in (hands_on.get("title") or "")},
        {"case": "侧栏有「公司」与「待处理 N」", "seen": listing.get("nav"),
         "ok": "公司" in (listing.get("nav") or []) and any(t.startswith("待处理") for t in listing.get("nav") or [])},
        {"case": "公司页只出 ERP 的分区（公司、知识库、待处理），有「···」", "seen": page,
         "ok": page.get("title") == params["titles"][params["a"]] and "notes" in (page.get("cards") or [])
         and not {"month", "folders", "tasks", "choices", "drafts"} & set(page.get("cards") or []) and bool(page.get("menu"))},
        {"case": "知识库新增一条", "seen": note, "ok": bool(note.get("got"))},
        {"case": "待处理点开到 ERP 单据页、不交给 CC", "seen": pending,
         "ok": pending.get("opened") == f"/desk/pearnly-purchase/{params['document']}" and not pending.get("hand")
         and all(tag == "BUTTON" for tag, _text in pending.get("rows") or [["x", ""]])},
        {"case": "待处理也列出入库单与收银日汇总，点开到各自的页面", "seen": pending,
         "ok": pending.get("entry") == f"/desk/pearnly-stock-{params['entry_direction']}/{params['entry']}"
         and pending.get("day") == f"/desk/pearnly-pos-receipts/{params['day']}"},
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
made = frappe.get_all("ERP Company", filters={{"tax_id": {params['new_tax']!r}}}, fields=["name", "company_name", "branch", "express_enabled",
                      "pos_enabled"], limit_page_length=5)
warehouses = frappe.get_all("ERP Warehouse", filters={{"company": ["in", [row.name for row in made] or [""]]}}, pluck="name", limit_page_length=20)
picked = []
if frappe.db.exists("DocType", "Pearnly Express Operator Task"):
    picked = frappe.get_all("Pearnly Express Operator Task", filters={{"owner": "admin@erp.regression", "name": ["not in", {params['tasks_before']!r} + [""]]}},
                            fields=["name", "company", "period", "status"], limit_page_length=20)
    for row in picked:
        frappe.delete_doc("Pearnly Express Operator Task", row.name, ignore_permissions=True, force=True)
for name in warehouses:
    frappe.delete_doc("ERP Warehouse", name, ignore_permissions=True, force=True)
for row in made:
    frappe.delete_doc("ERP Company", row.name, ignore_permissions=True, force=True)
frappe.db.delete("Pearnly CC Book", {{"device": {params['device']!r}}})
frappe.db.delete("Pearnly CC Device", {{"name": {params['device']!r}}})
frappe.db.delete("ERP Company Note", {{"company": {params['a']!r}, "text": {params['note']!r}}})
frappe.db.set_value("Purchase Document", {params['document']!r}, {{"ledger_state": {params['before'][0]!r} or None,
                    "ledger_reason": {params['before'][1]!r}}}, update_modified=False)
frappe.db.delete("User Permission", {{"user": "purchase@erp.regression", "allow": "ERP Company", "for_value": {params['a']!r}}})
frappe.db.set_value("ERP Stock Entry", {params['entry']!r}, {{"ledger_state": {params['entry_before'][0]!r} or None,
                    "ledger_reason": {params['entry_before'][1]!r}}}, update_modified=False)
frappe.db.delete("ERP POS Day Summary", {params['day']!r})
frappe.db.commit()
print(json.dumps({{"notes": notes, "seen": seen, "made": made, "warehouses": warehouses, "picked": picked}}, default=str))
""")
    a, b = params["a"], params["b"]
    return [
        {"case": "添加公司按账套建了一家接 Express 的公司（不做收银、带一个仓），流程后删掉", "saved": {"made": out["made"], "warehouses": out["warehouses"]},
         "ok": len(out["made"]) == 1 and out["made"][0]["company_name"] == params["new_title"] and out["made"][0]["express_enabled"] == 1
         and out["made"][0]["pos_enabled"] == 0 and out["made"][0]["branch"] == "00000" and len(out["warehouses"]) >= 1},
        {"case": "代录模式选 A 在站点建了 A 的一条任务（流程后删掉）", "saved": out["picked"],
         "ok": [row["company"] for row in out["picked"]] == [a]},
        {"case": "知识库落库一条（人记的，记了谁）", "saved": out["notes"],
         "ok": len(out["notes"]) == 1 and out["notes"][0]["origin"] == "person" and out["notes"][0]["created_by"] == "admin@erp.regression"},
        {"case": "员工只分到一家：只认、只列那一家；管理员两家都在", "saved": out["seen"],
         "ok": out["seen"]["purchase@erp.regression"] == {"allowed": [a], "listed": [a]}
         and set(out["seen"]["admin@erp.regression"]["allowed"]) == {a, b}},
    ]
