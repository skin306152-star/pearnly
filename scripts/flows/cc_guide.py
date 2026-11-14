"""流程用例：CC 引导卡（工具 pearnly_guide，引导式录入第 1 期）——写入前要人定的事由后台出题，模型不出题。

假智能体后端（`scripts/flows/lab_agents.py`）认用户原文里的 ``[lab-tool]{"name": "pearnly_guide", "input": {...}}[/lab-tool]``：照真实引擎发
这个工具的调用。站点出卡前按写账前置判定（CC 对话里共用规则走引导口径）算出卡、存成调用的输入，对话里出引导卡；用户在卡上点「提交」=
对话的「允许」带上答案，站点核对后按引导口径记下（没点过的预选不生效），以 user.custom_tool_result 交回；假后端回
「[lab-agent] pearnly_guide <结果原文>」。不调付费模型。

造的单同 `cowork_operator_lib.py`：一张合成的完整税票（采购，票面没写付款状态）。引导模式下付款方式这一题由服务端出：完整税票按漏斗
预选「赊账」并标「推荐」（怎么录类，点了才算）。断言：
  - 卡来自服务端（执行记录的输入里是服务端出的卡，题目文字是本轮语言），对话里没有 ask_user；
  - 推荐徽章、默认选中推荐项；提交后模型收到 ``answered``，答案按这家供应商记进账套记忆（同一件事下次不再问）；
  - 再调一次 pearnly_guide：付款这一题进「已定」，不再出题（同一件事只问一次）；
电脑与手机宽度各一遍；手机上引导卡不浮成允许卡。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP

TITLE = "CC 引导卡 pearnly_guide：后台出题、推荐预选点了才算、提交后记住、第二次不再问、对话里没有 ask_user"
REPLY = "[lab-agent] pearnly_guide "
USER = "admin@cowork.regression"
CARD = "qa('[data-slot=\"cc-guide-card\"]').pop()"


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    ref = "FLOW-GUIDE-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, CLEANUP + BASE + f"""
name = recognized({ref!r})
# 造单时按这家供应商记住了付款方式：删掉（同公司页删记住的选择），引导卡才会问付款方式（完整税票按漏斗预选赊账）
frappe.db.delete("Cowork Express Choice", {{"connection": conn["name"], "kind": "payment"}})
frappe.db.commit()
since = str(frappe.utils.now_datetime())
print(json.dumps({{"company": company["id"], "doc": name, "since": since}}, default=str))
""")
    tag = "guide" + uuid.uuid4().hex[:6]
    return {**out, "ref": ref, "tag": tag, "cases": []}


def _call(params) -> str:
    tool = {"name": "pearnly_guide", "input": {"company": params["company"], "doc_ids": [params["doc"]]}}
    return f"{params['tag']} 写入前 [lab-tool]{json.dumps(tool, ensure_ascii=False)}[/lab-tool]"


def steps(params, camera_code=None):
    # 引导卡就是提问卡 AskCard（同一个组件，一页一题）：读此刻这一题的题目、选项（名字、选中、推荐徽章）
    read = ("const read=()=>{const c=" + CARD + "; if(!c) return null; "
            "return {phase: c.dataset.phase||'', question: c.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
            "choices: [...c.querySelectorAll('[data-slot=\"cc-askq-option\"]')].map(o=>({text:o.innerText.trim(), checked:o.hasAttribute('data-checked'), "
            "badge: !!o.querySelector('[data-slot=\"badge\"]')})), other: !!c.querySelector('[data-slot=\"cc-askq-other-option\"]'), text: c.innerText};};")
    head = JS + read
    reply = (f"const R={json.dumps(REPLY)}; const whole=(n)=>{{const found=[]; for(const e of qa('[data-role=\"assistant\"]')){{const t=e.innerText; "
             "const i=t.indexOf(R); if(i<0) continue; const s=t.slice(i+R.length); const j=s.lastIndexOf('}'); try{found.push(JSON.parse(s.slice(0,j+1)));}catch(x){}} "
             "return found.length>=n?found[n-1]:null;}; ")
    # 一页一题：每一题有推荐就照推荐（已默认选中），没有推荐的（做不做类）选第一个，点主按钮（下一步 / 提交）直到收起
    finish = ("for(let k=0;k<12;k++){const c=" + CARD + "; if(!c||c.dataset.phase!=='ask') break; "
              "const picked=c.querySelector('[data-slot=\"cc-askq-option\"][data-checked]'); "
              "if(!picked) c.querySelector('[data-slot=\"cc-askq-option\"]')?.click(); await sleep(200); "
              "c.querySelector('[data-slot=\"cc-askq-next\"]')?.click(); await sleep(400);} ")
    menu = ("const row=()=>q('[data-slot=\"cc-quick-replies\"][data-business]'); const ph=()=>q('textarea[aria-label]')?.getAttribute('placeholder')||''; ")
    # 选了业务 CC 在对话里说一句要什么（页面画、不进模型上下文）：读此刻对话里这一句（几条、哪个业务、文字、算不算模型回复）
    prompt = ("const said=()=>{const all=qa('[data-slot=\"cc-business-prompt\"]'); return {count: all.length, "
              "business: all[0]?.dataset.business||'', text: all[0]?.innerText.trim()||'', reply: !!all[0]?.closest('[data-role]')};}; ")
    return [
        {"name": "业务菜单", "js": JS + menu + prompt + "const shown=await until(()=>row(),120); const labels=[...(row()?.querySelectorAll('button')||[])].map(b=>b.innerText.trim()); "
         "const pick=async(l)=>{const b=[...(row()?.querySelectorAll('button')||[])].find(x=>x.innerText.trim()===l); b?.click(); await sleep(400);}; "
         "const cancel=async()=>{row()?.querySelector('button')?.click(); await sleep(400);}; "
         "await pick('采购'); const picked={chosen: row()?.dataset.business||'', placeholder: ph(), prompt: said()}; "
         "await cancel(); const cancelled=said(); "
         "await pick('销售'); const switched=said(); await cancel(); "
         "return {shown, labels, picked, cancelled, switched, cleared: row()?.dataset.business ?? null, placeholder: ph(), after: said()};"},
        {"name": "出卡", "js": head + f"const sent=await say({json.dumps(_call(params))}); const shown=await until(()=>{CARD}?.dataset.phase==='ask',200); "
         "await sleep(800); return {...read(), sent, seen: shown, sheet: !!q('[data-slot=\"cc-ask-sheet\"]'), ask: !!q('[data-slot=\"cc-askq\"]')};"},
        {"name": "提交", "js": head + reply + finish +
         "const done=await until(()=>" + CARD + "?.dataset.phase==='answered',200); const got=await until(()=>whole(1),200); "
         "return {done, result: whole(1), after: read()};"},
        {"name": "第二次", "js": head + reply + "const before=qa('[data-slot=\"cc-guide-card\"]').length; "
         f"const sent=await say({json.dumps(_call(params))}); "
         "const got=await until(()=>whole(2)||(qa('[data-slot=\"cc-guide-card\"]').length>before&&" + CARD + "?.dataset.phase==='ask'),200); "
         "await sleep(500); const card=qa('[data-slot=\"cc-guide-card\"]').length>before?read():null; "
         # 模型第二次收到的结果：页面读全了就用页面的；实验室没有实时通道（只轮询），偶尔停在半截回复（已知问题笔记 11-09 条），
         # 读不全时按服务端事件取同一句（同对话页取事件的接口），页面上「没出新卡」照旧按页面判
         "let result=whole(2); if(!result){ const sid=new URL(location.href).searchParams.get('c')||''; "
         "const ev=((await call('pearnly_core.assistant.api.events',{session_id:sid}))||{}).events||[]; "
         "const texts=ev.filter(e=>e.type==='agent.message').map(e=>(e.content||[]).map(b=>b.text||'').join('')).filter(t=>t.includes(R)); "
         "const t=texts[1]||''; const i=t.indexOf(R); if(i>=0){ const s=t.slice(i+R.length); try{ result=JSON.parse(s.slice(0,s.lastIndexOf('}')+1)); }catch(x){} } "
         "result=result?{...result, from:'events'}:null; } "
         "return {result, card, sent};"},
        {"name": "会话里选业务", "js": JS + menu + prompt + "await until(()=>row(),120); "
         "const b=[...(row()?.querySelectorAll('button')||[])].find(x=>x.innerText.trim()==='采购'); b?.click(); "
         "await until(()=>said().count>0,40); await sleep(1500); "  # 等服务端记下（POST business）再刷新
         "const sid=new URL(location.href).searchParams.get('c')||''; "
         "const mine=(await listed('chat')).find(x=>x.id===sid)||{}; "
         "return {sid, prompt: said(), saved: {business: mine.business||'', at: mine.business_at||''}};"},
        {"name": "刷新", "js": "setTimeout(()=>location.reload(),50); return true;", "wait_ms": 4000},
        {"name": "刷新后", "js": JS + menu + prompt + "await until(()=>q('textarea[aria-label]'),160); await until(()=>said().count>0,120); "
         "const kept=said(); row()?.querySelector('button')?.click(); await sleep(800); "
         "const sid=new URL(location.href).searchParams.get('c')||''; const mine=(await listed('chat')).find(x=>x.id===sid)||{}; "
         "return {kept, gone: said(), saved: {business: mine.business||'', at: mine.business_at||''}};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    menu, first, sent, again = (by.get(k) or {} for k in ("业务菜单", "出卡", "提交", "第二次"))
    inside, after = (by.get(k) or {} for k in ("会话里选业务", "刷新后"))
    one = lambda seen, business, text: (seen or {}).get("count") == 1 and seen.get("business") == business and seen.get("text") == text \
        and not seen.get("reply")
    pay = first if first.get("question") == "付款方式" else {}
    choices = pay.get("choices") or []
    return [
        {"case": "输入框上方的业务菜单：Cowork 只有采购、销售（没有收款）；点「采购」占位字换成「上传采购票据」，点 × 取消后回到原样",
         "seen": menu,
         "ok": bool(menu.get("shown")) and menu.get("labels") == ["采购", "销售"]
         and (menu.get("picked") or {}).get("chosen") == "purchase" and (menu.get("picked") or {}).get("placeholder") == "上传采购票据"
         and menu.get("cleared") == "" and menu.get("placeholder") != "上传采购票据"},
        {"case": "选了业务 CC 在对话里说一句要什么（不是只换输入框提示）：点「采购」出一条「请上传采购票据。」、不算模型回复；"
                 "点 × 收起；换「销售」只剩一条「请上传销售票据。」；再取消就没有",
         "seen": {k: menu.get(k) for k in ("picked", "cancelled", "switched", "after")},
         "ok": one((menu.get("picked") or {}).get("prompt"), "purchase", "请上传采购票据。")
         and (menu.get("cancelled") or {}).get("count") == 0 and one(menu.get("switched"), "sales", "请上传销售票据。")
         and (menu.get("after") or {}).get("count") == 0},
        {"case": "会话里选「采购」：这段还没交过文件，出那一句；服务端记下业务与选的时刻；刷新后照样只一条（按服务端重算）；点 × 收起、服务端清空",
         "seen": {"inside": inside, "after": after},
         "ok": one(inside.get("prompt"), "purchase", "请上传采购票据。") and (inside.get("saved") or {}).get("business") == "purchase"
         and bool((inside.get("saved") or {}).get("at")) and one(after.get("kept"), "purchase", "请上传采购票据。")
         and (after.get("gone") or {}).get("count") == 0 and (after.get("saved") or {}).get("business") == ""},
        {"case": "模型调 pearnly_guide 后对话里出引导卡：题目是服务端出的中文（付款方式），选项「赊账」带「推荐」并默认选中，「现金」不选；没有 ask_user 提问卡、不浮成允许卡",
         "seen": first,
         "ok": bool(first.get("seen")) and bool(pay) and any("赊账" in c["text"] and c["badge"] and c["checked"] for c in choices)
         and any("现金" in c["text"] and not c["checked"] and not c["badge"] for c in choices) and not first.get("other")
         and not first.get("sheet")},
        {"case": "提交后卡片收成「已答 n 项」，模型收到 answered、这张单能写", "seen": sent,
         "ok": bool(sent.get("done")) and (sent.get("result") or {}).get("status") == "answered"
         and (sent.get("result") or {}).get("answered", 0) >= 1 and params["doc"] in ((sent.get("result") or {}).get("writable") or [])},
        {"case": "同一件事只问一次：再调一次，付款方式已记住——要么不用问，要么新卡里没有「付款方式」这一题", "seen": again,
         "ok": (again.get("result") or {}).get("status") == "nothing_to_ask"
         or (bool(again.get("card")) and (again.get("card") or {}).get("question") != "付款方式" and "付款方式" not in (again.get("card") or {}).get("text", ""))},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], CLEANUP + f"""
