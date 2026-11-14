"""CC 对话里「允许 / 拒绝」卡片流程（`cc_approval_cards.py`、`erp_cc_approval_withdraw.py`）共用的页面帮手（不是流程，不登记）。

假智能体（`lab_agents.py`）认 ``[lab-tool]{"name", "input"}[/lab-tool]``，照真实引擎调这个工具；工具声明了 ``approval``，站点登记成
等「允许」，对话里出卡片：电脑在对话流里（`.cc-ask[data-phase="ask"]`，底部「拒绝」「允许」），手机浮在输入框上方
（`[data-slot="cc-ask-sheet"]`，右上「×」= 拒绝、右下「允许」）。点完由站点执行或不执行，结果交回假智能体，它回
「[lab-agent] <工具名> <结果原文>」。

`JS`（拼在每一步开头，已含 `cc_lib.JS`）：
  phone()            此刻是不是手机宽度（卡片浮在输入框上方）
  card()             等回答的那张卡片（手机浮层 / 电脑对话流里最后一张等回答的）
  cardInfo()         卡片上的问句、改动各行、有没有横向溢出
  replies(name)      对话里假智能体回这个工具结果的条数（再调一次同一个工具时据此等「新的那条」）
  answer(ok, name)   点「允许」（ok 为真）或「拒绝」，等假智能体回了这一次的结果，回那段结果对象
  ask(text, name)    说一句（含 [lab-tool]），等卡片出现，回 cardInfo()
  setLang(user, l)   把这个账号的界面语言改成 l，再重新载入页面（步骤带 wait_ms 等载入完；下一步按新语言）
"""
from __future__ import annotations

import json

from cc_lib import JS as BASE_JS

JS = BASE_JS + (
    "const phone=()=>window.innerWidth<768;"
    "const card=()=>phone()?q('[data-slot=\"cc-ask-sheet\"]'):qa('.cc-ask[data-phase=\"ask\"]').pop();"
    "const cardInfo=()=>{const c=card(); if(!c) return null; const q1=c.querySelector('.cc-ask-sheet-q, .cc-ask-q'); "
    "const rows=[...c.querySelectorAll('.cc-ask-changes li')]; "
    "return {question: q1?.innerText.trim()||'', rows: rows.map(li=>li.innerText.trim().replace(/\\s+/g,' ')), "
    "overflow: c.scrollWidth>c.clientWidth+1 || rows.some(li=>li.scrollWidth>li.clientWidth+1), "
    "buttons: [...c.querySelectorAll('button')].map(b=>(b.innerText||b.getAttribute('aria-label')||'').trim())};};"
    "const replies=name=>qa('[data-role=\"assistant\"]').filter(e=>e.innerText.includes('[lab-agent] '+name+' {')).length;"
    "const answer=async(ok,name)=>{const c=card(); if(!c) return {error:'no card'}; const n=replies(name); "
    "const b=ok?c.querySelector('.cc-card-btn[data-primary]'):(phone()?c.querySelector('.cc-card-btn[data-icon]')"
    ":c.querySelector('.cc-card-actions .cc-card-btn:not([data-primary])')); if(!b) return {error:'no button'}; b.click(); "
    # 结果整段 JSON 解析不了时（回复按 Markdown 画，账套路径里的反斜杠会少一个）退回读顶层 status（结果 JSON 第一个字段就是它）
    "const latest=()=>{const e=qa('[data-role=\"assistant\"]').filter(e=>e.innerText.includes('[lab-agent] '+name+' {')).pop(); "
    "const m=e&&e.innerText.slice(e.innerText.indexOf('[lab-agent] '+name+' {')).match(/^[^{]*\\{\"(\\w+)\": (\\d+|\"(\\w+)\")/); "
    "return m?{[m[1]]: m[3]!==undefined?m[3]:Number(m[2]), partial:true}:null;}; "
    "const back=await until(()=>replies(name)>n,240); return back?(toolResult(name)||latest()||{error:'no tool result'}):{error:'no reply'};};"
    "const ask=async(text,name)=>{const n=qa('.cc-ask').length; const sent=await say(text); "
    "const shown=await until(()=>card()&&(phone()||qa('.cc-ask').length>n)&&cardInfo().rows.length>0,240); await sleep(500); "
    "return {sent, shown, ...(cardInfo()||{})};};"
    "const setLang=async(user,l)=>{const r=await call('frappe.client.set_value',{doctype:'User',name:user,fieldname:'language',value:l},true); "
    "setTimeout(()=>location.reload(),200); return {lang:l, saved:(r||{}).language||''};};"
)


def tool(tag: str, name: str, data: dict) -> str:
    """让假智能体调这个工具的一句话（带本次标签，侧栏按它找这段对话）。"""
    return f"{tag} [lab-tool]{json.dumps({'name': name, 'input': data}, ensure_ascii=False)}[/lab-tool]"


def set_language(lab, site: str, user: str, lang: str) -> None:
    lab.python(site, f'frappe.db.set_value("User", {user!r}, "language", {lang!r})\nfrappe.db.commit()\nprint("{{}}")')
