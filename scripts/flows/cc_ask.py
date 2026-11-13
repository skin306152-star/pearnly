"""流程用例：CC 对话里的通用提问卡片（工具 ask_user，用户 11-05：1:1 照 Claude Code 的原生提问卡片；主会话 11-05 要能真点到底）。

实验室的假智能体后端（`scripts/flows/lab_agents.py`）认用户原文里的 ``[lab-ask]{…}[/lab-ask]``：照真实引擎发 ``agent.custom_tool_use``
（ask_user，入参就是这段 JSON）并停在 requires_action；页面照常提醒站点执行，站点登记成等作答（执行记录 awaiting_approval），对话里
出提问卡。点「提交」= 对话的「允许」带上作答（`assistant.api.confirm` 的 answers），站点核对后记进执行记录、以 user.custom_tool_result
交回引擎；假后端接着回一句「[lab-agent] ask_user <结果原文>」——断言的就是模型收到的工具结果。不调付费模型。

两题：账套（推荐项默认选中）、费用科目（「其他」接共用写账前置判定的服务端搜索：按造好的这张单定账套，搜合成账套的科目表）。
电脑与手机宽度各跑一遍；手机上提问卡不浮成允许卡（输入框上方没有允许浮层），「需要您的输入」在卡片下方；电脑上它在对话的工作状态行。
刷新后卡片照执行记录显示成答过的摘要。造数据同 `cowork_operator_lib.py`（产品自己的函数 + 合成账套目录，不连 Express）。
"""
from __future__ import annotations

import json
import re
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP

TITLE = "CC 对话里的通用提问卡片 ask_user：出题、推荐项默认选中、翻页、「其他」走服务端搜索、提交后服务端记下与模型收到的结果、刷新后是答过的摘要"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
REPLY = "[lab-agent] ask_user "
USER = "admin@cowork.regression"
CARD = "q('[data-slot=\"cc-askq\"]')"


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    ref = "FLOW-ASK-" + uuid.uuid4().hex[:6].upper()
    # 合成账套本来没有读到过主档（共用造数据不报）：这条流程自己给这份账套记一份科目表（同电脑读到主档后记下的那一处
    # `pool.put_facts`），「其他」才搜得到东西；5300 是造单时记住的费用科目，要在表里
    out = lab.python(site, CLEANUP + BASE + f"""
chart = [{{"code": "5300", "name": "Freight"}}, {{"code": "5310", "name": "Postage"}}, {{"code": "5320", "name": "Office supplies"}}]
pool.put_facts(frappe.get_doc("Cowork ERP Connection", conn["name"]), "flowop", {{"reported_accounts": chart}},
               device="lab", routine=False, now=frappe.utils.now_datetime())
frappe.db.commit()
name = recognized({ref!r})
frappe.db.commit()
print(json.dumps({{"company": company["id"], "doc": name}}, default=str))
""")
    tag = "ask" + uuid.uuid4().hex[:6]
    questions = [
        {"question": f"{tag} 这批单写进哪份账套？", "header": "账套", "multiSelect": False, "options": [
            {"label": "Flow 2026 (推荐)", "description": "这家公司 2026 年一直用的账套"},
            {"label": "Flow 2025", "description": "上一年度的账套"}]},
        {"question": f"{tag} Flow seller 的运费记到哪个科目？", "header": "费用科目", "multiSelect": False, "options": [
            {"label": "5300 Expense (推荐)", "description": "这家供应商以往都记这里"},
            {"label": "5310 Other", "description": "名称相近的科目"}],
         "search": {"company": out["company"], "doc_id": out["doc"], "kind": "accounts"}},
    ]
    return {**out, "ref": ref, "tag": tag, "questions": questions, "cases": []}


