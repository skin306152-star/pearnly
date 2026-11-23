"""ERP 版 CC（智能体入口）**直写**对话里的采购 / 销售引导（documents 源）真页面流程的共用片段：`erp_cc_guide_purchase.py`、
`erp_cc_guide_sales.py` 各调一次，只差业务（主会话 11-23 放开 ERP 直写，`express.cc_guides.GUIDES` ``modes: ["chat", "operator"]``）。

与代录那条（`erp_cc_guide_operator_lib.py`，页面流程窗口 11-23）同一套点法：数据照 `erp_cc_operator_intake.prepare` 同一套造法，只是会话
开成直写（``mode="chat"``，不建代录任务）；实验栈没有识别，「交文件 → 识别」那一段不走，用 documents 源的公开入口
``run.start(doc_ids=[I:…], at="asking")`` 开这次引导。之后全在页面上点卡（不调接口、不经模型）：打开直写对话 → 题卡逐题照推荐或第一项点
（推荐只带徽章、不预选，点了才算）→ 复核卡（这张票一行）→ 点确定那一下 → 结果卡。

库里：引导运行 done、同意是页面上这一下；这段会话只有一份写入计划、同意相同；收单记录已提交成 ERP 单（来源 cc，网页同一个入账函数
``submit_chat``），单子排上了写账（本地 Express：``ledger_state`` 有值、去向 Express；实验栈没有电脑来领，不说已录入）。
"""
from __future__ import annotations

import json

import erp_cc_guide_operator_lib as operator_lib
import erp_cc_operator_intake as intake_flow

USER = intake_flow.USER
WORD = operator_lib.WORD
DOCTYPES = intake_flow.DOCTYPES


def prepare(lab, manifest, business):
    params = intake_flow.prepare(lab, manifest, direction=business, mode="chat")
    try:
        started = lab.python(manifest["site"], f"""
from pearnly_core.assistant.guide import run
frappe.set_user({USER!r})
try:
    found = run.start({params['session']!r}, {business!r}, doc_ids=[{params['doc_id']!r}], at="asking")
    frappe.db.commit()
    print(json.dumps({{"run": found["run"], "status": found["status"], "stage": found.get("stage") or ""}}, default=str))
except Exception as error:
    frappe.db.rollback()
    print(json.dumps({{"run": "", "error": repr(error)[:300]}}))
""")
    except Exception:
        intake_flow.cleanup(lab, manifest, params)
        raise
    return {**params, "business": business, "run": started.get("run") or "", "started": started}


def steps(params, camera_code=None):
    # 页面点法与代录那条一字不差（同一张引导卡），只是打开的是直写对话
    found = operator_lib.steps(params, camera_code)
    found[0] = {**found[0], "name": "打开直写对话"}
    return found


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    asked, review, done = (by.get(k) or {} for k in ("题卡", "复核", "确定"))
    business, doc = params["business"], params["doc_id"]
    rows = done.get("rows") or []
    return [
        {"case": f"直写对话里开出来的是{WORD[business]}引导卡（服务端出的，不经模型），不是代录卡，至少一题、逐题点完没有卡住",
         "seen": {"started": params.get("started"), **asked},
         "ok": asked.get("business") == business and not asked.get("operator") and not asked.get("stuck")
         and bool(asked.get("asked"))},
        {"case": "复核卡：只有这一张票、不挡写入，确定那一下能点", "seen": review,
         "ok": bool(review.get("review")) and [d.get("doc") for d in review.get("docs") or []] == [doc]
         and not any(d.get("blocking") for d in review.get("docs") or []) and bool(review.get("enabled"))},
        {"case": "点确定：结果卡列这一张、已提交并排上写账（实验栈没有电脑来领，不说已录入）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [doc]
         and all(r.get("status") not in ("failed", "withdrawn", "held", "") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    doctype = DOCTYPES[params["business"]]
    got = lab.python(manifest["site"], f"""
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": {params['session']!r}}},
                       fields=["name", "consent", "status", "mode"], order_by="creation asc", limit_page_length=10)
guide = frappe.db.get_value("Pearnly Assistant Guide Run", {params['run']!r}, ["status", "consent", "business"], as_dict=True) or {{}}
record = frappe.db.get_value("ERP LINE Intake", {params['intake']!r}, ["status", "purchase", "sales_document"], as_dict=True) or {{}}
made = record.get("purchase") if {params['business']!r} == "purchase" else record.get("sales_document")
doc = frappe.db.get_value({doctype!r}, made, ["source", "company", "status", "ledger_state", "ledger_target"], as_dict=True) \\
    if made else None
print(json.dumps({{"plans": plans, "guide": guide, "record": record, "made": made, "doc": doc}}, default=str))
""")
    plans, guide, doc = got.get("plans") or [], got.get("guide") or {}, got.get("doc") or {}
    consents = [str(plan.get("consent") or "") for plan in plans]
    return [
        {"case": "引导运行办完（done），同意是页面上这一下（guide:<运行>:<版本>）", "seen": guide,
         "ok": guide.get("status") == "done" and guide.get("business") == params["business"]
         and str(guide.get("consent") or "").startswith(f"guide:{params['run']}:")},
        {"case": "这段会话只有一份写入计划，是直写，同意与引导相同", "seen": plans,
         "ok": len(plans) == 1 and consents[0] == str(guide.get("consent") or "") and plans[0].get("mode") != "operator"},
        {"case": "收单记录用网页同一个入账函数提交成这家公司的 ERP 单（来源 cc、已提交），没交给代录", "seen": got,
         "ok": (got.get("record") or {}).get("status") == "confirmed" and bool(got.get("made"))
         and doc.get("source") == "cc" and doc.get("company") == params["company"] and doc.get("status") == "submitted"},
        {"case": "单子排上了写账（本地 Express：有写账状态、去向 Express）", "seen": doc,
         "ok": bool(doc.get("ledger_state")) and doc.get("ledger_target") == "express"},
    ]


def cleanup(lab, manifest, params):
    intake_flow.cleanup(lab, manifest, params)
