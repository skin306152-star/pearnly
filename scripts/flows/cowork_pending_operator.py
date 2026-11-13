"""流程用例：Cowork 的 CC 待处理（11-04 起并进对话：「这家公司 · 这个月」对话末尾的「需要处理」框，`?view=pending&company=`
兼容入口打开它）里一张归代录的单（交给代录、电脑在 Express 里没录成停下了），对话末尾的提问卡（用户 11-05：同 CC 的 ask_user，
共用写账前置判定出题）问付款方式，选现购「提交」——选择照复核页记进账套记忆，但不按直写重推（主会话 10-27：由代录去录），
库里不多推送记录、不多领单。

单据怎么到这一步（都是产品自己的函数，`cowork_operator_lib.py`）：代录批次准备（载荷带 ``entry_mode=operator``）→ 交出去 →
实验室合成写入端领单、回执「要人看」（manual）→ 公司页删掉记住的付款方式 → CC 对话里再推这张，推前判出要选付款方式、只记原因
（``choice_needed:payment``）→ 待处理列成「当场选」。

执行到的服务端：`companies.api.pending`、`write_precheck.api.check / answer`（Cowork 的判定与记住钩子）、
`push.application.service.operator_owned`。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS  # noqa: E402
from cowork_operator_lib import BASE, CLEANUP, SUBJECT

TITLE = "Cowork CC 对话里的「需要处理」与右侧栏月结台账：归代录的单在提问卡上选付款方式「提交」只记下选择、不按直写重推（产品账本，不连 Express）"
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
    card = "document.querySelector('[data-slot=\"cc-pending-askq\"]')"
    company, back = json.dumps(params["company"]), json.dumps(f"/assistant?view=pending&company={params['company']}")
    row = f"qa('[data-slot=\"cc-pending-ask\"][data-variant=\"row\"]').find(e=>e.innerText.includes({json.dumps(ref)}))"
    return [
        # 这家这个月的一段对话（在弹窗里选这家、说一句；月份默认当月 = 造的单据月份）：右侧栏「本月」卡就是这个月的月结台账
        # （11-07 公司页删了）：这张列成收着的一行（码名 · 单据 · 金额），点开才判定、出同一张提问卡（不带框）；看完收起（手机上关掉
        # 盖满屏的右侧栏）。再从提醒入口回到这家这个月的对话，末尾的提问卡照旧
        {"name": "开对话", "js": f"setTimeout(()=>location.assign('/assistant'),0); return true;", "wait_ms": 5000},
        {"name": "这家说一句", "js": JS + f"await until(()=>q('[data-slot=\"cc-chin-company\"]'),120); const picked=await pickCompany({json.dumps(params['title'])}); "
         f"const sent=picked && await say({json.dumps('flow ' + ref + ' 看一下月结')}); "
         "await until(()=>q('[data-slot=\"cc-panel-month\"], .cc-mpill'),160); await sleep(1500); return {picked, sent};"},
        {"name": "月结台账点开", "js": WAIT + "const month=()=>document.querySelector('[data-slot=\"cc-panel-month\"]'); "
         "await until(()=>document.querySelector('[data-slot=\"cc-chin-company\"], .cc-mpill'),120); await new Promise(r=>setTimeout(r,800)); "
         # 电脑：右侧栏关着就点顶栏的开关；手机：右上「…」→ 公司名，右侧栏盖满屏
         "if(!month()){const t=document.querySelector('[data-slot=\"cc-panel-toggle\"][aria-pressed=\"false\"]'); "
         "if(t) t.click(); else {const m=qa('.cc-mpill button')[1]; m?.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'})); await until(()=>document.querySelector('.cc-menu-item'),20); document.querySelector('.cc-menu-item')?.click();}} "
         "await until(month,120); "
         # 卡片记得收没收（按人记）：收着就先点开
         "const fold=document.querySelector('[data-slot=\"cc-panel-month\"] .cc-panel-card-toggle[aria-expanded=\"false\"]'); fold?.click(); "
         f"const seen=await until(()=>{row},200); const r={row}; "
         "if(!r) return {seen, url: location.pathname+location.search, month: !!month(), "
         "open: document.querySelector('[data-slot=\"cc-panel-month\"] .cc-panel-card-toggle')?.getAttribute('aria-expanded')||'', "
         "asks: document.querySelectorAll('[data-slot=\"cc-pending-ask\"]').length, items: document.querySelectorAll('[data-slot=\"cc-month-item\"]').length, "
         "text: (month()?.innerText||'').slice(0,300)}; "
         "const closed=r.querySelector('.cc-pending-toggle')?.getAttribute('aria-expanded'); r.querySelector('.cc-pending-toggle').click(); "
         f"const opened=await until(()=>({row})?.querySelector('[data-slot=\"cc-pending-askq\"] [data-slot=\"cc-askq-question\"]'),160); const x={row}; "
         "const out={seen, closed, opened, question: x?.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
         "framed: !!x?.querySelector('.cc-steps-card'), status: !!x?.querySelector('[data-slot=\"cc-askq-status\"]')}; "
         "x?.querySelector('.cc-pending-toggle')?.click(); await new Promise(r=>setTimeout(r,300)); "
         "document.querySelector('.cc-panel[data-mode=\"phone\"] .cc-panel-top .cc-icon-btn')?.click(); await new Promise(r=>setTimeout(r,600)); "
         "return out;"},
        {"name": "回到对话", "js": f"setTimeout(()=>location.assign({back}),0); return true;", "wait_ms": 6000},
        {"name": "待处理出提问卡", "js": WAIT + f"const seen=await until(()=>{card},160); const c={card}; "
         "const chin=(document.querySelector('[data-slot=\"cc-chin-company\"]')?.innerText||document.querySelector('[data-slot=\"cc-mobile-title\"] .cc-mtitle-sub')?.innerText||'').trim(); "
         "const mode=document.querySelector('[data-slot=\"cc-mode-switch\"] [aria-checked=\"true\"]')?.dataset.mode; "
         "const opts=qa('[data-slot=\"cc-pending-askq\"] [data-slot=\"cc-askq-option\"]'); "
         "return {seen, question: c?.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
         "options: opts.map(o=>({text:o.querySelector('.cc-askq-label')?.innerText.trim()||'', checked:o.hasAttribute('data-checked'), badge: !!o.querySelector('[data-slot=\"badge\"]')})), "
         "other: !!c?.querySelector('[data-slot=\"cc-askq-other-option\"]'), "
         "next: (q=>q?{text:q.innerText.trim(), disabled:q.disabled}:null)(c?.querySelector('[data-slot=\"cc-askq-next\"]')), "
         "status: !!document.querySelector('[data-slot=\"cc-askq-status\"]'), "
         f"rowListed: qa('[data-slot=\"cc-pending-in-chat\"]').some(e=>e.innerText.includes({json.dumps(ref)})), "
         "chin, mode, nav: !!document.querySelector('[data-slot=\"cc-nav-pending\"]')};"},
        {"name": "选现购", "js": WAIT + f"const c={card}; if(!c) return {{error:'card missing'}}; "
         "const o=qa('[data-slot=\"cc-pending-askq\"] [data-slot=\"cc-askq-option\"]').find(e=>(e.querySelector('.cc-askq-label')?.innerText.trim()||'')==='现购'); "
         "if(!o) return {error:'no cash option'}; o.click(); await new Promise(r=>setTimeout(r,300)); "
         "const n=c.querySelector('[data-slot=\"cc-askq-next\"]'); "
         "return {checked: o.hasAttribute('data-checked'), next: n?.innerText.trim()||'', enabled: !!n && !n.disabled};"},
        {"name": "提交", "js": WAIT + f"const n={card}?.querySelector('[data-slot=\"cc-askq-next\"]'); if(!n) return {{error:'no submit'}}; n.click(); "
         f"const done=await until(()=>{card}?.querySelector('[data-slot=\"cc-ask-answer\"]'),160); await new Promise(r=>setTimeout(r,1500)); "
         f"const c={card}; return {{done, answer: c?.querySelector('[data-slot=\"cc-ask-answer\"]')?.innerText.trim()||'', "
         "alert: c?.querySelector('[role=\"alert\"]')?.innerText.trim()||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    listed, picked, answered, ledger = (by.get(k) or {} for k in ("待处理出提问卡", "选现购", "提交", "月结台账点开"))
    labels = [o.get("text") or "" for o in listed.get("options") or []]
    return [
        {"case": "右侧栏「本月」月结台账：这张是收着的一行，点开才出同一张提问卡（嵌在台账里、不带框与「需要您的输入」），问付款方式", "seen": ledger,
         "ok": bool(ledger.get("seen")) and ledger.get("closed") == "false" and bool(ledger.get("opened"))
         and "付款方式" in (ledger.get("question") or "") and not ledger.get("framed") and not ledger.get("status")},
        {"case": "提醒入口打开这家这个月的对话（对话模式、下巴或手机标题是这家），末尾单独一张提问卡（带「需要您的输入」），这张单不再列成一行；侧栏没有「待处理」",
         "seen": {**listed, "want": params["title"]},
         "ok": bool(listed.get("seen")) and bool(listed.get("status")) and not listed.get("rowListed")
         and listed.get("mode") == "chat" and params["title"] in (listed.get("chin") or "") and not listed.get("nav")},
        {"case": "只问付款方式：记住的删掉了，按票种预选赊购（共用判定的预选规则，排第一带「推荐」徽章、默认选中），现购、「其他」在后；一题时主按钮是「提交」",
         "seen": listed,
         "ok": "付款方式" in (listed.get("question") or "") and "Flow seller" in (listed.get("question") or "")
         and labels == ["赊购", "现购"] and [o.get("checked") for o in listed.get("options") or []] == [True, False]
         and [o.get("badge") for o in listed.get("options") or []] == [True, False]  # 推荐改独立徽章（用户 11-12 批准，11-13 落地）
         and bool(listed.get("other")) and (listed.get("next") or {}).get("text", "").startswith("提交")},
        {"case": "改选现购（单选换成它），能提交", "seen": picked, "ok": bool(picked.get("checked")) and bool(picked.get("enabled"))},
        {"case": "提交后卡片收成一行所选（现购），没有报错", "seen": answered,
         "ok": bool(answered.get("done")) and "现购" in (answered.get("answer") or "") and not answered.get("alert")},
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