runs = frappe.get_all("Pearnly Assistant Tool Run", filters={{"user": {USER!r}, "creation": [">=", {params["since"]!r}]}},
                      fields=["tool", "status", "result", "tool_input"], order_by="creation asc", limit_page_length=20)
choices = frappe.get_all("Cowork Express Choice", filters={{"kind": "payment"}}, fields=["subject", "value"], limit_page_length=20)
gone = cleanup()
print(json.dumps({{"runs": runs, "choices": choices, "gone": gone, "left": leftovers()}}, default=str))
""")
    runs = rows["runs"]
    guides = [r for r in runs if r.get("tool") == "pearnly_guide"]
    first = guides[0] if guides else {}
    card = (json.loads(first.get("tool_input") or "{}") or {}).get("card") or {}
    second = json.loads((guides[1] if len(guides) > 1 else {}).get("result") or "{}")
    return [
        {"case": "执行记录：第一次引导卡的输入是服务端出的卡（带题目文字），做完、结果 answered", "saved": first,
         "ok": first.get("status") == "done" and any(q.get("kind") == "payment" and q.get("title") for q in card.get("questions") or [])
         and json.loads(first.get("result") or "{}").get("status") == "answered"},
        {"case": "整段对话没有 ask_user 调用（业务题全由后台出）", "saved": [r.get("tool") for r in runs],
         "ok": not any(r.get("tool") == "ask_user" for r in runs)},
        {"case": "付款方式按这家供应商记进账套记忆；第二次付款这一题不再问", "saved": {"choices": rows["choices"], "second": second},
         "ok": bool(rows["choices"]) and (second.get("status") == "nothing_to_ask"
                                          or not any(q.get("kind") == "payment" for q in ((json.loads((guides[1] if len(guides) > 1 else {}).get("tool_input") or "{}") or {}).get("card") or {}).get("questions") or []))},
        {"case": "造的连接、公司、单据都清掉", "saved": {"gone": rows["gone"], "left": rows["left"]},
         "ok": rows["gone"]["connections"] == 1 and not any(rows["left"].values())},
    ]
