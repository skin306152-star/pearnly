"""流程用例：Cowork 的 CC「代录」（统一对话框架，代录任务就是一段会话）开始前卡片里的「推送结果不明」——上次推送电脑领单途中断了
（领单超时，推送记录转人工、结果不明），在代录新建页选这家公司说一句建代录会话，代录事件源出的「开始前」卡片列出这张单与站点那一句，
点「没有入账」后照复核页「核对」同一条路放开（推送记录 manual / confirmed_not_entered、锁放掉、单据原因跟着改），卡片重新预检时它
不再算结果不明。

执行到的服务端：`pearnly_core.assistant.api.create`（mode=operator，同一事务建任务）→ 代录事件源（`express_operator.feed`）；
卡片调 `pearnly_core.express_operator.api`（documents / precheck / resolve）→ Cowork 的 `assistant.operator_source`（sources、precheck、
resolve）→ `assistant.precheck_source.operator_check` 与 `resolve_unknown` → `push.lifecycle.result_unknown`（预检与核对两处判结果不明）。
造数据见 `cowork_operator_lib.py`（产品自己的函数 + 实验室的合成写入端，不连 Express）。说的那一句交给实验室的假智能体。
网页上没有 CC 桌面版的桥：不点「开始」。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP

TITLE = "Cowork CC 代录：开始前卡片列出推送结果不明的单，点「没有入账」放开（代录会话、预检 / 核对，产品账本，网页无桥不开录）"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
USER = "admin@cowork.regression"

def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, 'frappe.db.set_value("User", "admin@cowork.regression", "language", "zh")\nfrappe.db.commit()\nprint("{}")')
    ref = "FLOW-UNK-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, CLEANUP + BASE + f"""
name = recognized({ref!r})
record = prepared(name)
claim = native.lease(record, context=context_of(record), adapter=writer, now=datetime.now(timezone.utc) - timedelta(hours=1))
frappe.db.commit()
# 领单后电脑断了：租约过期 → 转人工、结果不明（锁保留，不自动重试）
native.recover_expired(record, context=context_of(record), adapter=writer, now=datetime.now(timezone.utc))
frappe.db.commit()
row = frappe.db.get_value("Cowork Push Record", record, ["status", "result_unknown", "reason"], as_dict=True)
doc = frappe.db.get_value("Cowork OCR History", name, ["last_push_status", "last_push_reason", "last_push_record"], as_dict=True)
from pearnly_core.express_operator import tasks
frappe.local.lang = "zh"
print(json.dumps({{"company": company["id"], "title": company["title"], "period": period, "doc": name, "record": record,
                  "leased": bool(claim.get("leased")), "record_state": dict(row), "doc_state": dict(doc),
                  "message": frappe._("intake.error.ERP_CONFIRMED_PUSH_RESULT_UNKNOWN"),
                  "docs": len(frappe.get_attr("pearnly_cowork.assistant.operator_source.sources")(company["id"], period, 0, 100)["documents"]),
                  "tasks": frappe.db.count(tasks.DOCTYPE, {{"owner": "admin@cowork.regression"}}),
                  "sessions": frappe.db.count("Pearnly Assistant Session", {{"user": "admin@cowork.regression"}})}}, default=str))
