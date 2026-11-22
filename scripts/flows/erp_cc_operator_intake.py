"""流程用例：ERP CC 代录里放进来的票走完「开始前卡片 → 记下 → 写入计划 → 开始 → 提交成 ERP 单、交进写账」（H3 / H4，11-16 真机：
卡片作答 403、写入计划判「未找到」、CC 说没有上传功能）。

放进来的票识别后是收单记录 ``I:<记录>``（`express.operator`），开录时才用网页同一个入账函数提交成采购 / 销售单。实验栈没有识别，
「放票 → 交识别」那一段由 `cc.operator.upload-on-send` 覆盖；这里按识别完成的收单记录起步（同 `erp_line_card_lines` 造记录的做法：
本人从代录对话放进来的、这家公司的、识别结果已在），其余每一步都在页面上点：

1. 打开代录对话：开始前卡片列出这张票要人定的几项（付款与 Express 那几项；行物料、仓库照 H3 真机那张票已定）。
2. 在卡片上逐题作答、发送：记下成功（修前这里报「没有权限」）。答案写回收单记录。
3. 说一句让 CC 建写入计划（假智能体照真实引擎调 ``pearnly_write_plan``）：这张票在要写的里（修前判 ``not_found``）。
4. 再说一句开始（``pearnly_write_start``）：收单记录提交成 ERP 单据，交进写账（实验栈没有执行电脑，交单后照常排着等电脑）。
5. 照电脑领单、写成回执（正式回执路径），同时另一条线程以任务主人身份连查进度（``express_operator_status``，11-17 验收：领单那一刻
   报「出错」）：一次都不出错；写成后任务这一行带 Express 单号，对话以一句「已录入：单号」收尾、只有一条（主会话 11-19 方案 A）。

方向由模块常量 ``DIRECTION`` 定（采购本模块，销售 `erp_cc_operator_intake_sales`）。模型调用换成实验室的假智能体（`lab_agents.py`）。
cleanup 作废提交出的单、撤掉写账件，删掉会话、任务与收单记录。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from erp_lib import company, void_documents

TITLE = "ERP CC 代录放进来的票：开始前卡片作答记下、写入计划列入、开始后提交成 ERP 单并交进写账（H3 / H4）"
USER = "admin@erp.regression"
DIRECTION = "purchase"
DOCTYPES = {"purchase": "Purchase Document", "sales": "ERP Sales Document"}
CARD = "q('[data-slot=\"cc-op-before-ask\"]')"  # 有要人定的题时开始前卡片就是这张问答卡


NOT_CONNECTED = "未连接电脑"  # 单据页同一句（ERP 词条 No computer connected 的中文，B60 11-16 用户批准，原「CC 桌面版未连接」）
OTHER_USER = "flow.other.person@erp.regression"


def prepare(lab, manifest, direction=None, no_computer=False, new_item=False):
    direction = direction or DIRECTION
    co, tag = company(manifest), "opi" + uuid.uuid4().hex[:6]
    if no_computer:
        # 发单人自己一台登录过 CC 的电脑都没有：实验栈里的电脑只算别人登录过（格结束时 `restore_fields` 还原）
        found = lab.python(manifest["site"], f"""
from pearnly_core.cc_desktop import contracts
print(json.dumps({{"devices": frappe.get_all("Pearnly CC Device", filters={{"retired_at": ["is", "not set"]}}, pluck="name",
                                             limit_page_length=20), "users": contracts.with_user(None, {OTHER_USER!r})}}))
""")
        for device in found["devices"]:
            lab.set_fields(manifest["site"], "Pearnly CC Device", device, {"signed_in_users": found["users"]})
    out = lab.python(manifest["site"], f"""
import base64, hashlib
from pearnly_core.assistant import sessions
from pearnly_erp_portal.integrations import intake, line_items
frappe.db.set_value("User", {USER!r}, "language", "zh")
frappe.set_user({USER!r})
period = frappe.utils.today()[:7]
made = sessions.create(project={co!r}, period=period, mode="operator")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {tag!r})
pixel = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
file = frappe.get_doc({{"doctype": "File", "file_name": "{tag}.png", "is_private": 1, "content": pixel}}).insert()
day = frappe.utils.today()
lines = [{{"item_type": "goods", "description": "ของใหม่ {tag}", "qty": 2, "unit_price": 100, "vat_rate": 7}}]
printed = {{"total": "214.00"}}  # 票面印的合计（同真识别读出的；写账前按它核，`express.contracts.printed_hold`）
if {direction!r} == "purchase":
    result = {{"doc_no": "{tag.upper()}-1", "doc_date": day, "doc_kind": "purchase_invoice", "has_vat": 1,
              "supplier_name": "flow intake supplier {tag}", "lines": lines, "printed": printed}}
