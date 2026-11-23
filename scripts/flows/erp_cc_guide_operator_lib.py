"""ERP 版 CC（智能体入口）代录对话里的采购 / 销售引导（documents 源）真页面流程的共用片段：`erp_cc_guide_operator_purchase.py`、
`erp_cc_guide_operator_sales.py` 各调一次，只差业务。

数据照 `erp_cc_operator_intake.prepare` 同一套造法（本人从代录对话放进来、识别完成的收单记录 ``I:<记录>``，行已认出物料、仓库已定，
账套最小主档，本人一台登录过 CC 的电脑）。ERP 的采购 / 销售引导声明只开在代录（`express.cc_guides.GUIDES` ``modes: ["operator"]``）。
实验栈没有识别，「交文件 → 识别」那一段不走：用 documents 源的公开入口 ``run.start(doc_ids=[I:…], at="asking")`` 开这次引导
（同 `erp_cc_guide_documents_edit`）。

之后全在页面上点卡（不调接口、不经模型）：打开代录对话 → 题卡逐题照推荐或第一项点、点「下一步 / 提交」→ 复核卡（这张票一行）→
点确定那一下（``cc-guide-confirm``）→ 结果卡（交进代录任务，实验栈没有电脑来领）→ 删会话（cleanup 作废、撤件、删任务）。
库里：引导运行 done、同意是页面上这一下；这段会话只有一份写入计划、同意相同；这张票交进代录任务一次。
"""
from __future__ import annotations

import json

import erp_cc_operator_intake as intake_flow
from cc_lib import JS

USER = intake_flow.USER
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"
WORD = {"purchase": "采购", "sales": "销售"}


def prepare(lab, manifest, business):
    params = intake_flow.prepare(lab, manifest, direction=business)
    try:
        started = lab.python(manifest["site"], f"""
from pearnly_core.assistant.guide import run
frappe.set_user({USER!r})
try:
    found = run.start({params['session']!r}, {business!r}, doc_ids=[{params['doc_id']!r}], at="asking")
    frappe.db.commit()
    print(json.dumps({{"run": found["run"], "status": found["status"], "stage": found.get("stage") or ""}}, default=str))
except Exception as error:
    frappe.db.rollback()
    print(json.dumps({{"run": "", "error": repr(error)[:300]}}))
""")
    except Exception:
        intake_flow.cleanup(lab, manifest, params)
        raise
    return {**params, "business": business, "run": started.get("run") or "", "started": started}


HELP = "const card=()=>" + CARD + "; "


def steps(params, camera_code=None):
    return [
        {"name": "打开代录对话", "wait_ms": 6000, "js": JS +
         f"setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent({json.dumps(params['session'])})),0); return true;"},
        {"name": "题卡", "js": JS + HELP +
         "await until(()=>card()&&(card().querySelector('[data-slot=\"cc-askq-option\"]')||card().querySelector('[data-slot=\"cc-guide-review\"]')),200); "
         "await sleep(600); const head={business: card()?.dataset.business||'', status: card()?.dataset.status||'', stage: card()?.dataset.stage||'', "
         "operator: !!card()?.querySelector('[data-slot=\"cc-guide-operator\"]')}; "
         "const asked=[]; for(let k=0;k<12;k++){const c=card(); const opts=[...(c?.querySelectorAll('[data-slot=\"cc-askq-option\"]')||[])]; "
         " if(!opts.length) break; const title=c.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||''; "
         " const hit=opts.find(o=>o.querySelector('[data-slot=\"badge\"]'))||opts[0]; asked.push({title, picked: hit.innerText.trim().split('\\n')[0]}); "
         " if(!hit.hasAttribute('data-checked')) hit.click(); await sleep(250); "
         " const next=c.querySelector('[data-slot=\"cc-askq-next\"]'); if(!next||next.disabled) return {...head, asked, stuck: title}; "
         " const before=c.innerText; next.click(); await until(()=>!card()||card().innerText!==before,80); await sleep(500);} "
         "return {...head, asked};"},
        {"name": "复核", "shot": True, "file": "1-review", "js": JS + HELP +
         "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-review\"][data-source=\"documents\"]'),200); await sleep(500); "
         "const r=card()?.querySelector('[data-slot=\"cc-guide-review\"]'); const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); "
         "return {review: !!r, docs: [...(r?.querySelectorAll('[data-slot=\"cc-guide-doc\"]')||[])].map(d=>({doc: d.dataset.doc, blocking: !!d.dataset.blocking})), "
         "text: r?.innerText||'', confirm: b?b.innerText.trim():'', enabled: !!b&&!b.disabled};"},
        {"name": "确定", "shot": True, "file": "2-result", "js": JS + HELP +
         "const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); b?.click(); "
         "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-result\"]'),200); await sleep(800); "
         "const res=card()?.querySelector('[data-slot=\"cc-guide-result\"]'); "
         "return {clicked: !!b, status: card()?.dataset.status||'', result: res?.dataset.result||'', text: res?.innerText||'', "
         "rows: [...(res?.querySelectorAll('[data-doc]')||[])].map(x=>({doc: x.dataset.doc, status: x.dataset.status||''}))};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    asked, review, done = (by.get(k) or {} for k in ("题卡", "复核", "确定"))
    business, doc = params["business"], params["doc_id"]
    rows = done.get("rows") or []
    return [
        {"case": f"代录对话里开出来的是{WORD[business]}引导卡（服务端出的，不经模型），至少一题、逐题点完没有卡住",
         "seen": {"started": params.get("started"), **asked},
         "ok": asked.get("business") == business and not asked.get("stuck") and bool(asked.get("asked"))},
        {"case": "复核卡：只有这一张票、不挡写入，确定那一下能点", "seen": review,
         "ok": bool(review.get("review")) and [d.get("doc") for d in review.get("docs") or []] == [doc]
         and not any(d.get("blocking") for d in review.get("docs") or []) and bool(review.get("enabled"))},
        {"case": "点确定：结果卡列这一张、已交进代录（实验栈没有电脑来领，不说已录入）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [doc]
         and all(r.get("status") not in ("failed", "withdrawn", "") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": {params['session']!r}}},
                       fields=["name", "consent", "status", "mode"], order_by="creation asc", limit_page_length=10)
guide = frappe.db.get_value("Pearnly Assistant Guide Run", {params['run']!r}, ["status", "consent", "business"], as_dict=True) or {{}}
task = frappe.get_doc("Pearnly Express Operator Task", {params['task']!r})
print(json.dumps({{"plans": plans, "guide": guide, "task_docs": [r.doc_id for r in task.get("documents") or []]}}, default=str))
""")
    plans, guide = got.get("plans") or [], got.get("guide") or {}
    consents = [str(plan.get("consent") or "") for plan in plans]
    return [
        {"case": "引导运行办完（done），同意是页面上这一下（guide:<运行>:<版本>）", "seen": guide,
         "ok": guide.get("status") == "done" and guide.get("business") == params["business"]
         and str(guide.get("consent") or "").startswith(f"guide:{params['run']}:")},
        {"case": "这段会话只有一份写入计划，同意与引导相同", "seen": consents,
         "ok": len(plans) == 1 and consents[0] == str(guide.get("consent") or "")},
        {"case": "这张票交进代录任务一次", "seen": got.get("task_docs"),
         "ok": (got.get("task_docs") or []).count(params["doc_id"]) == 1},
    ]


def cleanup(lab, manifest, params):
    intake_flow.cleanup(lab, manifest, params)
