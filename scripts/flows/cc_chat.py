"""流程用例：CC 对话页 `/assistant`——新建对话、两轮收发、刷新后从侧栏找回、搜索筛出、落库。

模型调用这一层换成实验室的假智能体服务（`scripts/flows/lab_agents.py`，编排把 bench 的 ANTHROPIC_BASE_URL 指过去）：
站点接口、会话记录、转发线程、真实的 Managed Agents 适配器照真的跑；假服务的回复是「[lab-agent] + 用户原文前 40 个字」，
所以断言的是消息往返、会话落库、列表与搜索，不是模型回答的内容。不调付费模型。
"""
from __future__ import annotations

import json
import uuid

TITLE = "CC 对话页：新建对话、两轮收发、刷新找回、搜索、落库（假智能体后端）"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
REPLY = "[lab-agent]"

WAIT = ("const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
        "const roles=r=>[...document.querySelectorAll(`[data-role=\"${r}\"]`)].map(e=>e.innerText.trim());"
        "const say=async t=>{const ta=document.querySelector('textarea[aria-label]'); "
        "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,t); ta.dispatchEvent(new Event('input',{bubbles:true})); "
        "await new Promise(r=>setTimeout(r,300)); document.querySelector('button[aria-label=\"发送\"]').click();};"
        # 手机宽度：侧栏是抽屉（打开它的按钮叫「菜单」；电脑上收起的侧栏用「切换侧栏」），看不见就先点开
        "const items=()=>document.querySelector('[data-slot=\"aui_thread-list-items\"]');"
        "const shown=e=>!!(e&&e.getClientRects().length&&e.offsetParent);"
        "const openSide=async()=>{ const box=document.querySelector('input[aria-label=\"搜索\"]'); if(shown(items())&&shown(box)) return; "
        "(document.querySelector('button[aria-label=\"菜单\"]')||document.querySelector('button[aria-label=\"切换侧栏\"]'))?.click(); "
        "await until(()=>shown(items()),20);};")


def prepare(lab, manifest):
    # 判定按中文界面写（按钮、提示）：上一轮 cc.settings 中途被停时管理员语言可能还是英文，先还原
    lab.python(manifest["site"], 'frappe.db.set_value("User", "admin@erp.regression", "language", "zh")\nfrappe.db.commit()\nprint("{}")')
    tag = "flow" + uuid.uuid4().hex[:6]
    before = lab.python(manifest["site"], 'print(json.dumps({"n": frappe.db.count("Pearnly Assistant Session", {"user": "admin@erp.regression"})}))')
    return {"tag": tag, "first": f"{tag} 盘点一下三号仓", "second": f"{tag} second turn", "sessions_before": before["n"], "cases": []}


