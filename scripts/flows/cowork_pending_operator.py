"""流程用例：Cowork 的 CC 待处理（11-04 起并进对话：「这家公司 · 这个月」对话末尾的「需要处理」框，`?view=pending&company=`
兼容入口打开它）里一张归代录的单（交给代录、电脑在 Express 里没录成停下了），当场选付款方式「确认」——
选择照复核页记进账套记忆，但不按直写重推（主会话 10-27：由代录去录），页面显示「单据在代录中」，库里不多推送记录、不多领单。

单据怎么到这一步（都是产品自己的函数，`cowork_operator_lib.py`）：代录批次准备（载荷带 ``entry_mode=operator``）→ 交出去 →
实验室合成写入端领单、回执「要人看」（manual）→ 公司页删掉记住的付款方式 → CC 对话里再推这张，推前判出要选付款方式、只记原因
（``choice_needed:payment``）→ 待处理列成「当场选」。

执行到的服务端：`companies.api.pending`、`push.application.api.choices`、`assistant.pending_api.answer`（`operator_owned` 分支）、
`push.application.service.save_choices / operator_owned`。
"""
from __future__ import annotations

import json
import uuid

from cowork_operator_lib import BASE, CLEANUP, SUBJECT

TITLE = "Cowork CC 对话里的「需要处理」：归代录的单当场选「确认」只记下选择、不按直写重推，页面显示单据在代录中（产品账本，不连 Express）"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-slot=\"cc-mode-switch\"]')"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const qa=s=>[...document.querySelectorAll(s)];")


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, 'frappe.db.set_value("User", "admin@cowork.regression", "language", "zh")\nfrappe.db.commit()\nprint("{}")')
    ref = "FLOW-OP-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, CLEANUP + BASE + f"""
from pearnly_cowork.assistant import tools
from pearnly_cowork.push import choices as choice_store
name = recognized({ref!r})
record = prepared(name, operator={{"id": "flow-" + uuid4().hex[:8], "index": 1, "total": 1, "start_at": 0, "doc": name, "company": company["id"]}})
claim = native.lease(record, context=context_of(record), adapter=writer, now=datetime.now(timezone.utc))
frappe.db.commit()
# 代录在 Express 里停下（对方档拿不准），回执「要人看」：推送记录 manual，还归代录
native.ack(record, context=context_of(record), adapter=writer, fence=claim["fence"], attempt_token=claim["attempt_token"],
           success=False, outcome="needs_review", error="[ERR_SUPPLIER_AMBIGUOUS] supplier ambiguous")
frappe.db.commit()
# 公司页删掉记住的付款方式（同一个底层函数），CC 对话里再推这张：推前判出要选付款方式、只记原因，不建推送记录
choice_store.forget(conn["name"], ["flowop"], "payment", [{SUBJECT!r}])
frappe.db.commit()
pushed = tools._push_one(name)
frappe.db.commit()
doc = frappe.db.get_value("Cowork OCR History", name, ["last_push_status", "last_push_reason", "last_push_record"], as_dict=True)
frappe.local.lang = "zh"
print(json.dumps({{"company": company["id"], "title": company["title"], "period": period, "doc": name, "record": record, "pushed": pushed, "doc_state": dict(doc),
                  "owned": application.operator_owned(storage.read(name)),
                  "records": frappe.db.count("Cowork Push Record", {{"history": name}}),
                  "attempts": frappe.db.count("Cowork Push Attempt", {{"push_record": record}}),
                  "message": frappe._("push.operator_owned")}}, default=str))
