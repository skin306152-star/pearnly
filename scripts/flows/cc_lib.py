"""CC 对话页流程用例的共用片段（统一对话框架，方案 cc-unified-plan 14，事件清单以评估 14.10 为准）：代录任务就是一段标准对话会话，
直写与代录共用同一个对话页、新建页（公司 / 月份胶囊）、侧栏；代录的状态由服务端代录事件源翻成统一对话事件，卡片与步骤在
`frontend/assistant/src/components/tool-uis/operator-*`。

`JS` 是交给页面的一段帮手（每一步 `js` 的开头拼上）：
  until / sleep / q / qa            等条件、取元素
  mode()                            左上角「对话 | 代录」此刻是哪种（chat / operator）
  toMode(m)                         切到那种模式（看得见的那个切换钮）
  press(e)                          Radix 下拉按下就开，单发 click 不开
  say(t)                            在当前对话的输入框里写一句并发出（同 cc_chat.py）
  pickCompany(title)                新建页下巴点「公司」，在选择公司弹窗里选这家（名称完全相同；月份随之默认当月）
  call(m, args, post)               调站点接口（完整点分名），回 message；最多等 60 秒，超时抛「request <接口> timed out」
  listed(m)                         侧栏同一个接口 `assistant.api.listing` 按模式列的会话（第一页）
  ownChats(tag, m)                  这一格建的会话（标题含 tag，等异步生成的标题出现，最多 30 秒）；删会话一律用它
  openSteps(slot)                   展开对话里收起的步骤组，直到出现 data-slot = slot 的那一步
  rows()                            侧栏对话行（标题文字）
  turnDone(n)                       等这一轮结束再读回复：停止键消失、连续 1 秒没再出现（最多 n×250 毫秒，默认 60 秒），回 true / false。
                                    从页面读回复文字一律先等它（11-14：这一轮还在跑时读到的可能是还没画完的半截；实验室没有实时通道，
                                    「这一轮结束」靠补拉带回）
  toolResult(name)                  假智能体回的「[lab-agent] <工具名> <工具结果原文>」里那段结果，**整段 JSON 已完整画出来**时回对象，
                                    否则回 null（回复是流式画出来的）。读工具结果一律 `await until(()=>toolResult(名))` 再取字段：
                                    只等某个字段名出现就读（11-11 / 11-12 两次偶发：`"status"` 先出现在嵌套的跳过清单里，顶层还没画完）
                                    或按正则取第一处同名字段（会取到嵌套的那个）都不行，`tests/test_flows_wait_values.py` 拦
"""
from __future__ import annotations