def steps(params, camera_code=None):
    ask = {"questions": params["questions"]}
    message = f"{params['tag']} 开始 [lab-ask]{json.dumps(ask, ensure_ascii=False)}[/lab-ask]"
    # 读卡片此刻的样子（每一步开头拼上）
    read = ("const read=()=>{const c=" + CARD + "; const opts=qa('[data-slot=\"cc-askq\"] [data-slot=\"cc-askq-option\"]'); "
            "const n=c?.querySelector('[data-slot=\"cc-askq-next\"]'); "
            "return {question: c?.querySelector('[data-slot=\"cc-askq-question\"]')?.innerText.trim()||'', "
            "nth: c?.querySelector('.cc-askq-nth')?.innerText.trim()||'', "
            "options: opts.map(o=>({label:o.querySelector('.cc-askq-label')?.innerText.trim()||'', desc:o.querySelector('.cc-askq-desc')?.innerText.trim()||'', "
            "checked:o.hasAttribute('data-checked')})), other: !!c?.querySelector('[data-slot=\"cc-askq-other-option\"]'), "
            "badge: !!c?.querySelector('[data-slot=\"badge\"]'), next: n?{text:n.innerText.trim(), disabled:n.disabled}:null};};")
    head = JS + read
    return [
        {"name": "出题", "js": head + f"const sent=await say({json.dumps(message)}); const shown=await until(()=>{CARD},160); "
         "await sleep(800); "
         "return {...read(), sent, seen: shown, sheet: !!q('[data-slot=\"cc-ask-sheet\"]'), "
         "status: !!q('[data-slot=\"cc-askq-status\"]'), running: q('[data-slot=\"cc-run-indicator\"]')?.dataset.kind||''};"},
        {"name": "翻页", "js": head + "const after=qa('button[aria-label=\"下一项\"]').find(seen); if(!after) return {error:'no pager'}; after.click(); "
         "await sleep(300); const two=read(); const prev=qa('button[aria-label=\"上一项\"]').find(seen); prev?.click(); await sleep(300); "
         "return {two, back: read()};"},
        {"name": "下一步到第 2 项", "js": head + "const n=" + CARD + "?.querySelector('[data-slot=\"cc-askq-next\"]'); n?.click(); await sleep(400); "
         "return read();"},
        {"name": "其他走搜索", "js": head + "const o=" + CARD + "?.querySelector('[data-slot=\"cc-askq-other-option\"]'); if(!o) return {error:'no other'}; o.click(); "
         "const found=await until(()=>qa('[data-slot=\"cc-askq-choice\"]').length>0,120); "
         "const choices=qa('[data-slot=\"cc-askq-choice\"]').map(e=>e.innerText.trim()); const pick=qa('[data-slot=\"cc-askq-choice\"]')[0]; pick?.click(); "
         "await sleep(300); const now=read(); return {listed: found, choices, picked: pick?.innerText.trim()||'', input: q('[data-slot=\"cc-askq-other\"] input')?.value||'', "
         "selected: pick?.getAttribute('aria-selected'), next: now.next};"},
        {"name": "提交", "js": head + "const n=" + CARD + "?.querySelector('[data-slot=\"cc-askq-next\"]'); if(!n) return {error:'no submit'}; n.click(); "
         "const done=await until(()=>" + CARD + "?.querySelector('[data-slot=\"cc-ask-answer\"]'),160); "
         # 回复是流式出来的：等到结果原文整段是一个完整的 JSON 再取
         f"const R={json.dumps(REPLY)}; const whole=()=>{{for(const e of qa('[data-role=\"assistant\"]')){{const t=e.innerText; const i=t.indexOf(R); if(i<0) continue; "
         "const s=t.slice(i+R.length); const j=s.lastIndexOf('}'); try{JSON.parse(s.slice(0,j+1)); return t;}catch(x){}} return '';}; "
         "const replied=await until(()=>whole(),200); const reply=whole(); "
         "return {done, replied, reply, answer: " + CARD + "?.querySelector('[data-slot=\"cc-ask-answer\"]')?.innerText.trim()||'', "
         "buttons: !!" + CARD + "?.querySelector('[data-slot=\"cc-askq-next\"]')};"},
        # 刷新：地址里不留对话（nav.ts 处理完 replaceState 回 /assistant），按会话编号重开这段（同 cc.operator.replay）；本人最近的对话就是这段
        {"name": "刷新", "js": JS + "const sid=((await listed('chat'))[0]||{}).id||''; "
         "setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent(sid)),0); return {sid};", "wait_ms": 6000},
        {"name": "刷新后", "js": head + "const done=await until(()=>" + CARD + "?.querySelector('[data-slot=\"cc-ask-answer\"]'),200); "
         "return {done, answer: " + CARD + "?.querySelector('[data-slot=\"cc-ask-answer\"]')?.innerText.trim()||'', "
         "buttons: !!" + CARD + "?.querySelector('[data-slot=\"cc-askq-next\"]'), sheet: !!q('[data-slot=\"cc-ask-sheet\"]')};"},
    ]