""")
    if not out["owned"] or out["pushed"].get("status") != "needs_choice" or out["doc_state"].get("last_push_record") != out["record"]:
        raise RuntimeError(f"setup did not reach an operator document waiting for a choice: {out}")
    return {**out, "ref": ref, "cases": []}


def steps(params, camera_code=None):
    ref = params["ref"]
    row = f"qa('[data-slot=\"cc-pending-in-chat\"] [data-slot=\"cc-pending-choose\"]').find(e=>e.innerText.includes({json.dumps(ref)}))"
    return [
        {"name": "待处理列出这张", "js": WAIT + f"const seen=await until(()=>{row},160); const r={row}; "
         "const chin=document.querySelector('[data-slot=\"cc-chin-company\"]')?.innerText.trim()||''; "
         "const mode=document.querySelector('[data-slot=\"cc-mode-switch\"] [aria-checked=\"true\"]')?.dataset.mode; "
         "return {seen, text: r?.innerText.trim()||'', choose: qa('[data-slot=\"cc-pending-in-chat\"] [data-slot=\"cc-pending-choose\"]').length, "
         "chin, mode, period: document.querySelector('[data-slot=\"cc-period-chip\"]')?.innerText.trim()||'', "
         "nav: !!document.querySelector('[data-slot=\"cc-nav-pending\"]')};"},
        {"name": "展开选付款方式", "js": WAIT + f"const r={row}; if(!r) return {{error:'row missing'}}; "
         "r.querySelector('.cc-pending-toggle').click(); "
         f"const ok=await until(()=>{row}?.querySelector('select'),120); const s={row}?.querySelector('select'); "
         "if(!s) return {ok, error:'no select'}; const options=[...s.options].map(o=>({value:o.value, text:o.innerText.trim(), disabled:o.disabled})); "
         "const before=s.value; Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(s,'cash'); "
         "s.dispatchEvent(new Event('change',{bubbles:true})); await new Promise(r=>setTimeout(r,400)); "
         f"const btn=[...({row}).querySelectorAll('button')].find(b=>b.innerText.trim()==='确认'); "
         "return {ok, options, before, after: s.value, label: s.closest('label')?.innerText.trim().split('\\n')[0]||'', enabled: !!btn && !btn.disabled};"},
        {"name": "确认", "js": WAIT + f"const btn=[...({row}).querySelectorAll('button')].find(b=>b.innerText.trim()==='确认'); "
         "if(!btn) return {error:'no confirm button'}; btn.click(); "
         f"const shown=await until(()=>({row})?.querySelector('[role=\"alert\"]'),160); await new Promise(r=>setTimeout(r,1500)); "
         f"const r={row}; return {{shown, alert: r?.querySelector('[role=\"alert\"]')?.innerText.trim()||'', still: !!r}};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    listed, opened, answered = (by.get(k) or {} for k in ("待处理列出这张", "展开选付款方式", "确认"))
    values = [o.get("value") for o in opened.get("options") or [] if not o.get("disabled")]
    return [
        {"case": "提醒入口打开这家这个月的对话（对话模式、下巴是这家），末尾「需要处理」框把这张列成能当场选的一行（单号、对方）；侧栏没有「待处理」",
         "seen": {**listed, "want": params["title"]},
         "ok": bool(listed.get("seen")) and params["ref"] in (listed.get("text") or "") and "Flow seller" in (listed.get("text") or "")
         and listed.get("mode") == "chat" and params["title"] in (listed.get("chin") or "") and not listed.get("nav")},
        {"case": "展开只问付款方式：现金 / 赊账两项、不预选，选了才能确认", "seen": opened,
         "ok": bool(opened.get("ok")) and sorted(values) == ["cash", "credit"] and opened.get("before") == ""
         and opened.get("after") == "cash" and bool(opened.get("enabled"))},
        {"case": "确认后页面写「单据在代录中」（服务端那一句，不当推送成功、不显示原因码）", "seen": answered,
         "ok": bool(answered.get("shown")) and answered.get("alert") == params["message"]},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], CLEANUP + f"""
frappe.set_user("admin@cowork.regression")
doc = frappe.db.get_value("Cowork OCR History", {params['doc']!r}, ["last_push_status", "last_push_reason", "last_push_record", "push_connection"], as_dict=True)
chosen = frappe.get_all("Cowork Express Choice", filters={{"connection": doc.push_connection, "kind": "payment", "subject": {SUBJECT!r}}},
                        fields=["value", "account_set"], limit_page_length=5)
record = frappe.db.get_value("Cowork Push Record", {params['record']!r}, ["status", "reason"], as_dict=True)
out = {{"doc": dict(doc), "chosen": [dict(r) for r in chosen], "record": dict(record),
       "records": frappe.db.count("Cowork Push Record", {{"history": {params['doc']!r}}}),
       "attempts": frappe.db.count("Cowork Push Attempt", {{"push_record": {params['record']!r}}})}}
out["gone"] = cleanup()
out["left"] = leftovers()
print(json.dumps(out, default=str))
""")
    doc = rows["doc"]
    return [
        {"case": "选的付款方式记进这份账套的记忆（现金）", "saved": rows["chosen"],
         "ok": rows["chosen"] == [{"value": "cash", "account_set": "flowop"}]},
        {"case": "没有按直写重推：推送记录还是原来那一条（代录的、停在要人看），没有多领单", "saved": {k: rows[k] for k in ("record", "records", "attempts")},
         "ok": rows["records"] == params["records"] == 1 and rows["attempts"] == params["attempts"]
         and rows["record"].get("status") == "manual"},
        {"case": "单据仍挂在代录那条推送记录上", "saved": doc,
         "ok": doc.get("last_push_record") == params["record"] and doc.get("last_push_status") == "manual"},
        {"case": "造的连接、公司、单据、锁都清掉", "saved": {"gone": rows["gone"], "left": rows["left"]},
         "ok": rows["gone"]["connections"] == 1 and not any(rows["left"].values())},
    ]
