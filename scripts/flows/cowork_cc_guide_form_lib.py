"""Cowork 版 CC 表单引导（增值税、POS 日汇总、预扣税）真页面流程的共用片段：造数据同 `cc_conversation_ledger`（一条 Express 连接、
同一税号两本同期账套 T.8 / T.9、从目录建的公司、录入人一台登记过的电脑），直写对话建在这家、本月、账套 T.8。

页面上全靠点（不调引导接口、不经模型）：
- ``open_business``：业务菜单上点这一项（直接摆出来的点按钮；手机宽度收进「更多」的先按开「更多」再点菜单项）；
- ``ANSWER``：一页一题地答（问卷一页一题）：``want`` 里给了的照填 / 照选（月份、数字、文字填值；单选按码点；交文件按文件名点），
  没给的：可跳过的点「跳过」，有推荐的照推荐，其余点第一项；点「下一步 / 提交」直到卡离开问题页；
- ``confirm``：确定卡（没有复核的表单）或复核卡上点 ``cc-guide-confirm``，等结果卡；
- 结果卡读 ``data-result``、文件行、原因行与 ``data-mark``。
"""
from __future__ import annotations

import json
import uuid

import cc_conversation_ledger as ledger
from cc_lib import JS

USER = ledger.USER
MONTH = ledger.CHAT_MONTH
CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"

HELP = (
    "const card=()=>" + CARD + "; "
    "const setVal=(el,v)=>{const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value'); d.set.call(el,v); "
    "el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true}));}; "
    "const result=()=>card()?.querySelector('[data-slot=\"cc-guide-result\"]'); "
    "const readResult=()=>{const r=result(); return {status: card()?.dataset.status||'', result: r?.dataset.result||'', text: r?.innerText||'', "
    "files: [...(r?.querySelectorAll('[data-slot=\"cc-guide-result-file\"]')||[])].map(x=>x.dataset.fileName), "
    "marks: [...(r?.querySelectorAll('[data-mark]')||[])].map(x=>({code: x.dataset.mark, text: x.innerText.trim()})), "
    "rows: [...(r?.querySelectorAll('[data-doc]')||[])].map(x=>({doc: x.dataset.doc, status: x.dataset.status||'', text: x.innerText.trim()}))};}; "
)


def seed() -> str:
    return ledger._mine(ledger.CLEANUP) + ledger.EXTRA_CLEANUP + ledger.SEED + ledger.LEDGER_IDS


def prepare(lab, manifest, extra: str = "", tag_prefix: str = "fg"):
    """造数据、建对话；``extra`` 是接在造数后面的站点代码（能用 ``company`` / ``conn`` / ``ids`` / ``sid``），它最后一行打印的 JSON 并进参数。"""
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = tag_prefix + uuid.uuid4().hex[:6]
    out = lab.python(site, seed() + f"""
from pearnly_core.assistant import sessions
made = sessions.create(project=company["id"], period={MONTH!r}, ledger=ids.get({ledger.T8!r}, ""))
sid = made["id"]
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": sid}}, "title", {tag!r})
frappe.db.commit()
base = {{"company": company["id"], "connection": conn["name"], "session": sid, "t8": ids.get({ledger.T8!r}, "")}}
extra = {{}}
""" + extra + """
print(json.dumps({**base, **extra}, default=str))
""")
    return {**out, "tag": tag, "cases": []}


def cleanup(lab, manifest, params, extra: str = ""):
    company = params.get("company") or "none"
    lab.python(manifest["site"], ledger._mine(ledger.CLEANUP) + ledger.EXTRA_CLEANUP + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {company!r}}},
                          pluck="session_id", limit_page_length=20):
    sessions.delete(sid)
