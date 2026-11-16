"""流程用例：ERP CC 代录里放进来的票走完「开始前卡片 → 记下 → 写入计划 → 开始 → 提交成 ERP 单、交进写账」（H3 / H4，11-16 真机：
卡片作答 403、写入计划判「未找到」、CC 说没有上传功能）。

放进来的票识别后是收单记录 ``I:<记录>``（`express.operator`），开录时才用网页同一个入账函数提交成采购 / 销售单。实验栈没有识别，
「放票 → 交识别」那一段由 `cc.operator.upload-on-send` 覆盖；这里按识别完成的收单记录起步（同 `erp_line_card_lines` 造记录的做法：
本人从代录对话放进来的、这家公司的、识别结果已在），其余每一步都在页面上点：

1. 打开代录对话：开始前卡片列出这张票要人定的几项（付款与 Express 那几项；行物料、仓库照 H3 真机那张票已定）。
2. 在卡片上逐题作答、发送：记下成功（修前这里报「没有权限」）。答案写回收单记录。
3. 说一句让 CC 建写入计划（假智能体照真实引擎调 ``pearnly_write_plan``）：这张票在要写的里（修前判 ``not_found``）。
4. 再说一句开始（``pearnly_write_start``）：收单记录提交成 ERP 单据，交进写账（实验栈没有执行电脑，交单后照常排着等电脑）。

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


def prepare(lab, manifest, direction=None):
    direction = direction or DIRECTION
    co, tag = company(manifest), "opi" + uuid.uuid4().hex[:6]
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
if {direction!r} == "purchase":
    result = {{"doc_no": "{tag.upper()}-1", "doc_date": day, "doc_kind": "purchase_invoice", "has_vat": 1,
              "supplier_name": "flow intake supplier {tag}", "lines": lines}}
else:
    result = {{"doc_no": "{tag.upper()}-1", "doc_date": day, "doc_kind": "receipt", "has_vat": 1,
              "buyer_name": "flow intake buyer {tag}", "party_type": "company", "lines": lines}}
doc = frappe.get_doc({{"doctype": intake.DOCTYPE, "status": intake.READY, "direction": {direction!r}, "owner": frappe.session.user,
                       "sent_by": frappe.session.user, "source": intake.SOURCE_CC, "session_id": made["id"],
                       "request_key": "flow-{tag}", "language": "zh", "media_type": "image", "company": {co!r},
                       "party": "@doc", "is_paid": 1, "payment_method": "cash", "source_file": file.file_url,
                       "source_file_name": file.name, "file_hash": hashlib.sha256(pixel).hexdigest(),
                       "received_at": frappe.utils.now(), "result": json.dumps(result, ensure_ascii=False)}}).insert(ignore_permissions=True)
value = line_items.prepare(doc, result)
# 同 H3 真机那张票：行已认出物料（这里认成 Express 同步来的那件）、仓库按这家第一个启用的仓定好，卡片问付款与 Express 那几项；
# 行没认出、仓库没定时那几问由单测守住
product = frappe.db.get_value("ERP Product", {{"express_code": ["is", "set"], "is_active": 1}}, "name")
value["lines"] = [{{**line, "item_type": "goods", "product": product, "new": False, "decided": True}} for line in value["lines"]]
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
                     "facts": {{"accounts": [{{"code": code, "name": code, "type": code[0]}} for code in (*mapping.values(), "11-01-01-00")],
                               "mapping": mapping, "catalog": {{"isacc_candidates": []}}}}}})
frappe.db.commit()
print(json.dumps({{"session": made["id"], "task": made["task"], "intake": doc.name, "period": period, "book": key, "seeded": seeded}}))
""")
    return {**out, "company": co, "tag": tag, "direction": direction, "doc_id": "I:" + out["intake"], "cases": []}


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
frappe.set_user({USER!r})
if frappe.db.exists("Pearnly Assistant Session", {{"session_id": {params['session']!r}}}):
    sessions.delete({params['session']!r})
if {bool(params.get('seeded'))!r}:
    from pearnly_erp_portal.express import facts
    path = facts._path({params.get('book', '')!r})
    if os.path.exists(path):
        os.remove(path)
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
         "await until(()=>q('textarea.aui-composer-input'),120); "
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
         " await sleep(300); asked.push(qn); const n=c.querySelector('[data-slot=\"cc-askq-next\"]'); if(!n||n.disabled) break; n.click(); "
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
         "handed: ((r.handed||{}).results||[]).map(x=>({doc_id:x.doc_id, status:x.status, reason:String(x.reason||'').slice(0,120)}))};"},
    ]


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
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP LINE Intake", {params['intake']!r}, ["status", "purchase", "sales_document", "line_choices", "is_paid",
                                                                    "payment_method"], as_dict=True) or {{}}
made = row.get("purchase") if {params['direction']!r} == "purchase" else row.get("sales_document")
doctype = {DOCTYPES[params['direction']]!r}
doc = frappe.db.get_value(doctype, made, ["status", "ledger_state", "ledger_job"], as_dict=True) if made else None
chosen = json.loads(row.get("line_choices") or "null") or {{}}
task = frappe.get_doc("Pearnly Express Operator Task", {params['task']!r})
print(json.dumps({{"intake": row.get("status"), "made": made or "", "doc": doc or {{}},
                  "decided": all(line.get("decided") for line in chosen.get("lines") or [] if line),
                  "payment_decided": bool(chosen.get("payment_decided")), "warehouse": chosen.get("warehouse") or "",
                  "task_docs": [r.doc_id for r in task.get("documents") or []]}}, default=str))
""")
    doc = got.get("doc") or {}
    return [
        {"case": "the card's answers went back onto the intake record (payment, the line's product, the warehouse)",
         "saved": {k: got.get(k) for k in ("decided", "payment_decided", "warehouse")},
         "ok": got.get("decided") is True and got.get("payment_decided") is True and bool(got.get("warehouse"))},
        {"case": "the intake record was submitted as an ERP document handed to the Express write queue",
         "saved": {"intake": got.get("intake"), "made": got.get("made"), "doc": doc},
         "ok": bool(got.get("made")) and doc.get("status") == "submitted" and bool(doc.get("ledger_state"))},
        {"case": "the operator task carries the ticket", "saved": got.get("task_docs"),
         "ok": params["doc_id"] in (got.get("task_docs") or [])},
    ]