""")
    if not out["leased"] or out["record_state"].get("status") != "manual" or not out["record_state"].get("result_unknown"):
        raise RuntimeError(f"setup did not reach result unknown: {out}")
    return {**out, "ref": ref, "line": "flow " + ref + " 这次先录采购", "cases": []}


def steps(params, camera_code=None):
    title, ref = params["title"], params["ref"]
    card = "q('[data-slot=\"cc-op-before\"]')"
    unknown = f"qa('[data-slot=\"cc-op-unknown\"]').find(e=>e.innerText.includes({json.dumps(ref)}))"
    return [
        {"name": "代录新建页选公司说一句", "js": JS + "const switched=await toMode('operator'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         "const before=(await listed('operator')).map(s=>s.id); "
         f"const picked=await pickCompany({json.dumps(title)}); const sent=await say({json.dumps(params['line'])}); "
         f"const ok=await until(()=>{card}&&q('[data-slot=\"cc-op-target\"]'),160); "
         "const now=await listed('operator'); const fresh=now.filter(s=>!before.includes(s.id)); "
         "return {switched, picked, sent, ok, mode: mode(), fresh: fresh.map(s=>({id:s.id, mode:s.mode, task:s.task, project:s.project})), "
         "target: q('[data-slot=\"cc-op-target\"]')?.innerText.trim()||''};"},
        {"name": "开始前卡片", "js": JS + f"const shown=await until(()=>{unknown},160); const row={unknown}; "
         "await sleep(500); "
         "return {shown, text: row?.innerText.trim()||'', unknown: qa('[data-slot=\"cc-op-unknown\"]').length, "
         "buttons: row?[...row.querySelectorAll('button')].map(b=>b.innerText.trim()):[], "
         f"start: !!{card}?.querySelector('[data-slot=\"cc-op-start\"]')}};"},
        {"name": "资料", "js": JS + "const opened=await openSteps('cc-op-sources'); if(!opened) return {error:'no sources step'}; "
         "q('[data-slot=\"cc-op-sources\"] button.cc-step-row').click(); "
         "await until(()=>q('[data-slot=\"cc-op-sources-list\"] [data-slot=\"cc-op-doc\"]'),80); "
         "for(let k=0;k<20;k++){const more=q('[data-slot=\"cc-op-sources-list\"] .cc-show-more'); if(!more) break; more.click(); await sleep(800);} "
         "await sleep(300); const docs=qa('[data-slot=\"cc-op-sources-list\"] [data-slot=\"cc-op-doc\"]'); "
         "return {rows: docs.length, text: docs.map(e=>e.innerText).join('\\n').slice(0,2000)};"},
        {"name": "没有入账", "js": JS + f"const row={unknown}; if(!row) return {{error:'no unknown row'}}; "
         "row.querySelector('[data-slot=\"cc-op-not-entered\"]').click(); "
         f"const gone=await until(()=>!({unknown}),120); await sleep(800); "
         f"return {{gone, unknown: qa('[data-slot=\"cc-op-unknown\"]').length, failure: {card}?.querySelector('[data-slot=\"cc-ask-failure\"]')?.innerText||'', "
         f"start: !!{card}?.querySelector('[data-slot=\"cc-op-start\"]')}};"},
        {"name": "删除会话", "js": JS + "const now=await listed('operator'); "
         f"const mine=now.filter(s=>s.project==={json.dumps(params['company'])}&&s.task); if(!mine.length) return {{error:'no session'}}; "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); "
         "const after=(await listed('operator')).map(s=>s.id); return {ids: mine.map(s=>s.id), left: mine.some(s=>after.includes(s.id))};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    made, ask, files, resolved, removed = (by.get(k) or {} for k in ("代录新建页选公司说一句", "开始前卡片", "资料", "没有入账", "删除会话"))
    text = ask.get("text") or ""
    fresh = made.get("fresh") or []
    return [
        {"case": "代录新建页选这家公司说一句：建了一段代录会话（挂着任务、这家），出「开始前」卡片写入这家的 Express", "seen": made,
         "ok": bool(made.get("picked")) and bool(made.get("sent")) and bool(made.get("ok")) and made.get("mode") == "operator"
         and len(fresh) == 1 and fresh[0].get("mode") == "operator" and bool(fresh[0].get("task")) and fresh[0].get("project") == params["company"]
         and params["title"] in (made.get("target") or "")},
        {"case": "资料那一步与站点这家这个月等着录的单一致", "seen": {**files, "site": params["docs"]},
         "ok": files.get("rows") == params["docs"] and params["ref"] in (files.get("text") or "")},
        # 原因按代码出文案（统一框架的卡片，operator-shared.ts 的 reasonText：push.result_unknown →「结果不明」），不再照抄站点那一句
        {"case": "开始前卡片列出推送结果不明的那张：单号 · 对方、原因「结果不明」、「已入账」「没有入账」两个按钮；卡上没有「开始」"
                 "（11-11 一句话下令）", "seen": ask,
         "ok": bool(ask.get("shown")) and params["ref"] in text and "Flow seller" in text and "结果不明" in text
         and ask.get("buttons") == ["已入账", "没有入账"] and not ask.get("start")},
        {"case": "点「没有入账」后卡片重新预检，这张不再列为结果不明、没有报错", "seen": resolved,
         "ok": bool(resolved.get("gone")) and resolved.get("unknown") == 0 and not resolved.get("failure") and not resolved.get("start")},
        {"case": "删掉这段代录会话（连任务），列表里没有了", "seen": removed, "ok": bool(removed.get("ids")) and removed.get("left") is False},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], CLEANUP + f"""