else:
    # 销售识别的税率在表头（`sales.recognition` 的 vat_rate，行上不带）：只写在行上时提交出的销售单税额为 0，票面 214 对不上停下
    result = {{"doc_no": "{tag.upper()}-1", "doc_date": day, "doc_kind": "receipt", "has_vat": 1, "vat_rate": 7,
              "buyer_name": "flow intake buyer {tag}", "party_type": "company", "lines": lines, "printed": printed}}
doc = frappe.get_doc({{"doctype": intake.DOCTYPE, "status": intake.READY, "direction": {direction!r}, "owner": frappe.session.user,
                       "sent_by": frappe.session.user, "source": intake.SOURCE_CC, "session_id": made["id"],
                       "request_key": "flow-{tag}", "language": "zh", "media_type": "image", "company": {co!r},
                       "party": "@doc", "is_paid": 1, "payment_method": "cash", "source_file": file.file_url,
                       "source_file_name": file.name, "file_hash": hashlib.sha256(pixel).hexdigest(),
                       "received_at": frappe.utils.now(), "result": json.dumps(result, ensure_ascii=False)}}).insert(ignore_permissions=True)
value = line_items.prepare(doc, result)
# 同 H3 真机那张票：行已认出物料（这里认成 Express 同步来的那件）、仓库按这家第一个启用的仓定好，卡片问付款与 Express 那几项；
# 行没认出、仓库没定时那几问由单测守住
product = frappe.db.get_value("ERP Product", {{"express_company": {co!r}, "express_code": ["is", "set"], "is_active": 1}}, "name")
if not {new_item!r}:
    value["lines"] = [{{**line, "item_type": "goods", "product": product, "new": False, "decided": True}} for line in value["lines"]]
else:
    # 11-20 线上 H3：行上的物料认不出（新物料），开始前卡片要问物料题（只能搜、预选「新建」）
    value["lines"] = [{{**line, "item_type": "goods", "product": None, "new": False, "decided": False}} for line in value["lines"]]
value["warehouse"] = value.get("warehouse") or frappe.get_all("ERP Warehouse", filters={{"company": {co!r}, "is_active": 1}},
                                                              pluck="name", order_by="express_code asc", limit_page_length=1)[0]
frappe.db.set_value(intake.DOCTYPE, doc.name, "line_choices", json.dumps(value))
# 实验栈里这家公司的账套主档没落地（写账准备停在「主档读取中」）：放一份最小主档（同 `erp_express_new_item`），cleanup 删掉
import os
from pearnly_erp_portal.express import facts, push, target
key = target.pick(push._company({co!r}), day)["book"]["key"]
seeded = facts.load(key) is None
if seeded:
    mapping = {{"fallback_acc": "51-03-00-00", "vat_input_acc": "11-06-01-00", "ap_acc": "21-01-01-00", "revenue_acc": "41-01-00-00",
               "vat_output_acc": "21-04-01-00", "ar_acc": "11-03-01-00"}}
    facts.save(key, {{"version": 2, "customers": [], "suppliers": [], "products": [], "warehouses": [{{"code": "01", "name": "คลัง Flow"}}],
                     "settings": {{"mainloc": "01"}}, "stock": [], "departments": [],
                     "facts": {{"accounts": [{{"code": code, "name": code, "type": code[0]}} for code in (*mapping.values(), "11-01-01-00",
                                                                                                    "11-04-01-00", "11-04-02-00")],
                               # 认不出的物料行要在 Express 新建库存品：给两个合格的存货科目组（同真账套），卡片问选哪个；
                               # 一个都没有时整张单停在「缺存货科目组」，卡片不出题（11-22 GitHub Flows）
                               "mapping": mapping, "catalog": {{"isacc_candidates": [
                                   {{"code": "DM", "desc": "วัตถุดิบ", "inv_acc": "11-04-01-00", "fit": True, "used_by": 0}},
                                   {{"code": "P002", "desc": "สินค้า", "inv_acc": "11-04-02-00", "fit": True, "used_by": 0}}]
                                   if {new_item!r} else []}}}}}})
