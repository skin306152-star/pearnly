"""流程用例：ERP 版 CC **直写**对话里的「待处理」引导（form 源，`express.cc_guide_pending`，4509b017c 11-23 带进来）真页面：
点输入框上方业务菜单「待处理」→ 卡上选这家公司待处理清单里停下的那张销售单 → 提交 → 复核 → 确定（交给销售引导）→ 销售引导卡接着走
（题卡逐题照推荐或第一项点 → 复核卡，ERP 单据的票面只读 → 确定）→ 结果卡。

备数：照 `erp_cc_operator_intake.prepare` 同一套造法开一段直写会话、一条识别完成的销售收单记录，在站点里用网页同一个入账函数
（``intake.submit_chat``）提交成销售单；提交那一下不排写账（备数进程里把 ``push.queue`` 换成空的，免得后台写账任务和备数抢状态），
再把这张单标成「停下、要人选」（``ledger_state=held``、``choice_needed``，侧栏「待处理」同一口径 `express.pending.needs_person`）。
之后全在页面上点（不调接口、不经模型）。库里：待处理引导与销售引导两次运行都 done、同意都是页面上那一下；这张单重新排上写账。
"""
from __future__ import annotations

import json

import erp_cc_guide_operator_lib as operator_lib
import erp_cc_operator_intake as intake_flow
from cc_lib import JS

TITLE = "ERP 版 CC 直写待处理引导：菜单点「待处理」→ 选停下的销售单 → 确定交给销售引导 → 题卡 → 复核（票面只读）→ 确定 → 结果；两次运行 done、同意来自页面、单子重新排上写账"
USER = intake_flow.USER
CARD = operator_lib.CARD
HELP = "const card=()=>" + CARD + "; "


def prepare(lab, manifest):
    params = intake_flow.prepare(lab, manifest, direction="sales", mode="chat")
    try:
        made = lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import contracts, push
from pearnly_erp_portal.integrations import intake
frappe.set_user({USER!r})
queue, push.queue = push.queue, lambda doctype, name: None  # 备数提交不排写账（只在这个进程里）
try:
    created = intake.submit_chat({params['intake']!r})
finally:
    push.queue = queue
frappe.db.set_value("ERP Sales Document", created["name"], {{"ledger_state": contracts.HELD, "ledger_reason": contracts.CHOICE,
                                                             "ledger_target": contracts.LEDGER_EXPRESS}}, update_modified=False)
