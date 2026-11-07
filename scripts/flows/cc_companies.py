"""流程用例：ERP 版 CC 按公司（ADR-020 的 ERP 一半，2026-11-02；11-07 起没有侧栏「公司」与公司页，对话里点「公司」弹选择公司窗口，
公司页上的东西搬进对话右侧栏与组头「···」）——侧栏没有「公司」「待处理」入口；选择公司弹窗列本人可见的公司与电脑报来还没建的账套，
点没建的就建好、在这段新对话里用它（不开新窗口）；代录模式选已建的 A 进新建页（不建任务）；挂 A 的对话右侧栏只出 ERP 声明的分区
（公司、待处理、知识库；没有月结、文件夹、定时任务、记住的选择、草稿），公司卡与组头「···」只有「在浏览器中打开」（ERP 不在 CC 里改名、
删除）；知识库新增落库；待处理点开到 ERP 单据页（不交给 CC）；页面不调 Cowork 接口。待处理也列同样写账本的出入库单、收银日汇总：
点开到入库 / 出库页带单号、收银记录页带日汇总单号。

准备：旧数据集（两家公司）里把 A 公司那张已提交的采购单标成「停下等人选」（同电脑回报的状态），给采购员只分 A 公司；一台在用的假电脑
报两份账套（A 公司的、一家还没有的）。落库检查后还原单据状态、删掉这次记的知识库、撤掉分公司、删掉建的公司、任务、假电脑与账套。
只在实验室站点上改，不碰外部系统，模型是实验室的假回复。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_lib import JS  # noqa: E402
from erp_lib import ensure_documents  # noqa: E402

TITLE = "ERP 版 CC 按公司：对话里选公司弹窗（对话 / 代录两种模式）、右侧栏只出 ERP 分区、「···」、知识库落库、待处理点开到单据页"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-slot=\"cc-chin-company\"]')"

WAIT = JS + (  # cc_lib 的帮手（until / sleep / q / qa / say / pickCompany / toMode / press …）+ 这条流程自己的几个
    "const calls=()=>performance.getEntriesByType('resource').map(e=>e.name).filter(n=>n.includes('/api/method/'));"
    "const set=(el,v)=>{const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;"
    "Object.getOwnPropertyDescriptor(proto,'value').set.call(el,v); el.dispatchEvent(new Event('input',{bubbles:true}));};"
    "const navText=()=>[...document.querySelectorAll('.cc-nav-item')].map(e=>e.innerText.trim());")


# 新建页下巴点「公司」弹选择公司窗口，读出各行（已建的带 data-built）；记下页面有没有开新窗口（以前 ERP 开新建公司页）
OPEN = ("let opened=''; window.open=(u)=>{opened=String(u); return null;}; "
        "const box=()=>document.querySelector('[data-slot=\"cc-company-picker\"]'); "
        "document.querySelector('[data-slot=\"cc-chin-company\"]').click(); await until(box,40); "
        "const rowsNow=()=>[...box().querySelectorAll('[data-slot=\"cc-candidates\"] .cc-list-row')]; "
        "const settled=()=>!box().querySelector('[data-slot=\"cc-candidates\"] .cc-spinner')&&rowsNow().length>1; "
        # 标签（MR.ERP / Express / ERP）按下（mousedown）就切；没有标签时就是这一页
        "const tabsNow=()=>[...box().querySelectorAll('[role=\"tab\"]')]; "
        "const toTab=async t=>{t.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0})); await until(()=>t.dataset.state==='active',20); await sleep(300); await until(settled,40);}; "
        "await until(settled,80); const picker=[]; "
        "for(const t of (tabsNow().length?tabsNow():[null])){if(t) await toTab(t); "
        "picker.push(...rowsNow().map(r=>[r.innerText.split('\\n')[0].trim(), r.hasAttribute('data-built')]));} "
        "const findRow=async want=>{for(const t of (tabsNow().length?tabsNow():[null])){if(t) await toTab(t); "
        "const r=rowsNow().find(r=>r.innerText.split('\\n')[0].trim()===want); if(r) return r;} return null;};")


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
# 选择公司弹窗里还没建的账套：一台在用的电脑（CC 桌面版 1.12.17 起只报勾给 ERP 的位置）报 A 公司的账套与一家还没有公司的账套
a_row = frappe.db.get_value("ERP Company", {a!r}, ["tax_id", "branch"], as_dict=True)
from pearnly_core.cc_desktop import documents
device = documents.new(documents.DEVICE, "PCD-flow" + {tag!r}, {{"device_label": "flow-pc", "token_digest": "flow",
                                                               "companion_version": "1.12.17"}})
new_tax = "0105570" + {tag!r}[:6]
for key, company, tax, branch in (("flow-a", titles[{a!r}], a_row.tax_id, a_row.branch), ("flow-new", "Flow New " + {tag!r}, new_tax, "00000")):
    documents.new(documents.BOOK, frappe.generate_hash(length=20), {{"device": device.name, "book_key": key + {tag!r}, "label": company,
                  "company": company, "tax_id": tax, "branch": branch, "period_start": "2026-01-01", "period_end": "2026-12-31",
                  "writable": 1}})
tasks_before = frappe.get_all("Pearnly Express Operator Task", filters={{"owner": "admin@erp.regression"}}, pluck="name", limit_page_length=500) \
    if frappe.db.exists("DocType", "Pearnly Express Operator Task") else []
frappe.db.commit()
print(json.dumps({{"a": {a!r}, "b": {b!r}, "titles": titles, "document": row.name, "number": row.system_no, "before": [row.ledger_state or "",
                  row.ledger_reason or ""], "note": "flow note " + {uuid.uuid4().hex[:6]!r}, "cases": [],
                  "entry": entry.name, "entry_no": entry.doc_no or entry.name, "entry_direction": entry.direction,
                  "entry_before": [entry.ledger_state or "", entry.ledger_reason or ""], "day": day.name,
                  "device": device.name, "new_title": "Flow New " + {tag!r}, "new_tax": new_tax, "tasks_before": tasks_before,
                  "line": "flow " + {tag!r} + " 看一下待处理"}}))
""")


