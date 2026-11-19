"""流程用例：ERP 版 CC 按单据的引导（documents 源，采购）复核卡上就地改一格、带复核指纹确定（引导卡页面接口约定 5、7，
docs/work/2026-11-19-cc-guide-page-endpoints.md）。收款路线碰不到这两个接口，这条单独覆盖。

数据照 `erp_cc_operator_intake.prepare` 同一套造法（本人从代录对话放进来、识别完成的采购收单记录 ``I:<记录>``，行已认出物料、
仓库已定，账套最小主档）。ERP 的采购引导声明只开在代录（`express.cc_guides.GUIDES` 的 ``modes: ["operator"]``），所以会话是代录会话。
实验栈没有识别，「交文件 → 识别」那一段不走：用 documents 源的公开入口 ``run.start(doc_ids=[I:…], at="asking")``（别的引导 /
代录开始前把已有单据交进来的同一个入口）开这次引导；之后每一步都用页面同一组接口（页面里 ``call`` 调 ``assistant.api.guide_*``）：

1. 题卡逐题作答（有预选照预选，没有点第一项；带服务端搜索的题先 ``guide_search`` 取第一页），直到进复核。
2. ``guide_edit`` 改票面单号：版本 +1、复核行里是新单号；改合计（算出来的格）回 ``refused``（``computed``），版本不变。
3. ``guide_confirm`` 带改之前的复核指纹（票在复核之后改过）：回 ``changed``、回到复核，不建写入计划、不交单。
4. ``guide_confirm`` 带此刻复核卡上的 ``review.seen``：办完（``done``），写入计划开始、这张票交进代录任务。

模型不参与（不说话、不调假智能体）。cleanup 同 `erp_cc_operator_intake.cleanup`：作废提交出的单、撤写账件、删会话（连引导运行、
写入计划）、任务与收单记录、主档种子。
"""
from __future__ import annotations

import json

import erp_cc_operator_intake as intake_flow
from cc_lib import JS

TITLE = "ERP 版 CC 采购引导复核卡：就地改单号（版本 +1）、改合计被拒；带旧复核指纹确定回 changed 不写，带当前指纹确定办完交单"
# 单据源的复核阶段看 ``stage``（review）；运行状态平时是 asking，带旧指纹确定被退回后是 reviewing（`run._confirm_documents`），都算在复核
REVIEW_STATUSES = ("asking", "reviewing")
USER = intake_flow.USER
API = "pearnly_core.assistant.api."


def prepare(lab, manifest):
    params = intake_flow.prepare(lab, manifest)
    try:
        started = _start(lab, manifest, params)
    except Exception:
        # 开引导这一步出错时 prepare 还没回参数，`run.run_flow` 收不到 cleanup：这里先把造出来的会话、记录、种子收掉
        intake_flow.cleanup(lab, manifest, params)
        raise
    new_no = params["tag"].upper() + "-EDIT"
    return {**params, "run": started.get("run") or "", "started": started, "new_no": new_no}


def _start(lab, manifest, params):
    """documents 源的公开入口：把这张识别完成的收单记录交进一次采购引导，从题卡接着走（`guide.run.start(doc_ids=…)`）。"""
    return lab.python(manifest["site"], f"""
from pearnly_core.assistant.guide import run
frappe.set_user({USER!r})
try:
    found = run.start({params['session']!r}, "purchase", doc_ids=[{params['doc_id']!r}], at="asking")
    frappe.db.commit()
    print(json.dumps({{"run": found["run"], "status": found["status"], "stage": found.get("stage") or ""}}, default=str))
except Exception as error:
    frappe.db.rollback()
    print(json.dumps({{"run": "", "error": repr(error)[:300]}}))
""")


def cleanup(lab, manifest, params):
    intake_flow.cleanup(lab, manifest, params)


def _head(params):
    """每步开头：等页面载入（有 CSRF 令牌），定好本次的引导运行与单据；``view()`` 读此刻的卡（同页面 ``guide_view``）。"""
    return (JS + "await until(()=>q('textarea.aui-composer-input'),120); "
            f"const RUN={json.dumps(params['run'])}, DOC={json.dumps(params['doc_id'])}; "
            "if(!RUN) return {error:'no guide run'}; "
            f"const view=async()=>await call('{API}guide_view',{{run_name:RUN}}); "
            "const rowOf=v=>(((v||{}).review||{}).rows||[]).find(r=>r.doc_id===DOC)||null; ")