from pearnly_core.express_operator import tasks
frappe.set_user("admin@cowork.regression")
record = frappe.db.get_value("Cowork Push Record", {params['record']!r}, ["status", "reason", "result_unknown"], as_dict=True)
doc = frappe.db.get_value("Cowork OCR History", {params['doc']!r}, ["last_push_status", "last_push_reason", "last_push_record"], as_dict=True)
check = frappe.get_attr("pearnly_cowork.assistant.precheck_source.operator_check")({params['doc']!r})
locks = frappe.db.count("Cowork Push Writer Lock", {{"holder": {params['record']!r}}})
attempts = frappe.get_all("Cowork Push Attempt", filters={{"push_record": {params['record']!r}}}, pluck="status")
records = frappe.db.count("Cowork Push Record", {{"history": {params['doc']!r}}})
count = frappe.db.count(tasks.DOCTYPE, {{"owner": "admin@cowork.regression"}})
sessions = frappe.db.count("Pearnly Assistant Session", {{"user": "admin@cowork.regression"}})
gone = cleanup()
print(json.dumps({{"record": dict(record or {{}}), "doc": dict(doc or {{}}), "check": check, "locks": locks, "attempts": attempts,
                  "records": records, "tasks": count, "sessions": sessions, "gone": gone, "left": leftovers()}}, default=str))
""")
    record, doc = rows["record"], rows["doc"]
    return [
        {"case": "推送记录照「核对」落成没有入账：manual / confirmed_not_entered、不再结果不明，领单的那次结束、写者锁放掉",
         "saved": {k: rows[k] for k in ("record", "attempts", "locks")},
         "ok": record.get("status") == "manual" and record.get("reason") == "confirmed_not_entered" and not int(record.get("result_unknown") or 0)
         and rows["attempts"] == ["finished"] and rows["locks"] == 0},
        {"case": "单据上的推送原因跟着改（不再是结果不明），没有新建推送记录", "saved": {"doc": doc, "records": rows["records"]},
         "ok": doc.get("last_push_record") == params["record"] and doc.get("last_push_reason") == "confirmed_not_entered"
         and rows["records"] == 1},
        {"case": "再预检这张不再是结果不明", "saved": rows["check"], "ok": rows["check"].get("reason") != "push.result_unknown"},
        {"case": "任务记录、会话回到流程前的条数（会话删除连任务一起删）", "saved": {"before": [params["tasks"], params["sessions"]],
         "after": [rows["tasks"], rows["sessions"]]}, "ok": rows["tasks"] == params["tasks"] and rows["sessions"] == params["sessions"]},
        {"case": "造的连接、公司、单据、锁都清掉", "saved": {"gone": rows["gone"], "left": rows["left"]},
         "ok": rows["gone"]["connections"] == 1 and not any(rows["left"].values())},
    ]