def steps(params, camera_code=None):
    a_title, b_title = params["titles"][params["a"]], params["titles"][params["b"]]
    return [
        {"name": "选择公司", "js": WAIT + f"const NEW={json.dumps(params['new_title'])}; " + OPEN +
         "const row=await findRow(NEW); if(!row) return {error:'new company row missing', picker}; row.click(); "
         "const chosen=await until(()=>!box()&&(document.querySelector('[data-slot=\"cc-chin-company\"]')?.innerText||'').includes(NEW),80); "
         "return {seen: picker, chosen, opened, nav: navText()};"},
        {"name": "代录选公司", "js": WAIT + f"const A={json.dumps(a_title)}; "
         "if(!await toMode('operator')) return {error:'no operator mode'}; const picked=await pickCompany(A); "
         # 代录模式下选已建的 A = 直写的新建页、下巴选好 A（统一对话框架：没说话不建任务）
         "const work=!!document.querySelector('.aui-thread-welcome-root'); const inMode=mode(); await toMode('chat'); "
         "return {picked, work, mode: inMode};"},
        {"name": "公司对话", "js": WAIT + f"const A={json.dumps(a_title)}; const LINE={json.dumps(params['line'])}; "
         "document.querySelector('[data-slot=\"aui_thread-list-new\"], .aui-thread-list-new')?.click(); await sleep(400); "
         "if(!await pickCompany(A)) return {error:'pick A failed'}; if(!await say(LINE)) return {error:'send failed'}; "
         "await until(()=>document.querySelector('[data-slot=\"cc-session-panel\"] [data-card=\"notes\"]'),120); "
         "const cards=[...document.querySelectorAll('[data-slot=\"cc-session-panel\"] [data-card]')].map(e=>e.dataset.card); "
         "const more=document.querySelector('[data-slot=\"cc-company-more\"]'); let items=[]; "
         "if(more){press(more); await until(()=>q('[role=\"menu\"] [data-slot^=\"cc-company-\"]'),40); await sleep(300); items=qa('[role=\"menu\"] [data-slot^=\"cc-company-\"]').map(e=>e.dataset.slot); "
         "document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); await sleep(300);} "
         "return {cards, more: !!more, items, nav: navText()};", "wait_ms": 500},
        {"name": "组头菜单", "js": WAIT + f"const A={json.dumps(a_title)}; "
         "const head=qa('[data-slot=\"cc-project\"]').find(e=>e.innerText.includes(A)); if(!head) return {error:'no group header'}; "
         "const more=head.querySelector('.cc-group-more'); press(more); await until(()=>q('[data-slot=\"cc-group-sort-menu\"] [data-slot^=\"cc-company-\"]'),40); await sleep(300); "
         "const menu=q('[data-slot=\"cc-group-sort-menu\"]'); const items=[...menu.querySelectorAll('[data-slot^=\"cc-company-\"]')].map(e=>e.dataset.slot); "
         "const sort=!!menu.querySelector('[aria-haspopup]'); document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); await sleep(300); "
         "return {items, sort};"},
        {"name": "知识库新增", "js": WAIT + "document.querySelector('[data-card=\"notes\"] button[aria-label=\"添加\"]').click(); "
         "await until(()=>document.querySelector('[data-slot=\"cc-note-editor\"] textarea'),40); "
         f"set(document.querySelector('[data-slot=\"cc-note-editor\"] textarea'), {json.dumps(params['note'])}); "
         "await new Promise(r=>setTimeout(r,300)); "
         "[...document.querySelectorAll('[data-slot=\"cc-note-editor\"] button')].find(b=>b.innerText.trim()==='保存').click(); "
         f"const got=await until(()=>document.querySelector('[data-card=\"notes\"]').innerText.includes({json.dumps(params['note'])})&&!document.querySelector('[data-slot=\"cc-note-editor\"]'),80); "
         "return {got, text: document.querySelector('[data-card=\"notes\"]').innerText.slice(0,300)};"},
        # 待处理 11-04 起并进对话：提醒链接 ?view=pending&company= 打开服务端指定的那段「公司 · 月份」对话，那个月的在末尾
        # 「需要处理」框，别的月在右侧栏「待处理」卡；两处的行同一个样子
        {"name": "待处理链接", "js": f"location.href='/assistant?view=pending&company={params['a']}'; return 1;", "wait_ms": 6000},
        {"name": "待处理", "js": WAIT + "const rowsAll=()=>[...document.querySelectorAll('[data-slot=\"cc-pending-row\"]')]; "
         "await until(()=>document.querySelector('[data-slot=\"cc-pending-in-chat\"]'),120); "
         "if(!await until(()=>rowsAll().length>=3,80)) return {error:'pending rows missing', rows: rowsAll().map(r=>r.innerText.trim().slice(0,80)), nav: navText()}; "
         "await new Promise(r=>setTimeout(r,800)); "
         "const prow=rowsAll(); let opened=''; window.open=(u)=>{opened=String(u); return null;}; "
         "const openOf=(text)=>{opened=''; const row=prow.find(r=>r.innerText.includes(text)); if(row) row.click(); return opened;}; "
         f"const entry=openOf({json.dumps(params['entry_no'])}); const day=openOf({json.dumps(params['day'])}); "
         f"(prow.find(r=>r.innerText.includes({json.dumps(params['number'])}))||prow[0])?.click(); "
         "return {rows: prow.map(r=>[r.tagName, r.innerText.trim().slice(0,120)]), opened, entry, day, "
         "hand: !!document.querySelector('[data-slot=\"cc-hand-to-cc\"]'), "
         "box: !!q('[data-slot=\"cc-pending-in-chat\"]'), "
         "cowork: calls().filter(n=>n.includes('pearnly_cowork')).slice(0,5), "
         "erp: calls().filter(n=>n.includes('pearnly_erp_portal.masters.cc_companies')).length};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    picked, hands_on, chat, group, note, pending = (by.get(k) or {} for k in ("选择公司", "代录选公司", "公司对话", "组头菜单", "知识库新增", "待处理"))
    a_title, b_title = params["titles"][params["a"]], params["titles"][params["b"]]
    seen = {title: built for title, built in picked.get("seen") or []}
    return [
        {"case": "选择公司弹窗列本人可见的两家（已建）与电脑报来还没建的那家；点没建的就建好、下巴换成它，不开新窗口", "seen": picked,
         "ok": seen.get(a_title) is True and seen.get(b_title) is True and seen.get(params["new_title"]) is False
         and bool(picked.get("chosen")) and not picked.get("opened")},
        {"case": "代录模式在弹窗里选已建的 A：进新建页、下巴选好 A（仍在代录）", "seen": hands_on,
         "ok": bool(hands_on.get("picked")) and bool(hands_on.get("work")) and hands_on.get("mode") == "operator"},
        {"case": "侧栏没有「公司」「待处理」入口（11-07 公司页删了，11-04 待处理并进对话）", "seen": picked.get("nav"),
         "ok": bool(picked.get("nav")) and not any(t == "公司" or t.startswith("待处理") for t in picked.get("nav") or [])},
        {"case": "挂 A 的对话右侧栏只出 ERP 的分区（公司、知识库、待处理）；公司卡「···」只有在浏览器中打开", "seen": chat,
         "ok": "notes" in (chat.get("cards") or []) and "company" in (chat.get("cards") or [])
         and not {"month", "folders", "tasks", "choices", "drafts"} & set(chat.get("cards") or [])
         and chat.get("items") == ["cc-company-desk"]},
        {"case": "组头「···」上半段是这家的在浏览器中打开，下半段照旧分组和排序", "seen": group,
         "ok": group.get("items") == ["cc-company-desk"] and bool(group.get("sort"))},
        {"case": "知识库新增一条（右侧栏知识库卡）", "seen": note, "ok": bool(note.get("got"))},
        {"case": "提醒链接打开这家这个月的对话：末尾「需要处理」框与右侧栏待处理卡；点开到 ERP 单据页、不交给 CC", "seen": pending,
         "ok": bool(pending.get("box")) and pending.get("opened") == f"/desk/pearnly-purchase/{params['document']}" and not pending.get("hand")
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
        {"case": "弹窗里选中没建的账套就建了一家接 Express 的公司（不做收银、带一个仓），流程后删掉", "saved": {"made": out["made"], "warehouses": out["warehouses"]},
         "ok": len(out["made"]) == 1 and out["made"][0]["company_name"] == params["new_title"] and out["made"][0]["express_enabled"] == 1
         and out["made"][0]["pos_enabled"] == 0 and out["made"][0]["branch"] == "00000" and len(out["warehouses"]) >= 1},
        {"case": "代录模式选 A 只进新建页、没说话：站点没建任务（任务随第一句话与会话一起建）", "saved": out["picked"],
         "ok": out["picked"] == []},
        {"case": "知识库落库一条（人记的，记了谁）", "saved": out["notes"],
         "ok": len(out["notes"]) == 1 and out["notes"][0]["origin"] == "person" and out["notes"][0]["created_by"] == "admin@erp.regression"},
        {"case": "员工只分到一家：只认、只列那一家；管理员两家都在", "saved": out["seen"],
         "ok": out["seen"]["purchase@erp.regression"] == {"allowed": [a], "listed": [a]}
         and set(out["seen"]["admin@erp.regression"]["allowed"]) == {a, b}},
    ]