def steps(params, camera_code=None):
    first, second, tag = params["first"], params["second"], params["tag"]
    want = lambda text: f"roles('assistant').some(t=>t.includes({json.dumps(REPLY + ' ' + text)}))"
    return [
        {"name": "第一轮", "js": WAIT + f"await say({json.dumps(first)}); const got=await until(()=>{want(first)}); "
         "return {got, user: roles('user'), assistant: roles('assistant')};"},
        {"name": "第二轮", "js": WAIT + f"await until(()=>!document.querySelector('button[aria-label=\"停止\"]'),40); await say({json.dumps(second)}); "
         f"const got=await until(()=>{want(second)}); return {{got, user: roles('user'), assistant: roles('assistant')}};"},
        {"name": "刷新", "js": "location.reload(); return 1;", "wait_ms": 4000},
        {"name": "从侧栏找回", "js": WAIT + "await until(()=>items()); await openSide(); "
         f"await until(()=>items().innerText.includes({json.dumps(tag)}),60); "
         f"const row=[...items().querySelectorAll('a, button, [role]')].find(e=>e.innerText.includes({json.dumps(tag)})); "
         "if(!row) return {error:'conversation not listed', list: items().innerText.slice(0,300)}; row.click(); "
         f"const got=await until(()=>{want(second)},80); return {{got, user: roles('user'), assistant: roles('assistant')}};"},
        {"name": "搜索", "js": WAIT + "const sbox=()=>[...document.querySelectorAll('input[aria-label=\"搜索\"]')].find(shown); "
         "if(!sbox()){ await openSide(); } if(!sbox()){ document.querySelector('button[aria-label=\"搜索\"]')?.click(); await until(()=>sbox(),20); } "
         "const box=sbox(); if(!box) return {error:'no search'}; "
         f"Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(box,{json.dumps(tag)}); box.dispatchEvent(new Event('input',{{bubbles:true}})); "
         "await new Promise(r=>setTimeout(r,1500)); const text=document.body.innerText; "
         # 筛掉了别的同类对话（之前几轮留下的、标题同样以「盘点一下三号仓」结尾）才算搜索起作用
         f"const others=text.split('\\n').filter(l=>l.includes('盘点一下三号仓')&&!l.includes({json.dumps(tag)})); "
         f"return {{hit: text.includes({json.dumps(tag)}), others, text: text.slice(0,200)}};"},
        # 批 A ①（11-09 主会话定，照 Claude 实测）：回复超出一屏时电脑跟到底、手机停在这一轮开头（提问顶在上面）。
        # 放在最后：长回复把前面的消息挤到屏外，屏外消息按需渲染、读不到文字，会搅乱前面几步的断言
        {"name": "长回复", "js": WAIT + f"await until(()=>!document.querySelector('button[aria-label=\"停止\"]'),40); "
         f"await say({json.dumps(tag + ' [lab-long]60[/lab-long]')}); "
         "const got=await until(()=>roles('assistant').some(t=>t.includes('[lab-long-end]')),120); await new Promise(r=>setTimeout(r,1500)); "
         "const v=document.querySelector('[data-slot=\"aui_thread-viewport\"]'); if(!v) return {got, error:'no viewport'}; "
         "const users=[...document.querySelectorAll('[data-role=\"user\"]')]; const ask=users[users.length-1]; "
         "const top=ask?Math.round(ask.getBoundingClientRect().top-v.getBoundingClientRect().top):null; "
         "return {got, gap: Math.round(v.scrollHeight-v.scrollTop-v.clientHeight), overflow: v.scrollHeight-v.clientHeight, ask_top: top, "
         "phone: window.innerWidth<768};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, second = params["first"], params["second"]
    one, two, back, found, long = (by.get(k) or {} for k in ("第一轮", "第二轮", "从侧栏找回", "搜索", "长回复"))
    return [
        {"case": "第一轮收到回复", "seen": one, "ok": bool(one.get("got")) and first in (one.get("user") or [])},
        # F13（10-15 夜间批遗留）：页面刚加载（输入框一出现）就发的第一条不能丢——第一步就是就绪即发，刷新后还要在
        {"case": "F13：页面刚加载就发的第一条不丢", "seen": (back.get("user") or [])[:1],
         "ok": bool(one.get("got")) and first in (back.get("user") or [])},
        {"case": "第二轮收到回复（同一段对话）", "seen": two, "ok": bool(two.get("got")) and len(two.get("assistant") or []) >= 2},
        {"case": "刷新后从侧栏找回、两轮都在", "seen": back,
         "ok": bool(back.get("got")) and first in (back.get("user") or []) and second in (back.get("user") or [])},
        {"case": "搜索按标题筛出、别的对话筛掉", "seen": found, "ok": bool(found.get("hit")) and not found.get("others")},
        # 长回复超出一屏（overflow 远大于一屏）：电脑停在最底下（gap 近 0）；手机不跟，停在这一轮开头（提问在视口上沿附近、下面还有很多）
        {"case": "长回复：电脑跟到底、手机停在这一轮开头", "seen": long,
         "ok": bool(long.get("got")) and (long.get("overflow") or 0) > 600 and (
             (not long.get("phone") and (long.get("gap") if long.get("gap") is not None else 999) <= 8)
             or (bool(long.get("phone")) and (long.get("gap") or 0) > 300 and (long.get("ask_top") if long.get("ask_top") is not None else 999) < 200))},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
rows = frappe.get_all("Pearnly Assistant Session", filters={{"user": "admin@erp.regression", "title": ["like", "%{params['tag']}%"]}},
                      fields=["session_id", "title", "product", "engine_code", "archived"], limit_page_length=5)
print(json.dumps({{"rows": rows, "n": frappe.db.count("Pearnly Assistant Session", {{"user": "admin@erp.regression"}})}}))
""")
    found = rows["rows"]
    return [
        {"case": "落库一段对话（标题、产品、引擎）", "saved": found,
         "ok": len(found) == 1 and found[0]["product"] == "erp" and found[0]["engine_code"] == "anthropic-managed-agents"
         and not found[0]["archived"] and found[0]["session_id"].startswith("sesn_")},
        {"case": "只多出这一段对话", "saved": {"before": params["sessions_before"], "after": rows["n"]},
         "ok": rows["n"] == params["sessions_before"] + 1},
    ]
