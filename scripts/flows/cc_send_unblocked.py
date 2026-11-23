"""流程用例：CC 输入框不被挂着的东西挡住（11-11 发布核查与压测 S8a 的两处「发不出去」，主会话 11-12 审定）。

每一步都在页面上点（共享笔记「已知问题与结论」：界面类验收必须在界面上点）：

1. 永远传不上的文件（超限、空文件、不收的类型）选中那一刻就拒收：不进输入框、没有附件卡，输入框上方那一行说「文件超过 40 MB」；同一次选的另一份没超限的照常
   加入并传上。接着打一句发送，发得出去、带着那份没超限的。以前超限的留一张失败卡，发送时重传必败、整条发不出，不删卡就永远发不出。
   空文件同样拒收。
2. 提问卡 ask_user 浮在输入框上方等作答时在输入框里打字发送（照 Claude，审计 11-09 第六节，主会话 11-19）：这句就是当前这题的
   「其他」，后面没答的题跳过，当场交卡（不排队），对话里不另出这句的气泡；CC 收到的是作答结果，卡片收成只读摘要。以前这句排着
   等这一轮结束，而这一轮正等用户答卡，永远发不出。
3. 「允许 / 拒绝」卡挂着时打字发送：同上，那张卡作废，工具没有执行（站点执行记录是 cancelled，没有执行过）。这条路任何情况下
   不能变成「允许」。

4. 一时传不上的附件（断网）照旧（10-17「不静默丢附件」）：进了输入框的附件留一张失败卡，卡上看得出是哪份文件、为什么；带着它
   点发送整条不发、字放回输入框、上方写原因；点卡上的 × 移除后发得出去。

实验室假智能体（`lab_agents.py`）照真实引擎：停在等工具结果时收到用户消息回 400——站点不先交回卡片，这句就发不出。
直写对话，电脑与手机宽度、Cowork 与 ERP 各跑一遍（代录会话的输入框是同一个组件、同一个发送接口）。跑完删掉这几段会话。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "CC 输入框不被挂着的东西挡住：超限文件选中时拒收不留失败卡；提问卡浮着时打字即作答；允许卡挂着时打字当场发出，站点先结束那一轮、卡片作废（不执行）"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
NOTE_TOOL = {"cowork": "cowork_company_note_save", "erp": "erp_company_note_save"}
QUESTIONS = {"questions": [
    {"question": "Which account set?", "header": "Account set",
     "options": [{"label": "T.8", "description": "Period 2025"}, {"label": "T.9", "description": "Period 2025"}]},
    {"question": "Cash or credit?", "header": "Payment", "options": [{"label": "Credit"}, {"label": "Cash"}]}]}

# 页面此刻的样子：输入框的字、上方那一行、附件卡、排队的句子、右边那颗按钮、对话里的气泡与回复
STATE = ("const state=()=>({text: q('textarea.aui-composer-input')?.value||'', alert: q('[data-slot=\"cc-send-failure\"]')?.innerText.trim()||'', "
         "cards: qa('.aui-attachment-root').length, failed: qa('[data-slot=\"attachment\"][data-state=\"error\"]').length, "
         "queue: qa('[data-slot=\"cc-queue-item\"]').length, send: !!q('button.aui-composer-send'), stop: !!q('button.aui-composer-cancel'), "
         "users: qa('[data-role=\"user\"]').map(e=>e.innerText.trim().slice(0,80)), "
         "replies: qa('[data-role=\"assistant\"]').map(e=>e.innerText.trim())});")
# 一次选几份文件（同点「+」）：sizes 里 -1 = 比上限大 1MB、0 = 空文件、其余是字节数
PICK = ("const pick=async specs=>{const max=Number(window.pearnly_upload_max_bytes)||0; "
        "const files=specs.map(([name,size,type])=>new File([new Uint8Array(size<0?max+1024*1024:size)],name,{type})); "
        "const orig=HTMLInputElement.prototype.click; HTMLInputElement.prototype.click=function(){ if(this.type==='file'){ const dt=new DataTransfer(); "
        "for(const f of files) dt.items.add(f); this.files=dt.files; this.dispatchEvent(new Event('change',{bubbles:true})); return; } return orig.call(this); }; "
        "q('button.cc-attach')?.click(); HTMLInputElement.prototype.click=orig; await sleep(600); return max;};")
# 在输入框里打一句、点发送键（发送键不在或点不了回 false）
TYPE = ("const type=async t=>{const ta=q('textarea.aui-composer-input'); if(!ta) return false; "
        "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,t); ta.dispatchEvent(new Event('input',{bubbles:true})); "
        "await sleep(400); const b=q('button.aui-composer-send'); if(!b||b.disabled) return false; b.click(); return true;};")
HAS = "const has=(list,t)=>list.some(x=>x.includes(t));"


def prepare(lab, manifest):
    site = manifest["site"]
    product = "cowork" if "cowork" in site else "erp"
    lab.python(site, f'frappe.db.set_value("User", "admin@{product}.regression", "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "unb" + uuid.uuid4().hex[:6]
    start = lab.python(site, 'print(json.dumps({"now": str(frappe.utils.now_datetime())}))')["now"]
    call = {"name": NOTE_TOOL[product], "input": {"text": f"{tag} must not be saved"}}
    return {"tag": tag, "product": product, "start": start,
            "ask": f"{tag}-ask [lab-ask]{json.dumps(QUESTIONS)}[/lab-ask]", "gate": f"{tag}-gate [lab-tool]{json.dumps(call)}[/lab-tool]",
            "words": {"file": f"{tag} 带着这份发", "ask": f"{tag} 改写进别家", "gate": f"{tag} 先别存", "offline": f"{tag} 断网时发"}, "cases": []}


def steps(params, camera_code=None):
    head = JS + STATE + PICK + TYPE + HAS
    words = params["words"]
    reply = lambda text: json.dumps("[lab-agent] " + text[:30])  # noqa: E731 — 假智能体回的是这句的前一段
    open_new = {"name": "开新对话", "js": JS + "setTimeout(()=>location.assign('/assistant'),0); return {};", "wait_ms": 5000}
    return [
        {"name": "选超限与空文件", "shot": True, "file": "1-refused-at-pick", "js": head +
         "const had=await listed('chat'); "
         "const max=await pick([['big.pdf',-1,'application/pdf']]); const big=state().alert; "
         "await pick([['empty.pdf',0,'application/pdf']]); const empty=state().alert; "
         # 两种不收的类型（拖进、粘贴不经选文件框的筛选）：都由页面按服务端清单拒收
         "await pick([['photo.bmp',64,'image/bmp']]); const bmp=state().alert; "
         "await pick([['archive.zip',64,'application/zip']]); const zip=state().alert; const refused={...state(), big, empty, bmp, zip}; "
         "const sessions=(await listed('chat')).length-had.length; const blank=had.filter(s=>!(s.title||'').trim()).length; "
         "await pick([['big2.pdf',-1,'application/pdf'],['ok.txt',64,'text/plain']]); "
         "const uploaded=await until(()=>qa('.aui-attachment-root').length===1&&!q('.aui-attachment-tile-uploading')&&!q('[data-slot=\"attachment\"][data-state=\"error\"]'),160); "
         "return {max, refused, sessions, blank, uploaded, mixed: state()};"},
        {"name": "带着没超限的发出", "shot": True, "file": "2-sent-with-the-good-file", "js": head +
         f"const clicked=await type({json.dumps(words['file'])}); "
         f"const bubble=await until(()=>has(state().users,{json.dumps(words['file'])}),80); "
         f"const replied=await until(()=>has(state().replies,{reply(words['file'])}),240); "
         "return {clicked, bubble, replied, after: state(), file: qa('[data-role=\"user\"]').some(e=>e.innerText.includes('ok.txt'))||!!q('[data-role=\"user\"] .cc-file-card, .aui-user-message-attachments-end')};"},
        open_new,
        {"name": "提问卡挂着时打字发送", "shot": True, "file": "3-typed-while-question-card-waits", "js": head +
         f"const asked=await say({json.dumps(params['ask'])}); const card=await until(()=>q('[data-slot=\"cc-ask-float\"] [data-slot=\"cc-askq-next\"]'),200); await sleep(800); "
         "const before=state(); "
         f"const clicked=await type({json.dumps(words['ask'])}); await sleep(600); const queued=state().queue; "
         f"const answered=await until(()=>(q('[data-slot=\"cc-ask-answer\"]')?.innerText||'').includes({json.dumps(words['ask'])}),80); "
         f"const replied=await until(()=>state().replies.some(r=>r.includes('[lab-agent] ask_user')&&r.includes({json.dumps(words['ask'])})),240); "
         f"const bubble=has(state().users,{json.dumps(words['ask'])}); "
         "const settled=await until(()=>!q('[data-slot=\"cc-askq-next\"]'),80); "
         "const idle=await until(()=>!q('button.aui-composer-cancel'),80); "
         "q('[data-slot=\"cc-ask-answer\"]')?.scrollIntoView({block:'center'}); await sleep(400); "
         "return {asked, card, before, clicked, queued, answered, bubble, replied, settled, idle, after: state(), "
         "answer: q('[data-slot=\"cc-ask-answer\"]')?.innerText.trim()||''};"},
        open_new,
        {"name": "允许卡挂着时打字发送", "shot": True, "file": "4-typed-while-allow-card-waits", "js": head +
         # 电脑上允许卡在对话流里（拒绝、允许两颗）；手机上浮在输入框上方（cc-ask-sheet）
         "const buttons=()=>[...qa('.cc-ask-foot .cc-card-btn'), ...qa('[data-slot=\"cc-ask-sheet\"] .cc-card-btn')].filter(seen); "
         f"const asked=await say({json.dumps(params['gate'])}); const card=await until(()=>buttons().length>=2,200); await sleep(800); "
         "const before=state(); "
         f"const clicked=await type({json.dumps(words['gate'])}); await sleep(600); const queued=state().queue; "
         f"const bubble=await until(()=>has(state().users,{json.dumps(words['gate'])}),80); "
         f"const replied=await until(()=>has(state().replies,{reply(words['gate'])}),240); "
         "const settled=await until(()=>buttons().length===0,80); "
         "const idle=await until(()=>!q('button.aui-composer-cancel'),80); "
         "q('[data-slot=\"cc-ask-answer\"]')?.scrollIntoView({block:'center'}); await sleep(400); "
         "return {asked, card, before, clicked, queued, bubble, replied, settled, idle, after: state(), "
         "answer: qa('[data-slot=\"cc-ask-answer\"]').map(e=>e.innerText.trim())};"},
        open_new,
        {"name": "一时传不上的附件", "shot": True, "file": "5-upload-failed-card", "js": head +
         # 上传接口一时不通（只这一个接口回 503）：进了输入框的附件留失败卡，卡上有文件名与原因
         "window.__unblock=blockUploads(); "
         "await pick([['offline.txt',64,'text/plain']]); const failed=await until(()=>qa('[data-slot=\"attachment\"][data-state=\"error\"]').length===1,120); "
         "const tile=q('[data-slot=\"attachment\"][data-state=\"error\"]'); const card={name: tile?.querySelector('[data-slot=\"attachment-title\"]')?.innerText.trim()||'', "
         "reason: tile?.querySelector('[data-slot=\"attachment-description\"]')?.innerText.trim()||''}; "
         f"const clicked=await type({json.dumps(words['offline'])}); "
         f"const back=await until(()=>state().text.includes({json.dumps(words['offline'])})&&!!state().alert,120); const held=state(); "
         "return {failed, card, clicked, back, held};"},
        {"name": "移除失败卡后发出", "shot": True, "file": "6-sent-after-removing-the-card", "js": head +
         "if(window.__unblock) window.__unblock(); "
         "await until(()=>qa('[data-slot=\"attachment\"][data-state=\"error\"]').length===1&&!q('.aui-attachment-tile-uploading'),120); "
         "const x=q('.aui-attachment-tile-remove'); x?.click(); const removed=await until(()=>state().cards===0,40); "
         "const b=q('button.aui-composer-send'); const clicked=!!b&&!b.disabled; if(clicked) b.click(); "
         f"const bubble=await until(()=>has(state().users,{json.dumps(words['offline'])}),80); "
         f"const replied=await until(()=>has(state().replies,{reply(words['offline'])}),240); "
         "return {removed, clicked, bubble, replied, after: state()};"},
        {"name": "删除会话", "js": JS +
         f"const mine=(await ownChats({json.dumps(params['tag'])},'chat')); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); "
         "return {ids: mine.map(s=>s.id), left: (await listed('chat')).filter(s=>!(s.title||'').trim()).length};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    picked, sent, asked, gated, offline, cleared, removed = (by.get(k) or {} for k in (
        "选超限与空文件", "带着没超限的发出", "提问卡挂着时打字发送", "允许卡挂着时打字发送", "一时传不上的附件", "移除失败卡后发出",
        "删除会话"))
    refused, mixed = picked.get("refused") or {}, picked.get("mixed") or {}
    words = params["words"]
    limit = str(int(picked.get("max") or 0) // (1024 * 1024))
    replies = lambda step: [r for r in (step.get("after") or {}).get("replies") or [] if "[lab-agent]" in r]  # noqa: E731
    return [
        {"case": "超限、空文件、不收的类型选中时拒收：没有附件卡，输入框上方那一行各说各的原因（超限的带上限 MB 数），没有因此建会话", "seen": picked,
         "ok": refused.get("cards") == 0 and picked.get("sessions") == 0 and limit != "0" and limit in (refused.get("big") or "")
         and refused.get("empty") == "文件为空" and refused.get("bmp") == "不支持此文件类型" and refused.get("zip") == "不支持此文件类型"},
        {"case": "一次选两份、其中一份超限：超限的不进输入框，没超限的照常加入并传上，上方说「文件超过 N MB」", "seen": mixed,
         "ok": bool(picked.get("uploaded")) and mixed.get("cards") == 1 and mixed.get("failed") == 0
         and limit != "0" and limit in (mixed.get("alert") or "")},
        {"case": "接着打一句点发送：发得出去（气泡在、CC 回了这一句），输入框清空、上方那一行收起", "seen": sent,
         "ok": bool(sent.get("clicked")) and bool(sent.get("bubble")) and bool(sent.get("replied"))
         and (sent.get("after") or {}).get("text") == "" and (sent.get("after") or {}).get("alert") == ""
         and (sent.get("after") or {}).get("cards") == 0},
        {"case": "提问卡浮着时打字点发送 = 作答当前这题：当场交卡、不排队，对话里不另出这句的气泡，卡片摘要里是这句",
         "seen": {k: asked.get(k) for k in ("asked", "card", "clicked", "queued", "answered", "bubble", "answer", "before")},
         "ok": bool(asked.get("card")) and bool(asked.get("clicked")) and asked.get("queued") == 0 and bool(asked.get("answered"))
         and not asked.get("bubble")},
        {"case": "CC 收到的是作答（ask_user 的结果里带这句），只回这一次；卡片不再能点、这一轮结束",
         "seen": {"replies": [r[:200] for r in replies(asked)], **{k: asked.get(k) for k in ("replied", "settled", "idle")}},
         "ok": bool(asked.get("replied")) and bool(asked.get("settled")) and bool(asked.get("idle")) and len(replies(asked)) == 1},
        {"case": "允许卡挂着时打字点发送：当场发出、不排队，气泡出现", "seen": {k: gated.get(k) for k in ("asked", "card", "clicked", "queued", "bubble", "before")},
         "ok": bool(gated.get("card")) and bool(gated.get("clicked")) and gated.get("queued") == 0 and bool(gated.get("bubble"))},
        {"case": "那张允许卡作废、工具没执行，CC 只回这一句（回复里没有 saved）；卡片上没有按钮了、这一轮结束",
         "seen": {"replies": [r[:200] for r in replies(gated)], **{k: gated.get(k) for k in ("replied", "settled", "idle", "answer")}},
         "ok": bool(gated.get("replied")) and bool(gated.get("settled")) and bool(gated.get("idle")) and len(replies(gated)) == 1
         and not any('"saved"' in r for r in replies(gated))},
        {"case": "一时传不上的附件留失败卡：卡上有文件名与原因；带着它点发送整条不发，字放回输入框、上方写原因", "seen": offline,
         "ok": bool(offline.get("failed")) and (offline.get("card") or {}).get("name") == "offline.txt"
         and bool((offline.get("card") or {}).get("reason")) and bool(offline.get("clicked")) and bool(offline.get("back"))
         and not (offline.get("held") or {}).get("users")},
        {"case": "点失败卡上的 × 移除后再点发送：发得出去、CC 回了这一句", "seen": cleared,
         "ok": bool(cleared.get("removed")) and bool(cleared.get("clicked")) and bool(cleared.get("bubble")) and bool(cleared.get("replied"))},
        {"case": "删掉这几段会话，没有多出空标题的会话", "seen": {**removed, "blank_before": picked.get("blank")}, "ok": len(removed.get("ids") or []) >= 4 and removed.get("left") == picked.get("blank")},
    ]


def verify(lab, manifest, params, camera_code=None):
    tool = NOTE_TOOL[params["product"]]
    out = lab.python(manifest["site"], f"""
rows = frappe.db.sql("select tool, status, result from `tabPearnly Assistant Tool Run` where creation >= %s and tool in ('ask_user', %s) "
                     "and (tool_input like %s or tool = 'ask_user')", ({params['start']!r}, {tool!r}, "%{params['tag']}%"), as_dict=True)
note = frappe.db.sql("select count(*) from `tabPearnly Assistant Tool Run` where creation >= %s and tool = %s and status in ('running', 'done', 'error') "
                     "and tool_input like %s", ({params['start']!r}, {tool!r}, "%{params['tag']}%"))[0][0]
print(json.dumps({{"runs": [[r.tool, r.status] for r in rows], "executed": int(note)}}))
""")
    runs = out.get("runs") or []
    return [
        {"case": "执行记录：提问卡是答完的（done，打字即作答），写备注那次是 cancelled（作废），没有执行过", "seen": out,
         "ok": ["ask_user", "done"] in runs and [tool, "cancelled"] in runs and out.get("executed") == 0},
    ]
