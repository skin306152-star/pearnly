"""流程用例：ERP 版 CC 收款引导卡在复核卡上点「改」回去重问两次（guide_reopen），库里这位客户的收款草稿始终只有一笔
（A2 11-19 实验栈查出：每改一次就多留一笔没有同意记录的草稿；按类修在引擎：重问时保留上一次的草稿号，下一次生成草稿时
产品据它作废上一张，`assistant.guide.run.reopen`）。

真页面、假智能体（只用来开一段对话），收款数据同一套造法（`collection_lib.seed`）；卡上作答用页面同一组接口（guide_start / guide_view /
guide_answer / guide_reopen，页面的登录身份）。不连 Express、不发 LINE、不调模型。

断言的是应有状态：
- 第一次到复核：库里这位客户 1 笔草稿；
- 改发票那一步再复核、改客户那一步再复核：每次回 asking、版本 +1、那一题的 value 是原值；再到复核后库里仍只有 1 笔 draft，
  另两笔是 withdrawn（被作废，不是孤儿）；
- 最后一笔就是运行记录此刻的草稿。
"""
from __future__ import annotations

import json
import uuid

from cc_approval_lib import JS, set_language
from collection_lib import restore, seed

TITLE = "ERP 版 CC 收款引导卡复核后点「改」回去重问两次：每次回到那一步、原值在题上，库里这位客户的收款草稿始终只有一笔，旧的都作废"
USER = "admin@erp.regression"

# 页面小工具：按 run 取卡，等发票那一题读好（不在等读取）再答
HELP = (
    "const api=(m,a,w)=>call('pearnly_core.assistant.api.'+m,a,w); "
    "const view=async(run)=>api('guide_view',{run_name:run}); "
    "const ready=async(run,key)=>{for(let k=0;k<60;k++){const v=await view(run); "
    "const q=((v.card||{}).questions||[]).find(x=>x.key===key); if(v.status!=='asking'||(q&&!q.wait&&(q.options||[]).length)) return v; "
    "await sleep(1500);} return view(run);}; "
    "const ans=async(run,given)=>{const v=await view(run); return api('guide_answer',{run_name:run,answers:JSON.stringify(given),"
    "version:v.version},true);}; "
)


def prepare(lab, manifest):
    site = manifest["site"]
    set_language(lab, site, USER, "zh")
    tag = "gr" + uuid.uuid4().hex[:6]
    return {**seed(lab, manifest, tag), "tag": tag}


def steps(params, camera_code=None):
    tag, code, first = params["tag"], json.dumps(params["customer"]), json.dumps(params["invoices"][0])
    walk = (HELP + "const run=window.__gr_run; "
            f"await ans(run,{{customer:{code}}}); await ready(run,'invoices'); "
            f"await ans(run,{{invoices:[{{code:{first},amount:'600'}}]}}); "
            "const done=await ans(run,{methods:[{code:'cash',amount:'600'}]}); ")
    return [
        {"name": "开对话", "js": JS + f"const sent=await say({json.dumps(tag + ' 你好')}); await sleep(1500); return {{sent}};"},
        {"name": "开收款卡到复核", "js": JS + HELP +
         f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({json.dumps(tag)})); const sid=(mine[0]||{{}}).id; "
         "const v=await api('guide_start',{session_id:sid,business:'collection'},true); window.__gr_run=v.run; "
         "await ready(v.run,'customer'); " + walk[len(HELP):] +
         "return {run: v.run, status: done.status, version: done.version};"},
        {"name": "改发票再复核", "js": JS + HELP +
         "const run=window.__gr_run; const before=await view(run); "
         "const back=await api('guide_reopen',{run_name:run,step:'invoices',version:before.version},true); "
         "const q=((back.card||{}).questions||[]).find(x=>x.key==='invoices')||{}; await ready(run,'invoices'); "
         f"await ans(run,{{invoices:[{{code:{first},amount:'500'}}]}}); const done=await ans(run,{{methods:[{{code:'cash',amount:'500'}}]}}); "
         "return {back: back.status, from: before.version, to: back.version, value: q.value||null, preselected: q.preselected||null, "
         "status: done.status};"},
        {"name": "改客户再复核", "js": JS + HELP +
         "const run=window.__gr_run; const before=await view(run); "
         "const back=await api('guide_reopen',{run_name:run,step:'customer',version:before.version},true); "
         "const q=((back.card||{}).questions||[]).find(x=>x.key==='customer')||{}; " + walk[len(HELP) + len("const run=window.__gr_run; "):] +
         "return {back: back.status, from: before.version, to: back.version, value: q.value||null, status: done.status};"},
        {"name": "删除会话", "js": JS + HELP + "const run=window.__gr_run; if(run) await api('guide_cancel',{run_name:run},true); "
         f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({json.dumps(tag)})); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, invoices, customer = (by.get(k) or {} for k in ("开收款卡到复核", "改发票再复核", "改客户再复核"))
    return [
        {"case": "第一次走到复核", "seen": first, "ok": first.get("status") == "reviewing" and bool(first.get("run"))},
        {"case": "改发票：回 asking、版本 +1、题上是原来填的那一行（不当推荐），再到复核", "seen": invoices,
         "ok": invoices.get("back") == "asking" and invoices.get("to") == (invoices.get("from") or 0) + 1
         and isinstance(invoices.get("value"), list) and bool(invoices["value"]) and invoices.get("preselected") is None
         and invoices.get("status") == "reviewing"},
        {"case": "改客户：回 asking、版本 +1、题上是原客户码，再到复核", "seen": customer,
         "ok": customer.get("back") == "asking" and customer.get("to") == (customer.get("from") or 0) + 1
         and customer.get("value") == params["customer"] and customer.get("status") == "reviewing"},
        {"case": "删掉这段会话", "seen": by.get("删除会话"), "ok": bool((by.get("删除会话") or {}).get("ids"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Collection", filters={{"customer": {params['customer']!r}, "source": "cc"}},
                      fields=["name", "status", "consent_ref"], order_by="creation asc", limit_page_length=10)
print(json.dumps({{"rows": rows}}, default=str))
""")
    rows = out.get("rows") or []
    drafts = [row for row in rows if row.get("status") == "draft"]
    return [
        {"case": "库里这位客户的收款：1 笔草稿（最后一次复核的），另两笔作废，没有孤儿", "seen": out,
         "ok": len(drafts) == 1 and len(rows) == 3 and all(row.get("status") == "withdrawn" for row in rows[:2])
         and rows[-1].get("status") == "draft" and not any(row.get("consent_ref") for row in rows)},
    ]


def cleanup(lab, manifest, params):
    restore(lab, manifest, params)