frappe.db.commit()
print(json.dumps({{"session": made["id"], "task": made["task"], "intake": doc.name, "period": period, "book": key, "seeded": seeded}}))
""")
    return {**out, "company": co, "tag": tag, "direction": direction, "doc_id": "I:" + out["intake"], "no_computer": no_computer,
            "new_item": new_item, "cases": []}


def cleanup(lab, manifest, params):
    """提交出的单作废、撤掉写账件；会话（连任务）与收单记录删掉。成功、失败、超时都跑（`run.run_flow`）。"""
    made = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP LINE Intake", {params['intake']!r}, ["purchase", "sales_document"], as_dict=True) or {{}}
print(json.dumps({{"made": row.get("purchase") if {params['direction']!r} == "purchase" else row.get("sales_document")}}))
""")
    if made.get("made"):
        void_documents(lab, manifest["site"], [made["made"]], doctype=DOCTYPES[params["direction"]])
    lab.python(manifest["site"], f"""
import os
from pearnly_core.assistant import sessions
from pearnly_core.express_operator import tasks
# 先删准备时放的主档种子：后面哪一步出错也不留给同一站点的下一格（11-16：留下的主档让 cc.operator 的单被排进写账队列）
if {bool(params.get('seeded'))!r}:
    from pearnly_erp_portal.express import facts
    path = facts._path({params.get('book', '')!r})
    if os.path.exists(path):
        os.remove(path)
frappe.set_user({USER!r})
# 排着等电脑的任务不能删（`tasks.delete`）：单据已作废，先按站点收尾；仍在排队的（实验栈没有电脑来领）标停止
if frappe.db.exists(tasks.DOCTYPE, {params['task']!r}):
    tasks.settle({params['task']!r})
    if frappe.db.get_value(tasks.DOCTYPE, {params['task']!r}, "status") in ("queued", "running"):
        frappe.db.set_value(tasks.DOCTYPE, {params['task']!r}, "status", "stopped", update_modified=False)
    frappe.db.commit()
if frappe.db.exists("Pearnly Assistant Session", {{"session_id": {params['session']!r}}}):
    sessions.delete({params['session']!r})
frappe.set_user("Administrator")
if frappe.db.exists("ERP LINE Intake", {params['intake']!r}):
    frappe.delete_doc("ERP LINE Intake", {params['intake']!r}, ignore_permissions=True, force=True)
frappe.db.commit()
print("{{}}")
""")


