"""CC 引导式 P3（Cowork 读回、对账、增值税、收银日汇总、预扣税）流程用例的共用片段（11-16，**等接线**：登记在
`tests/flows/waiting.toml`，分支 3 接线与 A2 前端的新输入进 main 后搬进 `registry.toml` 再跑；之前没在实验栈跑过）。

造数据沿用 `cc_conversation_ledger.prepare / cleanup`（一条 Express 连接、同一税号两本同期账套 T.8 / T.9、从目录建的公司、录入人一台
登记过的电脑）；对话用 `assistant.api.create(project, period, ledger)` 直接建在这家、账套 = T.8（读回 / 增值税 / 日汇总要本对话账套是 Express），
再打开 `/assistant?c=<会话>`。假智能体只用来不让页面空着，引导卡不经模型。

页面标记（与引导式窗口分支 3 的引导卡、guided-frontend 的新输入约定，见共享笔记「引导式-引擎通用能力N1-N9-约定-11-16.md」）：
  [data-slot=cc-business-menu] 按钮              业务菜单（按钮文字 = 菜单字）
  [data-slot=cc-guide-run][data-status][data-business]   这次引导的卡
  [data-slot=cc-guide-question][data-key]        一题；选项是 label 里的按钮（同收款流程），月份 / 数字 / 文字是 [data-slot=cc-guide-value]
  [data-slot=cc-guide-file] [data-file-name]     交文件那一题：对话里的附件一行一个，点了选中
  [data-slot=cc-guide-subquestion][data-key]     子题（认列题、结果里的追问），选项同上
  [data-slot=cc-guide-review]、[data-slot=cc-guide-total]   复核卡与合计
  [data-slot=cc-guide-result][data-result]       结果；[data-slot=cc-guide-result-file] 结果里的文件
  卡上按钮文字：「提交」「确定」「写入」（确定那一下）、允许卡「允许」
"""
from __future__ import annotations

import json

from cc_lib import JS as BASE_JS

USER = "admin@cowork.regression"
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"

JS = BASE_JS + (
    "const card=()=>" + CARD + "; "
    "const keys=()=>[...(card()?.querySelectorAll('[data-slot=\"cc-guide-question\"]')||[])].map(x=>x.dataset.key); "
    "const qk=k=>card()?.querySelector(`[data-slot=\"cc-guide-question\"][data-key=\"${k}\"]`); "
    "const setVal=(el,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value'); d.set.call(el,v); "
    "el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));}; "
    # 一题点一个选项（按文字含 want；没给点第一个）
    "const pick=async(box,want)=>{const items=[...(box?.querySelectorAll('label')||[])]; "
    "const hit=want?items.find(l=>l.innerText.includes(want)):items[0]; hit?.querySelector('button')?.click(); await sleep(200); return !!hit;}; "
    # 交文件：在这一题里点对话附件那一行
    "const files=async(k,names)=>{const box=qk(k)?.querySelector('[data-slot=\"cc-guide-file\"]'); if(!box) return false; "
    "for(const n of names){const row=[...box.querySelectorAll('[data-file-name]')].find(r=>r.dataset.fileName===n); row?.click(); await sleep(150);} return true;}; "
    # 子题：{子题 key: 选项文字}
    "const subs=async(root,answers)=>{const out={}; for(const [k,want] of Object.entries(answers)){"
    "const box=root?.querySelector(`[data-slot=\"cc-guide-subquestion\"][data-key=\"${k}\"]`); out[k]=await pick(box,want);} return out;}; "
    # 点卡上一个按钮（按文字），等卡换样子
    "const tap=async label=>{const before=card()?.innerText||''; const b=[...(card()?.querySelectorAll('button')||[])].find(x=>x.innerText.trim()===label); "
    "if(!b||b.disabled) return false; b.click(); await until(()=>card()&&card().innerText!==before,120); await sleep(800); return true;}; "
    # 打开业务菜单上的一项，等卡出来
    "const openBusiness=async label=>{const row=()=>q('[data-slot=\"cc-business-menu\"]'); await until(()=>row(),120); "
    "[...row().querySelectorAll('button')].find(b=>b.innerText.trim()===label)?.click(); await until(()=>card(),120); "
    "return {status: card()?.dataset.status||'', business: card()?.dataset.business||'', keys: keys()};}; "
    # 上传一份附件进对话（同 cc_attach_delete：替身文件选择框）
    "const attach=async(name,content,type)=>{if(!window.__fileHook){window.__fileHook=1; const orig=HTMLInputElement.prototype.click; "
    "HTMLInputElement.prototype.click=function(){ if(this.type==='file'&&window.__nextFile){const dt=new DataTransfer(); dt.items.add(window.__nextFile); "
    "window.__nextFile=null; this.files=dt.files; this.dispatchEvent(new Event('change',{bubbles:true})); return;} return orig.apply(this,arguments);};} "
    "window.__nextFile=new File([content], name, {type}); const add=qa('button.cc-attach').find(seen); if(!add) return false; add.click(); "
    "await until(()=>document.body.innerText.includes(name),80); await until(()=>{const b=q('button.aui-composer-send'); return b&&!b.disabled;},120); return true;}; "
    "const result=()=>card()?.querySelector('[data-slot=\"cc-guide-result\"]'); "
    "const resultFiles=()=>[...(card()?.querySelectorAll('[data-slot=\"cc-guide-result-file\"]')||[])].map(x=>x.innerText.trim()); "
)


def prepare(lab, manifest):
    """造数据（同 `cc_conversation_ledger`）并回参数：公司、T.8 的账套编号、本流程标签。"""
    import cc_conversation_ledger as ledger

    return ledger.prepare(lab, manifest)


def cleanup(lab, manifest, params):
    import cc_conversation_ledger as ledger

    ledger.cleanup(lab, manifest, params)


def open_conversation(params, period="2026-10"):
    """在这家、这个月、账套 T.8 建一段对话并打开（页面跳转）。"""
    args = json.dumps({"project": params["company"], "period": period, "ledger": params["t8"]})
    return {"name": "建对话（这家、T.8）", "wait_ms": 6000, "js": JS +
            f"const sid=await call('pearnly_core.assistant.api.create',{args},true); "
            "const id=(sid&&sid.session_id)||sid; setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent(id)),0); return {id};"}


def say_hello(params):
    return {"name": "说一句（会话有标题）", "js": JS + f"const sent=await say({json.dumps(params['tag'] + ' 你好')}); await turnDone(); return {{sent}};"}


def delete_conversation(params):
    return {"name": "删除会话", "js": JS + f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({json.dumps(params['tag'])})); "
            "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"}


def by_step(results) -> dict:
    return {r.get("step"): r.get("value") or {} for r in results}
