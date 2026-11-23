"""流程用例：补拉 / 轮询一次带回的整条回复直接显示全文，只增不减（11-14 调查：共享笔记「CC对话轮询停在半截-调查-11-14.md」）。

以前组件库的逐字动画对所有回复都开着：补拉一次带回的整条回复（这一轮还在跑时）也按打字效果从头慢慢画出，这段时间里页面上是半截
（实验室没有实时通道、只靠轮询时每条都这样；线上实时通道断开退回轮询时同样会）。现在只有真正逐字推来的那条（逐字预览
event_start / event_delta 打开的）才用逐字动画，整条带回的直接显示全文。

实验室没有实时通道，回复都由补拉带回。假智能体（`lab_agents.py`）认 ``[lab-idle-delay]N[/lab-idle-delay]``：回一段 30 段的长文字，隔 N 秒
才记这一轮结束，把这段时间拉开。页面每 100 毫秒取一次这条回复的字数直到这一轮结束，同时每 500 毫秒问一次服务端事件，记下完整回复什么时候
已在事件记录里。断言：
  - 普通回复、提问卡作答后的回复（11-14 引导卡那次的样子）各量一遍：
  - 补拉一次带回的整条回复一出现就是全文：取样里没有「有字但不全」的时候（改前：组件库按打字效果把已到齐的整条在零点几秒里慢慢画出，
    这时读页面就是半截，11-14 实测 0.6 秒、机器忙时更久）；
  - 回复只增不减（不出现清空重打、变短）；
  - 服务端有了完整回复之后，页面在一次补拉间隔内（这一轮在跑、没连实时通道时 1.2 秒，留余量按 3 秒）显示全文，不等这一轮结束；
  - 这一轮结束后仍是全文。
直写对话，电脑与手机宽度各一遍；跑完删掉这段对话。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "CC 补拉带回的整条回复直接显示全文、只增不减（实验室只靠轮询；假智能体回复后隔几秒才结束这一轮）"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea.aui-composer-input')"
END = "[lab-long-end]"
DELAY = 6
LATE_MS = 3000


ASK = {"questions": [{"question": "Cash or credit?", "header": "Payment", "options": [{"label": "Credit"}, {"label": "Cash"}]}]}

# 量一轮回复（页面上下文里用）：这条回复正文（最后一条回复里全部 .aui-md 的文字）每 100 毫秒取一次直到这一轮结束后 1 秒（最多 40 秒）；
# 同时每 500 毫秒问一次服务端事件，记下第 n 条带结尾标记的完整回复什么时候已在事件记录里
MEASURE = (
    "const body=()=>{const all=qa('[data-role=\"assistant\"]'); const last=all[all.length-1]; "
    "return last?[...last.querySelectorAll('.aui-md')].map(e=>e.innerText).join('\\n'):'';}; "
    "const running=()=>!!q('button.aui-composer-cancel'); const sid=()=>new URL(location.href).searchParams.get('c')||''; "
    "const measure=async(n)=>{const t0=Date.now(); let serverAt=-1, stop=false; "
    "(async()=>{ while(!stop){ try{ const ev=((await call('pearnly_core.assistant.api.events',{session_id:sid()}))||{}).events||[]; "
    "if(serverAt<0&&ev.filter(e=>e.type==='agent.message'&&(e.content||[]).map(b=>b.text||'').join('').includes(END)).length>=n) serverAt=Date.now()-t0; "
    "}catch(x){} await new Promise(r=>setTimeout(r,500)); } })(); "
    "const samples=[]; let endedAt=-1; "
    "for(let k=0;k<400;k++){ const t=body(); samples.push([Date.now()-t0, t.length, t.includes(END), running()]); "
    "if(endedAt<0&&!running()&&samples.some(s=>s[2])) endedAt=Date.now()-t0; "
    "if(endedAt>=0&&Date.now()-t0>endedAt+1000) break; await new Promise(r=>setTimeout(r,100)); } "
    "stop=true; const shrinks=[]; let peak=0; for(const s of samples){ if(s[1]<peak) shrinks.push([s[0],peak,s[1]]); peak=Math.max(peak,s[1]); } "
    "const full=Math.max(0,...samples.map(s=>s[1])); const partials=samples.filter(s=>s[1]>0&&s[1]<full).map(s=>[s[0],s[1],full]); "
    "const final=body(); return {serverAt, partials: partials.slice(0,10), partialCount: partials.length, wholeAt:(samples.find(s=>s[2])||[-1])[0], endedAt, wholeWhileRunning: samples.some(s=>s[2]&&s[3]), "
    "shrinks: shrinks.slice(0,10), count: samples.length, tail: final.slice(-60), finalWhole: final.includes(END), "
    "trace: samples.filter((s,i)=>i%5===0).slice(0,80)};}; "
)


def prepare(lab, manifest):
    site = manifest["site"]
    admin = "admin@cowork.regression" if site.startswith("cowork") else "admin@erp.regression"
    lab.python(site, f'frappe.db.set_value("User", {admin!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "whole" + uuid.uuid4().hex[:6]
    late = f"[lab-idle-delay]{DELAY}[/lab-idle-delay]"
    return {"tag": tag, "say": f"{tag} {late}", "ask": f"{tag} 问一下 [lab-ask]{json.dumps(ASK)}[/lab-ask] {late}", "cases": []}


def steps(params, camera_code=None):
    tag = json.dumps(params["tag"])
    head = JS + f"const END={json.dumps(END)}; " + MEASURE
    return [
        {"name": "普通回复", "js": head + "await until(()=>q('textarea.aui-composer-input'),120); "
         f"const sent=await say({json.dumps(params['say'])}); if(!sent) return {{error:'could not send'}}; "
         f"await until(()=>qa('[data-role=\"user\"]').some(e=>e.innerText.includes({tag})),80); return await measure(1);"},
        # 卡片作答后的回复（11-14 引导卡那次：回复在「卡片作答 → 工具结果」之后出来，同一条消息里还有那张卡）
        {"name": "卡片作答后的回复", "js": head + "await turnDone(); "
         f"const sent=await say({json.dumps(params['ask'])}); if(!sent) return {{error:'could not send ask'}}; "
         "const card=()=>q('[data-slot=\"cc-askq\"]'); if(!await until(()=>card()?.querySelector('[data-slot=\"cc-askq-next\"]'),200)) return {error:'no card'}; "
         "await sleep(600); card().querySelector('[data-slot=\"cc-askq-option\"]')?.click(); await sleep(300); "
         "card().querySelector('[data-slot=\"cc-askq-next\"]')?.click(); return await measure(2);"},
        {"name": "删除会话", "js": JS +
         f"const mine=(await ownChats({tag},'chat')); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    out = []
    for step, label in (("普通回复", "a plain reply"), ("卡片作答后的回复", "the reply after a card is answered")):
        got = by.get(step) or {}
        server, whole = got.get("serverAt", -1), got.get("wholeAt", -1)
        out += [
            {"case": f"{label} brought whole by a poll appears whole at once (no typing out of text that is already all here)",
             "seen": {"partials": got.get("partials"), "partialCount": got.get("partialCount")},
             "ok": got.get("partialCount") == 0 and (got.get("count") or 0) > 10},
            {"case": f"{label} never gets shorter while the turn runs (no clearing and retyping)",
             "seen": {"shrinks": got.get("shrinks"), "count": got.get("count"), "error": got.get("error")},
             "ok": got.get("shrinks") == [] and (got.get("count") or 0) > 10},
            {"case": f"once {label} is whole on the server the page shows all of it within one poll interval, before the turn ends",
             "seen": {k: got.get(k) for k in ("serverAt", "wholeAt", "endedAt", "wholeWhileRunning")},
             "ok": server >= 0 and whole >= 0 and whole - server <= LATE_MS and got.get("wholeWhileRunning") is True},
            {"case": f"{label} is still whole after the turn ends", "seen": {k: got.get(k) for k in ("finalWhole", "tail")},
             "ok": got.get("finalWhole") is True},
        ]
    return out


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("Pearnly Assistant Session", filters={{"title": ["like", "%{params['tag']}%"]}}, pluck="name")
print(json.dumps({{"rows": rows}}))
""")
    return [{"case": "the conversation of this run is deleted", "saved": got["rows"], "ok": got["rows"] == []}]
