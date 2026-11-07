"""CC 对话页流程用例的共用片段（统一对话框架，方案 cc-unified-plan 14，事件清单以评估 14.10 为准）：代录任务就是一段标准对话会话，
直写与代录共用同一个对话页、新建页（公司 / 月份胶囊）、侧栏；代录的状态由服务端代录事件源翻成统一对话事件，卡片与步骤在
`frontend/assistant/src/components/tool-uis/operator-*`。

`JS` 是交给页面的一段帮手（每一步 `js` 的开头拼上）：
  until / sleep / q / qa            等条件、取元素
  mode()                            左上角「对话 | 代录」此刻是哪种（chat / operator）
  toMode(m)                         切到那种模式（看得见的那个切换钮）
  press(e)                          Radix 下拉按下就开，单发 click 不开
  say(t)                            在当前对话的输入框里写一句并发出（同 cc_chat.py）
  pickCompany(title)                新建页下巴点「公司」，在选择公司弹窗里选这家（名称完全相同；月份随之默认上个月）
  call(m, args, post)               调站点接口（完整点分名），回 message
  listed(m)                         侧栏同一个接口 `assistant.api.listing` 按模式列的会话（第一页）
  openSteps(slot)                   展开对话里收起的步骤组，直到出现 data-slot = slot 的那一步
  rows()                            侧栏对话行（标题文字）
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
    "const say=async t=>{const ta=q('textarea.aui-composer-input'); "
    "Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,t); ta.dispatchEvent(new Event('input',{bubbles:true})); "
    "await sleep(300); const b=q('button.aui-composer-send'); if(!b||b.disabled) return false; b.click(); return true;};"
    "const pickCompany=async title=>{const chin=q('[data-slot=\"cc-chin-company\"]'); if(!chin) return false; chin.click(); "
    # 点「公司」弹选择公司窗口（11-07 起不再是下拉）；列表是服务端分页搜索（每页 50），不在第一页的就在顶上的搜索框里搜
    "const box=()=>q('[data-slot=\"cc-company-picker\"]'); if(!await until(box,40)) return false; "
    "const row=()=>[...box().querySelectorAll('[data-slot=\"cc-candidates\"] .cc-list-row')].find(e=>e.innerText.split('\\n')[0].includes(title)); "
    "if(!await until(row,20)){const input=box().querySelector('input'); "
    "if(input){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,title); input.dispatchEvent(new Event('input',{bubbles:true}));} "
    # 搜不到时可能在另一个标签（MR.ERP / Express）下
    "if(!await until(row,40)){for(const tab of [...box().querySelectorAll('[role=\"tab\"]')]){tab.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,button:0,pointerType:'mouse'})); tab.click(); if(await until(row,20)) break;}}} "
    "const item=row(); if(!item) return false; item.click(); "
    "return await until(()=>!box()&&(q('[data-slot=\"cc-chin-company\"]')?.innerText||'').includes(title),80);};"
    "const call=async(m,args,post)=>{const body=new URLSearchParams(args||{}); const r=await fetch('/api/method/'+m+(post?'':'?'+body),"
    "{method:post?'POST':'GET',headers:{'X-Frappe-CSRF-Token':window.csrf_token||'','Content-Type':'application/x-www-form-urlencoded'},"
    "body:post?body:undefined}); return (await r.json()).message;};"
    "const listed=async m=>((await call('pearnly_core.assistant.api.listing',{mode:m}))||{}).sessions||[];"
    "const openSteps=async slot=>{for(let k=0;k<20&&!q(`[data-slot=\"${slot}\"]`);k++){"
    "const b=qa('.cc-steps-summary[aria-expanded=\"false\"]')[0]; if(!b) {await sleep(250); continue;} b.click(); await sleep(150);} "
    "return !!q(`[data-slot=\"${slot}\"]`);};"
    "const rows=()=>qa('[data-slot=\"aui_thread-list-item\"]').map(e=>e.innerText.trim().split('\\n')[0]);"
)
