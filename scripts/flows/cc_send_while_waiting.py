"""流程用例：CC 对话里识别挂在服务端等时，在界面上点发送能发出去（用户 11-08 定方案 B：模型空闲就显示「发送」、在工作就显示
「停止」；识别在服务端挂着等时模型没在工作，算空闲，只显示发送、输入框不锁，状态行照常显示「识别中 · 共 N 页 / 排队中」）。

11-08 线上验收：识别挂着的 5 分钟里发送键一直是停止键，「现在识别到哪了？」发不出去；11-07 记的「挂着时发消息 13 秒有回复」是直接调
接口、没走界面，不算数（共享笔记「已知问题与结论」：界面类验收必须在界面上点）。所以这里每一步都在页面上点。

做法：实验室假智能体（`lab_agents.py`）认用户原文里的 ``[lab-tool]{…}[/lab-tool]``，照真实引擎调 `cowork_recognize_attachments`；
实验室没有识别进程，识别只排队，推送线程等约 90 秒后这次调用挂起（`pearnly_core.assistant.waits`）。挂起后看输入框右边那颗按钮
是发送不是停止、状态行还在说这一步；在输入框里打一句、点发送：服务端先把挂着的调用按「还在识别」交回、再收这条（`sessions.send` →
`waits.release`），假智能体回这一句。直写对话与代录对话（代录新建页选公司说一句建的会话）各一条，电脑与手机宽度各跑一遍。
附件是 prepare 用框架自带的 PDF 生成造的 2 页 PDF（不识别、不扣费）。跑完删掉会话（挂着的调用随之作废）。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP

MODE = "direct"
TITLE = "CC 对话识别挂在服务端等时在界面上点发送能发（直写，方案 B：模型空闲显示发送，状态行照常说识别这一步）"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
USER = "admin@cowork.regression"
# 识别要的站点配置（同线上 `infrastructure/production/site-step.py` 的 COWORK_OCR 与预算 Redis）：没有它识别当场「请先配置」、不会挂起。
# 凭据只填占位（实验室没有识别进程，任务一直排队、不会真去读）；prepare 记下原值，cleanup 还原（成功、失败、步骤超时都还原）。
OCR_CONFIG = {"pearnly_cowork_ocr_environment": {"OCR_LLM_BACKEND": "vertex"}, "pearnly_cowork_ocr_policy": {"mode": "economy"},
              "pearnly_ocr_credentials": "/nonexistent/flow-placeholder.json", "pearnly_ocr_budget_redis": "redis://redis:6379/6"}


def prepare(lab, manifest, mode=None):
    mode = mode or MODE
    site = manifest["site"]
    tag = "wait" + uuid.uuid4().hex[:6]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    company = {}
    if mode == "operator":
        company = lab.python(site, CLEANUP + BASE + 'frappe.db.commit()\nprint(json.dumps({"company": company["id"], "title": company["title"]}))')
    made = lab.python(site, f"""
from frappe.utils.pdf import get_pdf
html = "<h1>{tag} p1</h1><div style='page-break-after: always'></div><h1>{tag} p2</h1>"
data = get_pdf(html)
doc = frappe.get_doc({{"doctype": "File", "file_name": "{tag}.pdf", "is_private": 0, "content": data}}).insert(ignore_permissions=True)
frappe.db.commit()
print(json.dumps({{"url": doc.file_url, "size": len(data)}}))
""")
    start = lab.python(site, 'print(json.dumps({"now": str(frappe.utils.now_datetime())}))')["now"]
    # 改配置放在准备的最后（之后出什么错都由 cleanup 还原）。走 `lab.site_config`：等网页进程的配置缓存（60 秒）过了才往下，
    # 否则挂附件时可能还读到「没配置」（11-09 --jobs 3）
    before = lab.site_config(site, OCR_CONFIG)
    call = {"name": "cowork_recognize_attachments", "input": {"files": [f"{tag}.pdf"]}}
    return {"mode": mode, "tag": tag, "url": made["url"], "file": f"{tag}.pdf", **company, "config_before": before, "start": start,
            "tool": f"{tag} [lab-tool]{json.dumps(call)}[/lab-tool]", "ask": f"{tag} 现在识别到哪了？", "cases": []}


ATTACH = """
const r=await fetch(URL); const blob=await r.blob(); const file=new File([blob],NAME,{type:'application/pdf'});
const orig=HTMLInputElement.prototype.click;
HTMLInputElement.prototype.click=function(){ if(this.type==='file'){ const dt=new DataTransfer(); dt.items.add(file); this.files=dt.files;
  this.dispatchEvent(new Event('change',{bubbles:true})); return; } return orig.call(this); };