# 点菜单用一段干净的直写对话（同一家公司、本月；待处理清单按公司取）：放过票的那段对话上挂着收单记录的提示，不出业务菜单
from pearnly_core.assistant import sessions
clean = sessions.create(project={params['company']!r}, period=frappe.utils.today()[:7], mode="chat")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": clean["id"]}}, "title", {params['tag'] + '-pending'!r})
frappe.db.commit()
print(json.dumps({{"document": created["name"], "clean": clean["id"]}}))
""")
    except Exception:
        intake_flow.cleanup(lab, manifest, params)
        raise
    return {**params, "business": "sales", "document": made["document"], "pending_doc": "S:" + made["document"],
            "clean": made["clean"]}


def steps(params, camera_code=None):
    want = json.dumps(params["pending_doc"])
    sales = operator_lib.steps({**params, "doc_id": params["pending_doc"]}, camera_code)
    return [
        {"name": "打开直写对话", "wait_ms": 6000, "js": JS +
         f"setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent({json.dumps(params['clean'])})),0); return true;"},
        # 菜单只摆前 4 项、其余收进「更多」（11-17 定稿）：直接摆出来的就点，否则开「更多」点里面那一项
        {"name": "点待处理", "js": JS + HELP + "const row=()=>q('[data-slot=\"cc-business-menu\"]'); await until(()=>composerReady(),120); "
         "const shown=await until(()=>row(),120); "
         "if(!shown) return {shown, url: location.href, composer: composerReady(), cards: qa('[data-slot=\"cc-guide-run\"]').length}; "
         "const labels=[...row().querySelectorAll('.cc-business-measure > button:not([data-slot=\"cc-business-more\"])')].map(b=>(b.textContent||'').trim()); "
         "const direct=row().querySelector(':scope > button[data-business=\"pending\"]'); "
         "if(direct) direct.click(); else { press(row().querySelector(':scope [data-slot=\"cc-business-more\"]')); "
         " await until(()=>q('[data-slot=\"cc-business-more-menu\"] [data-business=\"pending\"]'),40); "
         " q('[data-slot=\"cc-business-more-menu\"] [data-business=\"pending\"]')?.click(); } "
         "await until(()=>card()?.dataset.business==='pending'&&card().querySelector('[data-slot=\"cc-guide-option\"]'),120); await sleep(500); "
         "const codes=[...card().querySelectorAll('[data-slot=\"cc-guide-option\"]')].map(o=>o.dataset.code); "
         "return {shown, labels, direct: !!direct, business: card()?.dataset.business||'', status: card()?.dataset.status||'', codes};"},
        {"name": "选单提交", "js": JS + HELP + f"const want={want}; "
         "const opt=card()?.querySelector('[data-slot=\"cc-guide-option\"][data-code=\"'+want+'\"]'); opt?.click(); await sleep(300); "
         "const submit=[...card().querySelectorAll('button')].find(b=>b.innerText.split('\\n')[0].trim()==='提交'); "
         "const enabled=!!submit&&!submit.disabled; const before=card().innerText; submit?.click(); "
         "await until(()=>card()&&card().innerText!==before&&card().querySelector('[data-slot=\"cc-guide-confirm\"]'),80); await sleep(500); "
         "return {picked: !!opt, enabled, status: card()?.dataset.status||'', review: card()?.querySelector('[data-slot=\"cc-guide-review\"]')?.innerText||''};"},
        {"name": "交给销售引导", "js": JS + HELP +
         "const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); const enabled=!!b&&!b.disabled; b?.click(); "
         "await until(()=>card()?.dataset.business==='sales',120); await sleep(800); "
         "return {clicked: !!b, enabled, business: card()?.dataset.business||'', status: card()?.dataset.status||''};"},
        *sales[1:],
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    menu, picked, handed = (by.get(k) or {} for k in ("点待处理", "选单提交", "交给销售引导"))
    review, done = by.get("复核") or {}, by.get("确定") or {}
    doc = params["pending_doc"]
    rows = done.get("rows") or []
    return [
        {"case": "业务菜单有「待处理」，点了对话里出待处理引导卡，清单里有这张停下的销售单", "seen": menu,
         "ok": "待处理" in (menu.get("labels") or []) and menu.get("business") == "pending" and doc in (menu.get("codes") or [])},
        {"case": "选这张、提交，到复核，确定那一下能点", "seen": picked,
         "ok": bool(picked.get("picked")) and bool(picked.get("enabled")) and bool(picked.get("review"))},
        {"case": "确定后交给销售引导（对话里接着出销售引导卡）", "seen": handed,
         "ok": bool(handed.get("clicked")) and handed.get("business") == "sales"},
        {"case": "销售引导复核卡只有这一张、确定那一下能点", "seen": review,
         "ok": bool(review.get("review")) and [d.get("doc") for d in review.get("docs") or []] == [doc] and bool(review.get("enabled"))},
        {"case": "点确定：结果卡列这一张、排上写账（不说已录入、没有没成）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [doc]
         and all(r.get("status") not in ("failed", "withdrawn", "held", "") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['clean']!r}}},
                      fields=["name", "business", "status", "consent"], order_by="creation asc", limit_page_length=10)
doc = frappe.db.get_value("ERP Sales Document", {params['document']!r}, ["ledger_state", "ledger_reason", "source", "status"], as_dict=True)
print(json.dumps({{"runs": runs, "doc": doc}}, default=str))
""")
    runs, doc = got.get("runs") or [], got.get("doc") or {}
    by = {run.get("business"): run for run in runs}
    return [
        {"case": "待处理与销售两次引导运行都办完，同意都是页面上那一下（guide:<各自运行>:）", "seen": runs,
         "ok": all((by.get(b) or {}).get("status") == "done"
                   and str((by.get(b) or {}).get("consent") or "").startswith(f"guide:{(by.get(b) or {}).get('name')}:")
                   for b in ("pending", "sales"))},
        {"case": "这张单不再是「停下、要人选」，重新排上了写账（来源仍是 cc、已提交）", "seen": doc,
         "ok": doc.get("source") == "cc" and doc.get("status") == "submitted"
         and not (doc.get("ledger_state") == "held" and doc.get("ledger_reason") == "choice_needed")},
    ]


def cleanup(lab, manifest, params):
    if params.get("clean"):
        lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
if frappe.db.exists("Pearnly Assistant Session", {{"session_id": {params['clean']!r}}}):
    sessions.delete({params['clean']!r})
frappe.db.commit()
print("{{}}")
""")
    intake_flow.cleanup(lab, manifest, params)