def steps(params, camera_code=None):
    tag = params["tag"]
    plan_line = tag + " 写入 [lab-tool]" + json.dumps({"name": "pearnly_write_plan", "input": {}}) + "[/lab-tool]"
    card = CARD
    return [
        # 同真机：用户先说一句要录（卡片在这一轮之后出）
        {"name": "开始前卡片", "shot": True, "file": "1-card", "js": JS +
         "await until(composerReady,240); "
         f"const said=await say({json.dumps(tag + ' 把这张票录进 Express')}); "
         "await until(()=>qa('[data-role=\"assistant\"]').some(e=>e.innerText.includes('[lab-agent]')),160); "
         f"const got=await until(()=>{card}?.querySelector('[data-slot=\"cc-askq-question\"]'),240); await sleep(600); "
         f"const c={card}; return {{got: !!got, question: c?.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
         "nth: c?.querySelector('.cc-askq-nth')?.innerText.trim()||'', text: (c?.innerText||'').slice(0,600)};"},
        # 逐题：有预选的照预选，没有的点第一项；最后一题的主按钮就是发送（卡片调 express_operator.api.remember）
        {"name": "作答发送", "shot": True, "file": "2-answered", "js": JS +
         "const asked=[]; let failure='', answer=''; "
         "for(let k=0;k<12;k++){ const c=" + card + "; if(!c) break; "
         " answer=c.querySelector('[data-slot=\"cc-ask-answer\"]')?.innerText.trim()||''; "
         " failure=c.querySelector('[data-slot=\"cc-ask-failure\"]')?.innerText.trim()||''; if(answer||failure) break; "
         " const qn=c.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||''; "
         " const opts=[...c.querySelectorAll('[data-slot=\"cc-askq-option\"]')]; "
         " if(opts.length&&!opts.some(o=>o.getAttribute('aria-checked')==='true')) opts[0].click(); "
         # 大列表（物料）只有「其他」搜索框：输入（空白）出候选，点第一条（不带搜索词时第一条是「新建」）
         " if(!opts.length){ const other=c.querySelector('[data-slot=\"cc-askq-other-option\"]'); if(other){ other.click(); await sleep(200); "
         "   const box=c.querySelector('[data-slot=\"cc-askq-other\"] input'); if(box){ box.focus(); "
         "   Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(box,' '); "
         "   box.dispatchEvent(new Event('input',{bubbles:true})); } "
         "   await until(()=>qa('[data-slot=\"cc-askq-choice\"]').length>0,80); qa('[data-slot=\"cc-askq-choice\"]')[0]?.click(); } } "
         " await sleep(300); asked.push({q: qn, options: opts.map(o=>o.innerText.trim())}); const n=c.querySelector('[data-slot=\"cc-askq-next\"]'); if(!n||n.disabled) break; n.click(); "
         " await until(()=>{const d=" + card + "; return !d || d.querySelector('[data-slot=\"cc-ask-answer\"],[data-slot=\"cc-ask-failure\"]') "
         "  || (d.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'')!==qn;},80); await sleep(500); } "
         "return {asked, answer, failure};"},
        {"name": "建写入计划", "js": JS + f"const sent=await say({json.dumps(plan_line)}); "
         "await until(()=>!!toolResult('pearnly_write_plan'),240); const r=toolResult('pearnly_write_plan')||{}; "
         "await until(()=>!!q('button.aui-composer-send'),80); "
         "return {sent, status: r.status||'', plan: r.plan||'', count: r.count||0, documents: r.documents||[], skipped: r.skipped||[], "
         "error: String(r.error||r.reason||r.message||'').slice(0,200)};"},
        {"name": "开始", "shot": True, "file": "3-started", "js": JS +
         "const planId=(toolResult('pearnly_write_plan')||{}).plan||''; if(!planId) return {error:'no plan'}; "
         f"const line={json.dumps(tag + ' 录吧 [lab-tool]')}+JSON.stringify({{name:'pearnly_write_start', input:{{plan:planId}}}})+'[/lab-tool]'; "
         "const sent=await say(line); await until(()=>!!toolResult('pearnly_write_start'),240); const r=toolResult('pearnly_write_start')||{}; "
         "return {sent, status: r.status||'', count: r.count||0, documents: r.documents||[], "
         "handed: ((r.handed||{}).results||[]).map(x=>({doc_id:x.doc_id, status:x.status, reason:String(x.reason||'').slice(0,120)})), "
         "no_computer: r.no_computer||'', note: String(r.note||'').slice(0,200)};"},
    ] + ([
        # 发单人没有自己的电脑：对话里的代录进度写明「未连接电脑」（单据页同一句），不只「等待电脑」（页面靠补拉，等一会儿）
        {"name": "未连接", "shot": True, "file": "4-not-connected", "js": JS +
         # 只看这张单的代录步骤行（展开「填写了单据」），不看对话正文（假智能体会把工具结果原样回显）；页面靠补拉，等一会儿
         "const row=()=>qa('[data-slot=\"cc-op-row\"]').map(e=>e.innerText.trim()).join(' | '); "
         "let shown=false; for(let k=0;k<30&&!shown;k++){ await openSteps('cc-op-row'); "
         f"shown=row().includes({json.dumps(NOT_CONNECTED)}); if(!shown) await sleep(2000); }} "
         "return {seen: shown, row: row().slice(0,300)};"},
    ] if params.get("no_computer") else [])


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    card, answered, planned, started = (by.get(k) or {} for k in ("开始前卡片", "作答发送", "建写入计划", "开始"))
    doc_id = params["doc_id"]
    handed = {row.get("doc_id"): row for row in started.get("handed") or []}
    return [
        {"case": "the start card asks about the dropped ticket", "seen": {k: card.get(k) for k in ("got", "question", "nth")},
         "ok": card.get("got") is True},
        {"case": "answering the start card is saved, not refused", "seen": answered,
         "ok": bool(answered.get("answer")) and not answered.get("failure")},
        {"case": "the write plan writes the dropped ticket (not skipped as not found)", "seen": planned,
         "ok": planned.get("status") == "ready" and doc_id in (planned.get("documents") or [])},
        {"case": "starting hands the ticket in as an ERP document", "seen": started,
         "ok": started.get("status") == "started" and doc_id in (started.get("documents") or [])
         and handed.get(doc_id, {}).get("status") not in (None, "", "failed", "not_permitted")},
    ] + ([
        {"case": "with no computer of the sender, CC is told it is not connected instead of handed to a computer",
         "seen": {k: started.get(k) for k in ("no_computer", "note")},
         "ok": started.get("no_computer") == NOT_CONNECTED and "handed to the computer" not in (started.get("note") or "").lower()},
        {"case": "the conversation shows No computer connected", "seen": by.get("未连接"),
         "ok": (by.get("未连接") or {}).get("seen") is True},
    ] if params.get("no_computer") else []) + ([
        # 11-20 线上 H3：认不出的物料行，物料题只剩「其他」打字，提交 417。物料题要有「新建」可点（推荐、排第一）
        {"case": "the item question of an unrecognized line offers New item as its first option",
         "seen": answered.get("asked"),
         "ok": any(len(a.get("options") or []) >= 1 and "新建" in (a.get("options") or [""])[0]
                   for a in answered.get("asked") or [] if isinstance(a, dict))},
    ] if params.get("new_item") else [])


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP LINE Intake", {params['intake']!r}, ["status", "purchase", "sales_document", "line_choices", "is_paid",
                                                                    "payment_method"], as_dict=True) or {{}}