q('button.cc-attach')?.click(); HTMLInputElement.prototype.click=orig;
const attached=await until(()=>[...document.querySelectorAll('[data-slot*=attachment], .aui-attachment-root')].length>0, 120);
"""
# 输入框右边那颗按钮此刻是什么（发送 / 停止）与状态行
BUTTONS = ("const now=()=>({send: !!q('button.aui-composer-send'), stop: !!q('button.aui-composer-cancel'), "
           "line: q('[data-slot=\"cc-run-indicator\"]')?.innerText.trim()||''});")


def steps(params, camera_code=None):
    mode = params["mode"]
    head = JS + BUTTONS
    open_chat = ""
    if mode == "operator":
        # 代录新建页选这家公司说一句：建一段代录会话（同 cowork_operator_unknown），等假智能体回了再接着在这段会话里说
        open_chat = ("const switched=await toMode('operator'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
                     f"const picked=await pickCompany({json.dumps(params['title'])}); "
                     f"const opened=await say({json.dumps(params['tag'] + ' 开一段代录')}); "
                     "await until(()=>[...document.querySelectorAll('[data-role=\"assistant\"]')].some(e=>e.innerText.includes('[lab-agent]')),240); "
                     "await until(()=>!!q('button.aui-composer-send'),240); ")
    attach = ATTACH.replace("URL", json.dumps(params["url"])).replace("NAME", json.dumps(params["file"]))
    return [
        {"name": "挂附件让 CC 识别", "js": head + open_chat + attach +
         f"const sent=await say({json.dumps(params['tool'])}); "
         # 推送线程先同步等最多约 90 秒（这段模型在执行这一步，照旧是停止），之后才挂起。什么时候挂起只拿来定时机：读这段会话
         # 服务端的运行状态（同对话页的事件接口），出现挂着等的（run.waiting）就往下；按钮与发送一律在界面上看、在界面上点
         # 新对话刚发出时侧栏接口里可能还没有这段会话：每轮都再找一次
         f"const find=async()=>((await listed({json.dumps('operator' if mode == 'operator' else 'chat')}))"
         f".find(x=>(x.title||'').includes({json.dumps(params['tag'])})||x.project==={json.dumps(params.get('company') or '-')})||{{}}).id; "
         "const t0=Date.now(); const trail=[]; let parked=false; let sid=''; "
         "for(let i=0;i<70;i++){ await sleep(3000); sid=sid||await find(); if(!sid) continue; const n=now(); if(!trail.length||trail[trail.length-1].line!==n.line||trail[trail.length-1].send!==n.send) "
         "trail.push({...n, s: Math.round((Date.now()-t0)/1000)}); "
         "const run=((await call('pearnly_core.assistant.api.events',{session_id:sid,status:1}))||{}).run||{}; "
         # 修改前的服务端不给 waiting：同步等待最多 90 秒，满 100 秒还是本站在执行的也算已挂起（by 记下按哪样认的）
         "if((run.waiting||[]).length){ parked='waiting'; break; } "
         "if(Date.now()-t0>=100000&&(run.executing||[]).length){ parked='100s'; break; } } "
         # 服务端挂起后页面要等下一次补拉才知道：实验室没有实时通道，引擎停在等工具结果时页面按空闲每 6 秒补拉一次
         # （poll-source SLOW_MS，再加这次请求的耗时）。以前固定只等 4 秒，赶上补拉相位不巧就还是停止键（11-10 拼车 #6 连红）。
         # 改成等到页面换成发送键、最多 20 秒（超过两轮补拉）；判据不变，flip 记下挂起后多少秒换的
         "const tp=Date.now(); let flip=null; if(parked){ if(await until(()=>!!q('button.aui-composer-send'),80)) flip=Math.round((Date.now()-tp)/100)/10; } "
         "return {mode: mode(), attached, sent, sid: !!sid, parked, flip, after: now(), trail: trail.slice(-6)};"},
        {"name": "挂着时在界面上点发送", "js": head +
         "const before=now(); const ta=q('textarea.aui-composer-input'); if(!ta) return {error:'no composer', before}; "
         f"Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(ta,{json.dumps(params['ask'])}); "
         "ta.dispatchEvent(new Event('input',{bubbles:true})); await sleep(400); "
         "const b=q('button.aui-composer-send'); const clickable=!!b&&!b.disabled; if(clickable) b.click(); "
         f"const R={json.dumps('[lab-agent] ' + params['ask'][:30])}; "
         f"const bubble=await until(()=>[...document.querySelectorAll('[data-role=\"user\"]')].some(e=>e.innerText.includes({json.dumps(params['ask'])})),60); "
         "const replied=await until(()=>[...document.querySelectorAll('[data-role=\"assistant\"]')].some(e=>e.innerText.includes(R)),240); "
         "return {before, clickable, bubble, replied, after: now()};"},
        {"name": "删除会话", "js": head + f"const mine=(await listed({json.dumps('operator' if mode == 'operator' else 'chat')}))"
         f".filter(s=>(s.title||'').includes({json.dumps(params['tag'])})||s.project==={json.dumps(params.get('company') or '-')}); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); "
         "return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    waited, asked, removed = (by.get(k) or {} for k in ("挂附件让 CC 识别", "挂着时在界面上点发送", "删除会话"))
    after = waited.get("after") or {}
    before = asked.get("before") or {}
    return [
        {"case": "挂附件让假 CC 调识别：这次调用挂在服务端等（服务端运行状态出现挂着等的调用）", "seen": waited,
         "ok": bool(waited.get("attached")) and bool(waited.get("sent")) and bool(waited.get("parked"))
         and (params["mode"] != "operator" or waited.get("mode") == "operator")},
        {"case": "挂着等时模型空闲：输入框右边是发送、没有停止，状态行照常说这一步", "seen": {"after": after, "before": before},
         "ok": bool(before.get("send")) and not before.get("stop") and bool(before.get("line"))},
        {"case": "在界面上打一句、点发送：这句出现在对话里，服务端先交回挂着的调用再收这句，CC 回了这一句", "seen": asked,
         "ok": bool(asked.get("clickable")) and bool(asked.get("bubble")) and bool(asked.get("replied"))},
        {"case": "删掉这段会话（挂着的调用随之作废）", "seen": removed, "ok": bool(removed.get("ids"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], (CLEANUP if params["mode"] == "operator" else "") + f"""
left = frappe.db.sql("select count(*) from `tabPearnly Assistant Tool Run` where wait_until is not null and status='running' "
                     "and tool_input like %s", ("%{params['tag']}%",))[0][0]
{"cleanup()" if params["mode"] == "operator" else ""}
frappe.db.delete("File", {{"file_name": "{params['file']}"}})
frappe.db.commit()
print(json.dumps({{"parked_left": int(left)}}))
""")
    return [{"case": "跑完没有留下挂着的调用、造的附件已删", "seen": out, "ok": out.get("parked_left") == 0}]


def cleanup(lab, manifest, params):
    """成功、失败、步骤超时都跑（`run.run_flow`）：作废这次排着的识别、还原站点配置（等网页进程都看到原值，不带给下一格）。"""
    # 实验室没有识别进程：这次流程交的识别一直排着，作废掉，不占后面流程的排队名额
    lab.python(manifest["site"], f"""
frappe.db.sql("update `tabPearnly OCR Work` set status='cancelled' where status in ('queued', 'deferred') and creation >= %s", ({params['start']!r},))
frappe.db.commit()
print("{{}}")
""")
    lab.site_config(manifest["site"], params["config_before"])