def _result(reply: str) -> dict:
    """回复里「ask_user 」后面那段结果原文（到最后一个右花括号；后面是状态行的用时）。"""
    try:
        text = reply.split(REPLY, 1)[1]
        return json.loads(text[:text.rindex("}") + 1])
    except (IndexError, ValueError):
        return {}


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, paged, second, other, sent, again = (by.get(k) or {} for k in ("出题", "翻页", "下一步到第 2 项", "其他走搜索", "提交", "刷新后"))
    q1, q2 = params["questions"]
    labels = [o.get("label") for o in first.get("options") or []]
    got = _result(sent.get("reply") or "")
    answers = got.get("answers") or []
    mobile = bool(first.get("status"))
    return [
        {"case": "模型调 ask_user 后对话里出提问卡：第一题、两个选项带说明、推荐项带「推荐」徽章并默认选中、最后是「其他」，「第 1 项，共 2 项」，主按钮「下一步」能点",
         "seen": first,
         # 推荐项名字不再带「(推荐)」后缀，改成独立徽章（用户 11-12 批准；版式照 Claude 的提问卡，11-13）
         "ok": bool(first.get("seen")) and first.get("question") == q1["question"]
         and labels == [re.sub(r"\s*[(（](推荐|Recommended)[)）]\s*$", "", o["label"]) for o in q1["options"]]
         and bool(first.get("badge"))
         and [o.get("checked") for o in first.get("options") or []] == [True, False]
         and (first.get("options") or [{}])[0].get("desc") == q1["options"][0]["description"] and bool(first.get("other"))
         and first.get("nth") == "第 1 项，共 2 项" and (first.get("next") or {}).get("text", "").startswith("下一步")
         and not (first.get("next") or {}).get("disabled")},
        {"case": "不浮成允许卡；「需要您的输入」手机在卡片下方、电脑在对话的工作状态行", "seen": first,
         "ok": not first.get("sheet") and (mobile or first.get("running") == "input")},
        {"case": "左右箭头来回翻：第 2 项是费用科目那一题，翻回第 1 项推荐项仍选着", "seen": paged,
         "ok": (paged.get("two") or {}).get("question") == q2["question"] and (paged.get("two") or {}).get("nth") == "第 2 项，共 2 项"
         and (paged.get("back") or {}).get("question") == q1["question"]
         and [o.get("checked") for o in (paged.get("back") or {}).get("options") or []] == [True, False]},
        {"case": "「下一步」到最后一题，主按钮变「提交」", "seen": second,
         "ok": second.get("question") == q2["question"] and (second.get("next") or {}).get("text", "").startswith("提交")},
        {"case": "「其他」去服务端搜这张单的账套科目表，选一条后能提交", "seen": other,
         "ok": bool(other.get("listed")) and other.get("choices", [])[:1] == ["5300 Freight"] and bool(other.get("picked"))
         and other.get("selected") == "true" and not (other.get("next") or {}).get("disabled")},
        {"case": "提交后模型收到的工具结果：第一题选推荐的账套，第二题「其他」是选的那条「编码 名称」", "seen": {"reply": sent.get("reply"), "picked": other.get("picked")},
         "ok": bool(sent.get("replied")) and len(answers) == 2
         and answers[0].get("labels") == [q1["options"][0]["label"]] and answers[0].get("header") == q1["header"]
         and answers[1].get("labels") == [] and answers[1].get("other") == other.get("picked") and not answers[1].get("skipped")},
        {"case": "提交后卡片收成一行所选、没有按钮；刷新后照执行记录还是这一行", "seen": {"sent": sent, "again": again},
         "ok": bool(sent.get("done")) and not sent.get("buttons") and q1["options"][0]["label"] in (sent.get("answer") or "")
         and bool(again.get("done")) and not again.get("buttons") and again.get("answer") == sent.get("answer") and not again.get("sheet")},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], CLEANUP + f"""
run = frappe.get_all("Pearnly Assistant Tool Run", filters={{"tool": "ask_user", "user": {USER!r}}},
                     fields=["status", "result", "tool_input"], order_by="creation desc", limit_page_length=1)
gone = cleanup()
print(json.dumps({{"run": run[0] if run else {{}}, "gone": gone, "left": leftovers()}}, default=str))
""")
    run = rows["run"]
    result = json.loads(run.get("result") or "{}")
    asked = json.loads(run.get("tool_input") or "{}")
    return [
        {"case": "执行记录：这次 ask_user 做完，题目就是模型给的，答案按题序记下（同模型收到的）", "saved": run,
         "ok": run.get("status") == "done" and [q.get("question") for q in asked.get("questions") or []] == [q["question"] for q in params["questions"]]
         and [a.get("labels") for a in result.get("answers") or []][:1] == [[params["questions"][0]["options"][0]["label"]]]
         and len(result.get("answers") or []) == 2 and bool((result.get("answers") or [{}, {}])[1].get("other"))},
        {"case": "造的连接、公司、单据都清掉", "saved": {"gone": rows["gone"], "left": rows["left"]},
         "ok": rows["gone"]["connections"] == 1 and not any(rows["left"].values())},
    ]