def steps(params, camera_code=None):
    head = _head(params)
    new_no = json.dumps(params["new_no"])
    return [
        # 题卡逐题作答直到复核（documents 源每轮答完记下、下一轮可能出账本那边的新题；最多 5 轮）
        {"name": "作答到复核", "js": head +
         "const pick=async(qn)=>{ if(qn.preselected) return String(qn.preselected); let opts=qn.options||[]; "
         f" if(!opts.length&&qn.search){{ const s=await call('{API}guide_search',{{run_name:RUN,key:qn.key,txt:'',start:0,page_length:20}}); "
         "  opts=(s||{}).options||[]; } "
         " const plain=opts.find(o=>!o.entry); if(plain) return String(plain.code); return opts[0]?String(opts[0].code)+':1':''; }; "
         "let v=await view(); const rounds=[]; "
         "for(let k=0;k<5&&v&&v.stage==='asking';k++){ const qs=((v.card||{}).questions||[]); const answers=[]; "
         " for(const qn of qs){ const value=await pick(qn); if(value) answers.push({key:qn.key, value}); } "
         " rounds.push(qs.map(x=>x.key)); "
         f" const r=await call('{API}guide_answer',{{run_name:RUN, answers:JSON.stringify({{answers}}), version:v.version}},true); "
         " if(!r) return {error:'answer failed', rounds}; if(r.outcome) return {error:'answer '+r.outcome, refused:r.refused, rounds}; v=r; } "
         "const row=rowOf(v); "
         "return {status:(v||{}).status||'', stage:(v||{}).stage||'', version:(v||{}).version||0, rounds, "
         "rows:(((v||{}).review||{}).rows||[]).map(r=>r.doc_id), held:((v||{}).review||{}).held||[], seen:((v||{}).review||{}).seen||'', "
         "doc_no:row?((row.face||{}).doc_no||''):'', editable:row?(row.editable||[]):[], blocking:row?(row.blocking||[]):[]};"},
        # 就地改单号：收下版本 +1，复核卡照新值出；改前的指纹留给「旧指纹确定」那一步（每步是独立脚本，靠页面全局变量接力）
        {"name": "改单号", "js": head +
         "const v=await view(); window.__flowSeenBefore=((v||{}).review||{}).seen||''; "
         f"const r=await call('{API}guide_edit',{{run_name:RUN, doc_id:DOC, field:'doc_no', value:{new_no}, version:(v||{{}}).version}},true)||{{}}; "
         "const row=rowOf(r); "
         "return {before:(v||{}).version||0, version:r.version||0, outcome:r.outcome||'', status:r.status||'', stage:r.stage||'', "
         "doc_no:row?((row.face||{}).doc_no||''):'', seen_before:window.__flowSeenBefore, seen_after:(r.review||{}).seen||''};"},
        # 合计是算出来的格：不收（产品回原因码 computed），卡与版本不变
        {"name": "改合计", "js": head +
         "const v=await view(); "
         f"const r=await call('{API}guide_edit',{{run_name:RUN, doc_id:DOC, field:'total', value:'999.00', version:(v||{{}}).version}},true)||{{}}; "
         "const row=rowOf(r); "
         "return {before:(v||{}).version||0, version:r.version||0, outcome:r.outcome||'', refused:r.refused||{}, "
         "refused_text:r.refused_text||{}, total:row?((row.face||{}).total||''):''};"},
        # 带改单号之前的复核指纹确定：票在复核之后改过 → changed、回到复核，不办
        {"name": "旧指纹确定", "js": head +
         "const v=await view(); const old=window.__flowSeenBefore||'flow-stale-seen'; window.__flowStaleVersion=(v||{}).version||0; "
         f"const r=await call('{API}guide_confirm',{{run_name:RUN, version:(v||{{}}).version, seen:old}},true)||{{}}; "
         "return {before:(v||{}).version||0, version:r.version||0, outcome:r.outcome||'', status:r.status||'', stage:r.stage||'', "
         "rows:((r.review||{}).rows||[]).map(x=>x.doc_id), seen:(r.review||{}).seen||'', old, "
         "stale_consent:'guide:'+RUN+':'+((v||{}).version||0)};"},
        # 带此刻复核卡上的指纹确定：办完，结果卡列这张票
        {"name": "确定", "js": head +
         "const v=await view(); const current=((v||{}).review||{}).seen||''; "
         f"const r=await call('{API}guide_confirm',{{run_name:RUN, version:(v||{{}}).version, seen:current}},true)||{{}}; "
         "const res=r.result||{}; "
         "return {before:(v||{}).version||0, seen:current, outcome:r.outcome||'', status:r.status||'', result_status:res.status||'', "
         "rows:(res.rows||[]).map(x=>({doc_id:x.doc_id, status:x.status, reason:x.reason||'', reason_text:String(x.reason_text||'').slice(0,120)})), "
         "consent:'guide:'+RUN+':'+((v||{}).version||0)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    review, edited, refused, stale, done = (by.get(k) or {} for k in ("作答到复核", "改单号", "改合计", "旧指纹确定", "确定"))
    doc_id = params["doc_id"]
    total_code = (refused.get("refused") or {}).get("total")
    total_code = total_code.get("code") if isinstance(total_code, dict) else total_code
    return [
        {"case": "the handed-in purchase ticket reaches the review card with its doc_no editable", "seen": review,
         "ok": review.get("status") in REVIEW_STATUSES and review.get("stage") == "review" and doc_id in (review.get("rows") or [])
         and "doc_no" in (review.get("editable") or []) and bool(review.get("seen"))},
        {"case": "guide_edit on doc_no is taken: version +1 and the review row shows the new value", "seen": edited,
         "ok": not edited.get("outcome") and edited.get("version") == (edited.get("before") or 0) + 1
         and edited.get("doc_no") == params["new_no"] and edited.get("status") in REVIEW_STATUSES and edited.get("stage") == "review"},
        {"case": "the edit changes the review fingerprint (the seen before it is now stale)", "seen": edited,
         "ok": bool(edited.get("seen_before")) and bool(edited.get("seen_after")) and edited.get("seen_before") != edited.get("seen_after")},
        {"case": "guide_edit on the computed total is refused (computed) and the version stays", "seen": refused,
         "ok": refused.get("outcome") == "refused" and total_code == "computed" and refused.get("version") == refused.get("before")},
        {"case": "guide_confirm with the stale seen answers changed and goes back to review", "seen": stale,
         "ok": stale.get("outcome") == "changed" and stale.get("status") in REVIEW_STATUSES and stale.get("stage") == "review"
         and doc_id in (stale.get("rows") or []) and (stale.get("version") or 0) > (stale.get("before") or 0)},
        {"case": "guide_confirm with the current review.seen is done and the result lists the ticket", "seen": done,
         "ok": not done.get("outcome") and done.get("status") == "done" and done.get("result_status") == "started"
         and any(row.get("doc_id") == doc_id for row in done.get("rows") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    # verify 拿不到页面回包：两次确定按这段会话在库里的写入计划认（旧指纹那次不该留下计划）
    got = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP LINE Intake", {params['intake']!r}, ["status", "purchase", "result"], as_dict=True) or {{}}
result = json.loads(row.get("result") or "{{}}")
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": {params['session']!r}}},
                       fields=["name", "consent", "status", "mode"], order_by="creation asc", limit_page_length=10)
guide = frappe.db.get_value("Pearnly Assistant Guide Run", {params['run']!r}, ["status", "consent", "version"], as_dict=True) or {{}}
task = frappe.get_doc("Pearnly Express Operator Task", {params['task']!r})
print(json.dumps({{"intake": row.get("status"), "made": row.get("purchase") or "", "doc_no": result.get("doc_no") or "",
                  "plans": plans, "guide": guide, "task_docs": [r.doc_id for r in task.get("documents") or []]}}, default=str))
""")
    plans = got.get("plans") or []
    guide = got.get("guide") or {}
    consents = [str(plan.get("consent") or "") for plan in plans]
    return [
        {"case": "the edited doc_no is written back onto the intake record", "seen": got.get("doc_no"),
         "ok": got.get("doc_no") == params["new_no"]},
        {"case": "only the confirm with the current seen made a write plan (the stale one wrote nothing)", "seen": consents,
         "ok": len(plans) == 1 and consents[0] == str(guide.get("consent") or "") and consents[0].startswith(f"guide:{params['run']}:")},
        {"case": "the guide run is done with the page's consent", "seen": guide,
         "ok": guide.get("status") == "done" and str(guide.get("consent") or "").startswith(f"guide:{params['run']}:")},
        {"case": "the ticket was handed to the operator task once", "seen": got.get("task_docs"),
         "ok": (got.get("task_docs") or []).count(params["doc_id"]) == 1},
    ]