""" + extra + """
gone = cleanup()
frappe.db.commit()
print(json.dumps({"gone": gone, "left": leftovers()}))
""")


def open_conversation(params):
    return {"name": "打开对话", "wait_ms": 6000, "js": JS +
            f"setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent({json.dumps(params['session'])})),0); return true;"}


def open_business(business):
    """点业务菜单上的一项；回 {label, clicked, business, keys}。"""
    return {"name": "点菜单", "js": JS + HELP +
            f"await until(()=>menuRow(),160); const {{label, via}}=await pickBusiness({json.dumps(business)}); const b=!!via; "
            "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-question\"]'),160); await sleep(500); "
            "return {label, clicked: !!b, business: card()?.dataset.business||'', "
            "keys: [...(card()?.querySelectorAll('[data-slot=\"cc-guide-question\"]')||[])].map(x=>x.dataset.key)};"}


def answer(want: dict, name: str = "作答"):
    """一页一题答到卡离开问题页（复核卡 / 确定卡 / 结果）；回每页 {key, input, did}。要选的项在「显示更多」后面时先点开再选。"""
    return {"name": name, "js": JS + HELP + f"const WANT={json.dumps(want, ensure_ascii=False)}; " +
            "const pages=[]; for(let k=0;k<12;k++){ const c=card(); const qn=c?.querySelector('[data-slot=\"cc-guide-question\"]'); "
            " if(!qn) break; const key=qn.dataset.key, input=qn.dataset.input; let did=''; "
            " const opts=[...qn.querySelectorAll('[data-slot=\"cc-guide-option\"]')]; "
            " if(key in WANT){ const w=WANT[key]; "
            "  if(input==='file'){ for(const n of [].concat(w)){ const row=opts.find(o=>o.dataset.fileName===n); if(row&&!row.hasAttribute('data-checked')) row.click(); await sleep(150);} did='file'; } "
            "  else if(opts.length){ const find=()=>{const os=[...qn.querySelectorAll('[data-slot=\"cc-guide-option\"]')]; return os.find(o=>o.dataset.code===w)||os.find(o=>o.innerText.includes(w));}; "
            "   let hit=find(); for(let m=0;!hit&&m<5;m++){ const more=qn.querySelector('[data-slot=\"cc-guide-more\"]')||c.querySelector('[data-slot=\"cc-guide-more\"]'); if(!more) break; more.click(); await sleep(400); hit=find(); } "
            "   hit?.click(); did='pick:'+(hit?.dataset.code||''); } "
            "  else { const inp=qn.querySelector('[data-slot=\"cc-guide-value\"]')||qn.querySelector('input'); if(inp) setVal(inp,String(w)); did='value'; } } "
            " else { const skip=c.querySelector('[data-slot=\"cc-askq-skip\"]'); "
            "  if(skip&&!skip.disabled&&!opts.some(o=>o.hasAttribute('data-checked'))&&!(qn.querySelector('[data-slot=\"cc-guide-value\"]')?.value)){ "
            "   const before=c.innerText; skip.click(); pages.push({key,input,did:'skip'}); await until(()=>!card()||card().innerText!==before,80); await sleep(500); continue; } "
            "  if(opts.length&&!opts.some(o=>o.hasAttribute('data-checked'))){ const hit=opts.find(o=>o.querySelector('[data-slot=\"badge\"]'))||opts[0]; hit.click(); did='first:'+(hit.dataset.code||''); } } "
            " await sleep(250); const next=c.querySelector('[data-slot=\"cc-askq-next\"]'); pages.push({key,input,did,enabled:!!next&&!next.disabled}); "
            " if(!next||next.disabled) break; const before=c.innerText; next.click(); "
            " await until(()=>!card()||card().innerText!==before,120); await sleep(600);} "
            "return {pages, stage: card()?.dataset.stage||'', status: card()?.dataset.status||''};"}


def confirm(name: str = "确定", shot: str = "1-result"):
    """确定卡 / 复核卡上点确定那一下，等结果卡（结果在算的等它算完）。"""
    return {"name": name, "shot": True, "file": shot, "js": JS + HELP +
            "await until(()=>card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'),160); const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); "
            "const label=b?b.innerText.trim():'', enabled=!!b&&!b.disabled; const run=card()?.dataset.run||''; b?.click(); "
            "await until(()=>{const r=result(); return r&&r.dataset.result&&r.dataset.result!=='queued';},240); await sleep(800); "
            "return {label, enabled, run, ...readResult()};"}


def reread(name: str = "刷新后"):
    """刷新页面后再读结果卡（结果是每次取卡时现算的）。"""
    return {"name": name, "js": JS + HELP + "setTimeout(()=>location.reload(),0); return true;", "wait_ms": 6000}


def read_result(name: str = "读结果"):
    return {"name": name, "js": JS + HELP + "await until(()=>result(),200); await sleep(800); return readResult();"}
