"""Cowork 版 CC 代录对话里的采购 / 销售引导（documents 源）真页面流程的共用片段：`cowork_cc_guide_operator_purchase.py`、
`cowork_cc_guide_operator_sales.py` 各调一次，只差业务。

造数据、开引导、点卡同直写那套（`cowork_cc_guide_docs_lib`：一条 Express 连接、同一税号两本同期账套 T.8 / T.9、录入人一台登记过的电脑、
一张本业务识别完成的税票，``run.start(doc_ids=[单据], at="asking")`` 开这次引导），只是对话建成代录（``sessions.create(mode="operator")``，
同一事务建代录任务）。Cowork 的采购 / 销售引导声明 ``modes`` 含 operator（`assistant/guides.py`）。

之后全在页面上点卡（不调接口、不经模型）：打开代录对话 → 题卡逐题照推荐或第一项点 → 复核卡（这张票一行）→ 点确定那一下 →
结果卡（交进代录任务，实验栈没有电脑来领）→ cleanup 删会话（连任务、引导运行）与造的数据。
库里：引导运行 done、同意是页面上这一下；这段会话只有一份写入计划、同意相同；这张票交进代录任务一次（同 ERP 代录那两条）。
"""
from __future__ import annotations

import json
import uuid

import cc_conversation_ledger as ledger
import cowork_cc_guide_docs_lib as docs

USER = ledger.USER
WORD = docs.WORD


def prepare(lab, manifest, business):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "go" + uuid.uuid4().hex[:6]
    number = "FLOW-GO-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, docs._seed_code() + f"""
from pearnly_core.assistant import sessions
from pearnly_core.assistant.guide import run
name = guided_doc({business!r}, {number!r})
made = sessions.create(project=company["id"], period={ledger.CHAT_MONTH!r}, mode="operator", ledger=ids.get({ledger.T8!r}, ""))
sid = made["id"]
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": sid}}, "title", {tag!r})
frappe.db.commit()
try:
    started = run.start(sid, {business!r}, doc_ids=[name], at="asking")
    frappe.db.commit()
    started = {{"run": started["run"], "status": started["status"], "stage": started.get("stage") or ""}}
except Exception as error:
    frappe.db.rollback()
    started = {{"run": "", "error": repr(error)[:300]}}
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name, "connection": conn["name"],
                  "t8": ids.get({ledger.T8!r}, ""), "t9": ids.get({ledger.T9!r}, ""), "session": sid,
                  "task": made.get("task") or "", "started": started}}, default=str))
""")
    return {**out, "business": business, "number": number, "tag": tag, "run": (out.get("started") or {}).get("run") or "", "cases": []}


steps = docs.steps


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    asked, review, done = (by.get(k) or {} for k in ("题卡", "复核", "确定"))
    business, doc = params["business"], params["doc"]
    rows = done.get("rows") or []
    return [
        {"case": f"代录对话里开出来的是{WORD[business]}引导卡（服务端出的，不经模型），逐题点完没有卡住",
         "seen": {"started": params.get("started"), **asked},
         "ok": asked.get("business") == business and not asked.get("stuck")},
        {"case": "复核卡：只有这一张票、不挡写入，票面带单号与合计 107.00，确定那一下能点", "seen": review,
         "ok": bool(review.get("review")) and [d.get("doc") for d in review.get("docs") or []] == [doc]
         and not any(d.get("blocking") for d in review.get("docs") or []) and params["number"] in review.get("text", "")
         and "107" in review.get("text", "") and bool(review.get("enabled"))},
        {"case": "点确定：结果卡列这一张、已交进代录（实验栈没有电脑来领，不说已录入）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [doc]
         and all(r.get("status") not in ("failed", "withdrawn", "") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": {params['session']!r}}},
                       fields=["name", "consent", "status", "mode"], order_by="creation asc", limit_page_length=10)
guide = frappe.db.get_value("Pearnly Assistant Guide Run", {params['run']!r}, ["status", "consent", "business"], as_dict=True) or {{}}
docs = []
if {params.get('task', '')!r} and frappe.db.exists("Pearnly Express Operator Task", {params.get('task', '')!r}):
    docs = [r.doc_id for r in frappe.get_doc("Pearnly Express Operator Task", {params.get('task', '')!r}).get("documents") or []]
print(json.dumps({{"plans": plans, "guide": guide, "task_docs": docs}}, default=str))
""")
    plans, guide = got.get("plans") or [], got.get("guide") or {}
    consents = [str(plan.get("consent") or "") for plan in plans]
    handed = [str(doc) for doc in got.get("task_docs") or []]
    return [
        {"case": "引导运行办完（done），同意是页面上这一下（guide:<运行>:<版本>）", "seen": guide,
         "ok": guide.get("status") == "done" and guide.get("business") == params["business"]
         and str(guide.get("consent") or "").startswith(f"guide:{params['run']}:")},
        {"case": "这段会话只有一份写入计划，同意与引导相同", "seen": consents,
         "ok": len(plans) == 1 and consents[0] == str(guide.get("consent") or "")},
        {"case": "这张票交进代录任务一次", "seen": handed,
         "ok": sum(1 for doc in handed if doc.split(":")[-1] == params["doc"]) == 1},
    ]


def cleanup(lab, manifest, params):
    """排着等电脑的代录任务不能删（`tasks.delete`）：先按站点收尾，仍在排队的（实验栈没有电脑来领）标停止；再照直写那套删会话与数据。"""
    if params.get("task"):
        lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
frappe.set_user({USER!r})
if frappe.db.exists(tasks.DOCTYPE, {params['task']!r}):
    tasks.settle({params['task']!r})
    if frappe.db.get_value(tasks.DOCTYPE, {params['task']!r}, "status") in ("queued", "running"):
        frappe.db.set_value(tasks.DOCTYPE, {params['task']!r}, "status", "stopped", update_modified=False)
    frappe.db.commit()
print("{{}}")
""")
    docs.cleanup(lab, manifest, params)
