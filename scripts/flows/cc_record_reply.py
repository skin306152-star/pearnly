"""流程用例：「录吧」快捷回复跟着写入计划出现、点了等同打字、开始后消失（一句话下令，用户 11-10；A 线写入计划 + B 线快捷回复，11-11）。

Cowork 直写对话（挂在流程造的这家公司下）：用户说一句，实验室的假智能体照真实引擎调 `pearnly_write_plan`（原文带 ``[lab-tool]``），
服务端按写账判定算出要写的这张单、存一份 awaiting 计划、推 / 补拉 ``pearnly.write_plan``；这一轮说完后输入框上方出「录吧」。点「录吧」
= 这句话照常发出（用户消息序号加一）；再由假智能体照 CC 的做法调 `pearnly_write_start`（实验室的模型不会自己开始，这一步用原文带
``[lab-tool]`` 代替），服务端核过复述之后用户确实发过话才开始，计划转 started，「录吧」消失。直写开始只把计划标成已开始，
推送要 CC 接着调推送工具（这里不推，网页上也没有 CC 桌面版）。

造数据见 `cowork_operator_lib.py`（产品自己的函数 + 合成账套与写入端，不连 Express）：一张识别好的采购单，付款与科目已记住，写账判定
没有要问的，所以计划里有它。跑完删会话（计划随之删）并清掉造的数据。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP

TITLE = "Cowork CC 直写：建写入计划后输入框上方出「录吧」，点了照常发出，开始后「录吧」消失（写入计划 + 快捷回复）"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
USER = "admin@cowork.regression"
CHIP = "[data-slot=\"cc-quick-replies\"] button"


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    ref = "FLOW-RR-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, CLEANUP + BASE + f"""
name = recognized({ref!r})
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name}}))
""")
    tag = "rr" + uuid.uuid4().hex[:6]
    plan = {"name": "pearnly_write_plan", "input": {"documents": [out["doc"]]}}
    return {**out, "ref": ref, "tag": tag, "cases": [],
            "plan_line": f"{tag} 把这张录进 Express [lab-tool]{json.dumps(plan)}[/lab-tool]"}


def steps(params, camera_code=None):
    reply = "qa('[data-role=\"assistant\"]')"
    return [
        {"name": "说一句建写入计划", "js": JS + "const switched=await toMode('chat'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(params['title'])}); const sent=await say({json.dumps(params['plan_line'])}); "
         # 等工具结果整段 JSON 画完、能取到值再读（toolResult，cc_lib）
         "await until(()=>!!toolResult('pearnly_write_plan'),240); const result=toolResult('pearnly_write_plan')||{}; "
         f"const bubble={reply}.find(e=>e.innerText.includes('pearnly_write_plan {{')); "
         "const status=result.status??null, plan=result.plan??null, count=result.count??null; window.__plan=plan; "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         f"const chip=await until(()=>!!q({json.dumps(CHIP)}),80); "
         f"return {{switched, picked, sent, replied: !!bubble, status, plan, count, chip, label: q({json.dumps(CHIP)})?.innerText.trim()||''}};"},
        {"name": "点录吧", "js": JS + f"const b=q({json.dumps(CHIP)}); if(!b) return {{error:'no chip'}}; const label=b.innerText.trim(); "
         "const users=()=>qa('[data-role=\"user\"]').map(e=>e.innerText.trim()); const before=users().length; b.click(); "
         "const bubble=await until(()=>users().length>before&&users().at(-1).includes(label),60); "
         f"const replied=await until(()=>{reply}.some(e=>e.innerText.includes('[lab-agent] '+label)),240); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         f"return {{label, bubble, replied, last: users().at(-1)||''}};"},
        # 实验室的模型不会自己开始：照 CC 听到「录吧」后的做法，用原文带 [lab-tool] 让假智能体调 pearnly_write_start（又一条用户消息）
        {"name": "按计划开始", "js": JS + "const plan=window.__plan||''; if(!plan) return {error:'no plan'}; "
         f"const line={json.dumps(params['tag'] + ' [lab-tool]')}+JSON.stringify({{name:'pearnly_write_start', input:{{plan}}}})+'[/lab-tool]'; "
         "const sent=await say(line); "
         "await until(()=>!!toolResult('pearnly_write_start'),240); const result=toolResult('pearnly_write_start')||{}; "
         f"const bubble={reply}.find(e=>e.innerText.includes('pearnly_write_start {{')); "
         "const status=result.status??null; "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         f"const gone=await until(()=>!q({json.dumps(CHIP)}),60); await sleep(1500); "
         f"return {{plan, sent, replied: !!bubble, status, gone, still: !!q({json.dumps(CHIP)})}};"},
        {"name": "删除会话", "js": JS + "const mine=(await listed('chat')).filter(s=>(s.title||'').includes("
         + json.dumps(params["tag"]) + ")); for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); "
         "return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    made, clicked, started = (by.get(k) or {} for k in ("说一句建写入计划", "点录吧", "按计划开始"))
    return [
        {"case": "说一句：CC 建写入计划（这张单要写，计划等用户点头），这一轮说完后输入框上方出「录吧」", "seen": made,
         "ok": bool(made.get("replied")) and made.get("status") == "ready" and made.get("count") == 1 and bool(made.get("chip"))},
        {"case": "点「录吧」= 这句话照常发出（对话里出现这句、CC 回了这一句）", "seen": clicked,
         "ok": bool(clicked.get("bubble")) and bool(clicked.get("replied")) and clicked.get("label") == made.get("label")},
        {"case": "CC 按计划开始：用户点头之后服务端放行（started），「录吧」消失、不再出现", "seen": started,
         "ok": started.get("status") == "started" and bool(started.get("gone")) and not started.get("still")},
        {"case": "删掉这段对话", "seen": by.get("删除会话") or {}, "ok": bool((by.get("删除会话") or {}).get("ids"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
plans = frappe.db.count("Pearnly Assistant Write Plan", {{"user": {USER!r}}})
sessions = frappe.db.count("Pearnly Assistant Session", {{"user": {USER!r}, "title": ["like", "%{params['tag']}%"]}})
print(json.dumps({{"plans": plans, "sessions": sessions}}))
""")
    return [{"case": "对话删掉后写入计划随之删掉", "saved": out, "ok": out["plans"] == 0 and out["sessions"] == 0}]


def cleanup(lab, manifest, params):
    """成功、失败、超时都跑：删这次的对话（计划随之删）、清掉造的连接与单据。"""
    lab.python(manifest["site"], CLEANUP + f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
for sid in frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "title": ["like", "%{params.get('tag') or 'none'}%"]}},
                          pluck="session_id", limit_page_length=20):
    sessions.delete(sid)
cleanup()
frappe.db.commit()
print("{{}}")
""")