JS = (
    "const until=async(f,n=120)=>{for(let k=0;k<n;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
    "const sleep=ms=>new Promise(r=>setTimeout(r,ms)); const q=s=>document.querySelector(s); const qa=s=>[...document.querySelectorAll(s)];"
    "const seen=e=>!!(e&&e.getClientRects().length);"
    "const mode=()=>q('[data-slot=\"cc-mode-switch\"] [aria-checked=\"true\"]')?.dataset.mode;"
    "const toMode=async m=>{if(mode()===m) return true; const b=qa(`[data-slot=\"cc-mode-switch\"] [data-mode=\"${m}\"]`).find(seen)"
    "||q(`[data-slot=\"cc-mode-switch\"] [data-mode=\"${m}\"]`); if(!b) return false; b.click(); return await until(()=>mode()===m,40);};"
    "const press=e=>{e.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'})); e.click();};"
    # 输入框可用：链接 / 通知打开某段对话时，切到那里之前输入框和「+」不可用（lib/opening.ts 的闸，11-17），人点不了，流程也要等
    "const composerReady=()=>{const ta=q('textarea.aui-composer-input'); const add=qa('button.cc-attach').find(seen); return !!ta&&!ta.disabled&&!!add&&!add.disabled;};"
    # 让 CC 附件上传一时传不上（只这一个接口回 503，同改动前的造法）：页面上传走 XMLHttpRequest（要真进度，B32），没有 XHR 时走
    # fetch，两条都拦；回一个恢复函数。XHR 不发出去，直接以 503 回（lib/frappe.ts 的 postWithProgress 照常交给 parse，成 RequestError）
    "const blockUploads=()=>{const hit=u=>String(u&&u.url||u||'').includes('assistant.api.upload'); const realFetch=window.fetch; "
    "const P=XMLHttpRequest.prototype, realOpen=P.open, realSend=P.send; "
    "window.fetch=(u,o)=>hit(u)?Promise.resolve(new Response('',{status:503})):realFetch(u,o); "
    "P.open=function(m,u,...r){this.__ccUrl=String(u); return realOpen.call(this,m,u,...r);}; "
    "P.send=function(b){if(!hit(this.__ccUrl)) return realSend.call(this,b); const x=this; "
    "for(const [k,v] of [['status',503],['statusText','Service Unavailable'],['responseText',''],['readyState',4]]) Object.defineProperty(x,k,{value:v,configurable:true}); "
    "setTimeout(()=>{x.onload&&x.onload(new ProgressEvent('load'));},50);}; "
    "return ()=>{window.fetch=realFetch; P.open=realOpen; P.send=realSend;};};"
    "const say=async t=>{const ta=q('textarea.aui-composer-input'); "
    "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,t); ta.dispatchEvent(new Event('input',{bubbles:true})); "
    "await sleep(300); const b=q('button.aui-composer-send'); if(!b||b.disabled) return false; b.click(); return true;};"
    "const pickCompany=async title=>{const chin=q('[data-slot=\"cc-chin-company\"]'); if(!chin) return false; chin.click(); "
    # 点「公司」弹选择公司窗口（11-07 起不再是下拉）；列表是服务端分页搜索（每页 50），不在第一页的就在顶上的搜索框里搜
    "const box=()=>q('[data-slot=\"cc-company-picker\"]'); if(!await until(box,40)) return false; "
    "const row=()=>[...box().querySelectorAll('[data-slot=\"cc-candidates\"] .cc-list-row')].find(e=>e.innerText.split('\\n')[0].includes(title)); "
    "if(!await until(row,20)){const input=box().querySelector('input'); "
    "if(input){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,title); input.dispatchEvent(new Event('input',{bubbles:true}));} "
    # 搜不到时可能在另一个标签（MR.ERP / Express / ERP）下；组件库的标签按下（mousedown）就切
    "if(!await until(row,40)){for(const tab of [...box().querySelectorAll('[role=\"tab\"]')]){tab.dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0})); if(await until(row,20)) break;}}} "
    "const item=row(); if(!item) return false; item.click(); "
    "return await until(()=>!box()&&(q('[data-slot=\"cc-chin-company\"]')?.innerText||'').includes(title),80);};"
    # 每个请求最多等 60 秒，超时就带着接口名抛错（11-13：一格的请求一直没回，整格挂满 900 秒、看不出卡在哪个请求）
    "const call=async(m,args,post)=>{const body=new URLSearchParams(args||{}); const stop=new AbortController(); "
    "const timer=setTimeout(()=>stop.abort(),60000); try{ const r=await fetch('/api/method/'+m+(post?'':'?'+body),"
    "{method:post?'POST':'GET',headers:{'X-Frappe-CSRF-Token':window.csrf_token||'','Content-Type':'application/x-www-form-urlencoded'},"
    "body:post?body:undefined, signal:stop.signal}); return (await r.json()).message; }"
    "catch(e){ if(e&&e.name==='AbortError') throw new Error('request '+m+' timed out after 60s'); throw e; } finally{ clearTimeout(timer); }};"
    "const listed=async m=>((await call('pearnly_core.assistant.api.listing',{mode:m}))||{}).sessions||[];"
    # 这一格建的会话（标题含本格的 tag）：标题是首句异步生成的，等到出现为止（最多 30 秒）；手机格走得快时只看一次会落空（11-23 Flows 偶发 ids=[]）
    "const ownChats=async(tag,m)=>{for(let i=0;i<120;i++){const found=(await listed(m||'chat')).filter(s=>(s.title||'').includes(tag)); if(found.length) return found; await sleep(250);} return [];};"
    "const openSteps=async slot=>{for(let k=0;k<20&&!q(`[data-slot=\"${slot}\"]`);k++){"
    "const b=qa('.cc-steps-summary[aria-expanded=\"false\"]')[0]; if(!b) {await sleep(250); continue;} b.click(); await sleep(150);} "
    "return !!q(`[data-slot=\"${slot}\"]`);};"
    "const rows=()=>qa('[data-slot=\"aui_thread-list-item\"]').map(e=>e.innerText.trim().split('\\n')[0]);"
    "const turnDone=async(n=240)=>{let quiet=0; for(let k=0;k<n;k++){ if(q('button.aui-composer-cancel')) quiet=0; else if(++quiet>=4) return true; "
    "await new Promise(r=>setTimeout(r,250)); } return false;};"
)

# 工具结果整段 JSON（见文件头 toolResult）：从「<工具名> {」起按括号配对（跳过字符串里的括号）取到完整的一段再解析，取最新的那条回复
TOOL_RESULT = (
    "const toolResult=name=>{const mark=name+' {'; for(const e of qa('[data-role=\"assistant\"]').reverse()){const t=e.innerText; "
    "const i=t.indexOf(mark); if(i<0) continue; const s=t.slice(i+name.length+1); let d=0, str=false, esc=false; "
    "for(let k=0;k<s.length;k++){const c=s[k]; if(str){ if(esc) esc=false; else if(c==='\\\\') esc=true; else if(c==='\"') str=false; continue; } "
    "if(c==='\"') str=true; else if(c==='{') d++; else if(c==='}'){ d--; if(d===0){ try{ return JSON.parse(s.slice(0,k+1)); }catch(x){ return null; } } } } "
    "return null; } return null;};"
)
JS += TOOL_RESULT