made = row.get("purchase") if {params['direction']!r} == "purchase" else row.get("sales_document")
doctype = {DOCTYPES[params['direction']]!r}
doc = frappe.db.get_value(doctype, made, ["status", "ledger_state", "ledger_job"], as_dict=True) if made else None
chosen = json.loads(row.get("line_choices") or "null") or {{}}
task = frappe.get_doc("Pearnly Express Operator Task", {params['task']!r})
# 照电脑写好的样子回执（正式回执路径 `push.job_changed` → 代录回执 → 后台按任务主人收尾）：代录任务里这张票（I:）跟着变「已入账」、
# 带上 Express 单号（11-17 验收：回执只带 P:，任务按 I: 找不到，一直「排队中」没单号）
receipt = {{}}
if doc and doc.get("ledger_job") and not {bool(params.get('no_computer'))!r}:
    import threading, time
    from pearnly_core.express_operator import feed, tools
    from pearnly_erp_portal.express import push
    key = frappe.db.get_value("Pearnly CC Job", doc["ledger_job"], "request_key")
    # 电脑领单、写成回执的同时 CC 在对话里查进度（11-17 验收：领单那一刻 express_operator_status 出错）：另一条线程以任务主人身份连查
    site, sites, asked = frappe.local.site, frappe.local.sites_path, {{"calls": 0, "errors": []}}
    def ask():
        frappe.init(site=site, sites_path=sites)
        frappe.connect()
        frappe.set_user({USER!r})
        try:
            for _ in range(8):
                try:
                    json.loads(tools.status({{"task": {params['task']!r}}}))
                    frappe.db.commit()
                except Exception as error:
                    frappe.db.rollback()
                    asked["errors"].append(repr(error)[:300])
                asked["calls"] += 1
                time.sleep(0.2)
        finally:
            frappe.destroy()
    # 领单那一刻的确定性复现：CC 先读了任务（有了读快照）、慢 1.5 秒才收尾，这期间电脑页面报「开始录了」（`tasks.mark_running`）并提交
    from pearnly_core.express_operator import tasks
    slow, real_read = threading.local(), tasks.read
    def read_slowly(name):
        out = real_read(name)
        if getattr(slow, "on", False):
            slow.on = False
            time.sleep(1.5)
        return out
    tasks.read = read_slowly
    def ask_slowly():
        slow.on = True
        frappe.init(site=site, sites_path=sites)
        frappe.connect()
        frappe.set_user({USER!r})
        try:
            json.loads(tools.status({{"task": {params['task']!r}}}))
            frappe.db.commit()
        except Exception as error:
            frappe.db.rollback()
            asked["errors"].append("taken: " + repr(error)[:300])
        asked["calls"] += 1
        frappe.destroy()
    first = threading.Thread(target=ask_slowly)
    first.start()
    time.sleep(0.4)
    frappe.set_user({USER!r})
    tasks.mark_running({params['task']!r})
    frappe.db.commit()
    frappe.set_user("Administrator")
    first.join(60)
    probe = threading.Thread(target=ask)
    probe.start()
    for status, docnum in (("leased", ""), ("success", "FLOW-{params['tag'][:6]}")):
        frappe.db.set_value("Pearnly CC Job", doc["ledger_job"], "status", status, update_modified=False)
        push.job_changed({{"source_doctype": doctype, "source_name": made, "status": status, "job": doc["ledger_job"],
                          "docnum": docnum, "request_key": key}})
        frappe.db.commit()
        time.sleep(0.6)
    probe.join(60)
    for _ in range(60):
        frappe.db.rollback()
        # 只看这张票那一行：写入计划按这家这个月的整份待录清单定，同站点别的流程留下的单也可能在任务里
        rows = frappe.get_all("Pearnly Express Operator Task Document", filters={{"parent": {params['task']!r},
                              "doc_id": {params['doc_id']!r}}}, fields=["doc_id", "state", "docnum"])
        if rows and all(r.state == "saved" for r in rows):
            break
        time.sleep(1)
    # 写成后对话里的收尾那句（服务端按任务记录定，主会话 11-19 方案 A）：带这张的 Express 单号，只有一条
    ends = [e for e in feed.project(frappe.get_doc("Pearnly Express Operator Task", {params['task']!r}), None, feed._tz())
            if e["type"] == "agent.message"]
    receipt = {{"rows": [dict(r) for r in rows], "status": frappe.db.get_value("Pearnly Express Operator Task", {params['task']!r}, "status"),
               "asked": asked, "ends": [((e.get("content") or [{{}}])[0].get("text") or "") for e in ends]}}
print(json.dumps({{"intake": row.get("status"), "made": made or "", "doc": doc or {{}},
                  "decided": all(line.get("decided") for line in chosen.get("lines") or [] if line),
                  "new_lines": [bool(line.get("new")) for line in chosen.get("lines") or [] if line],
                  "payment_decided": bool(chosen.get("payment_decided")), "warehouse": chosen.get("warehouse") or "",
                  "task_docs": [r.doc_id for r in task.get("documents") or []], "receipt": receipt}}, default=str))
""")
    doc = got.get("doc") or {}
    return [
        {"case": "the card's answers went back onto the intake record (payment, the line's product, the warehouse)",
         "saved": {k: got.get(k) for k in ("decided", "payment_decided", "warehouse")},
         "ok": got.get("decided") is True and got.get("payment_decided") is True and bool(got.get("warehouse"))},
        {"case": "the intake record was submitted as an ERP document handed to the Express write queue",
         "saved": {"intake": got.get("intake"), "made": got.get("made"), "doc": doc},
         "ok": bool(got.get("made")) and doc.get("status") == "submitted" and bool(doc.get("ledger_state"))
         and (params.get("no_computer") or (doc.get("ledger_state") == "queued" and bool(doc.get("ledger_job"))))},
        {"case": "the operator task carries the ticket", "saved": got.get("task_docs"),
         "ok": params["doc_id"] in (got.get("task_docs") or [])},
    ] + ([
        {"case": "choosing New item on the card is saved on the line (no 417)", "saved": got.get("new_lines"),
         "ok": got.get("new_lines") == [True]},
    ] if params.get("new_item") else []) + ([] if params.get("no_computer") else [
        {"case": "after the computer's receipt the ticket's task row is entered with the Express number",
         "saved": got.get("receipt"),
         "ok": [(r.get("doc_id"), r.get("state"), r.get("docnum")) for r in (got.get("receipt") or {}).get("rows") or []]
         == [(params["doc_id"], "saved", "FLOW-" + params["tag"][:6])]},
        {"case": "asking for progress while the computer takes and writes the document does not fail",
         "saved": (got.get("receipt") or {}).get("asked"),
         "ok": ((got.get("receipt") or {}).get("asked") or {}).get("calls", 0) > 0
         and not ((got.get("receipt") or {}).get("asked") or {}).get("errors")},
        {"case": "once written the conversation ends with one sentence naming the Express number",
         "saved": (got.get("receipt") or {}).get("ends"),
         "ok": (got.get("receipt") or {}).get("ends") == ["已录入：FLOW-" + params["tag"][:6]]},
    ])
