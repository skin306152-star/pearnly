"""Cowork 版 CC「待处理」与「单据」引导（同一组函数，`assistant.guided.pending`）的真页面流程共用片段：
`cowork_cc_guide_pending.py`、`cowork_cc_guide_pending_operator.py`、`cowork_cc_guide_documents.py`、
`cowork_cc_guide_documents_operator.py` 各调一次，只差业务（pending / documents）与对话模式（直写 / 代录）。

造数据同 `cowork_cc_guide_form_lib`（一条 Express 连接、同一税号两本同期账套 T.8 / T.9、从目录建的公司、录入人一台登记过的电脑），
再用 `cowork_cc_guide_docs_lib.DOC` 的 ``guided_doc`` 造一张识别完成、推送目标是 T.8、还没推的进项票（待处理那两条再记成推之前停下要人选，
清单里算「要人选」），
票面日期是对话月份（代录对话清单只列对话月份的单）。对话建在这家、本月、账套 T.8，按 ``mode`` 建成直写或代录。

页面上全靠点：点业务菜单 → 操作选「删除」→ 单据选这一张 → 复核卡（一行操作、一张单）→ 允许卡上点「允许」→ 结果卡：
已删除 1 条（照录入列表那句）。库里：引导运行 done、同意来自页面，这张票真删了。
"""
from __future__ import annotations

import json
import uuid

import cc_conversation_ledger as ledger
import cowork_cc_guide_docs_lib as docs
import cowork_cc_guide_form_lib as form

USER = form.USER
MONTH = form.MONTH
WORD = {"pending": "待处理", "documents": "单据"}


def prepare(lab, manifest, business, mode="chat"):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "gm" + uuid.uuid4().hex[:6]
    number = "FLOW-GM-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, form.seed() + docs.DOC + f"""
from pearnly_core.assistant import sessions
name = guided_doc("purchase", {number!r}, {MONTH + "-01"!r})
if {business!r} == "pending":
    # 待处理清单只收要人处理的票（`companies.pending.classify`）：照 CC 推之前停下要人选时记的那样（`push_state.record_failure`）
    frappe.db.set_value("Cowork OCR History", name, "last_push_reason", "choice_needed:payment", update_modified=False)
made = sessions.create(project=company["id"], period={MONTH!r}, ledger=ids.get({ledger.T8!r}, ""),
                       **({{"mode": "operator"}} if {mode!r} == "operator" else {{}}))
sid = made["id"]
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": sid}}, "title", {tag!r})
frappe.db.commit()
print(json.dumps({{"company": company["id"], "connection": conn["name"], "session": sid, "task": made.get("task") or "",
                  "doc": name}}, default=str))
""")
    return {**out, "business": business, "mode": mode, "number": number, "tag": tag, "cases": []}


def steps(params, camera_code=None):
    return [form.open_conversation(params), form.open_business(params["business"]),
            form.answer({"action": "delete", "items": params["doc"]}), form.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, answered, done = (by.get(k) or {} for k in ("点菜单", "作答", "确定"))
    picked = [page.get("did") for page in answered.get("pages") or ()]
    return [
        {"case": f"业务菜单有「{WORD[params['business']]}」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == params["business"] and opened.get("keys") == ["action"]},
        {"case": "选「删除」、选这一张单后到复核卡", "seen": answered,
         "ok": picked[:2] == ["pick:delete", "pick:" + params["doc"]] and answered.get("status") == "reviewing"},
        {"case": "允许卡上点「允许」→ 结果卡：已删除 1 条", "seen": done,
         "ok": bool(done.get("enabled")) and done.get("status") == "done" and done.get("result") == "ready"
         and "1" in (done.get("text") or "") and "删除" in (done.get("text") or "")},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}, "business": {params['business']!r}}},
                      fields=["name", "status", "consent"], limit_page_length=3)
print(json.dumps({{"runs": runs, "doc": bool(frappe.db.exists("Cowork OCR History", {params['doc']!r}))}}, default=str))
""")
    runs = out.get("runs") or []
    return [
        {"case": "引导运行办完（done），同意是页面上这一下", "seen": runs,
         "ok": len(runs) == 1 and runs[0].get("status") == "done" and str(runs[0].get("consent") or "").startswith("guide:")},
        {"case": "这张票真删了（同录入列表「删除」）", "seen": out.get("doc"), "ok": out.get("doc") is False},
    ]


def cleanup(lab, manifest, params):
    if params.get("task"):
        lab.python(manifest["site"], f"""
from pearnly_core.express_operator import tasks
frappe.set_user({USER!r})
if frappe.db.exists(tasks.DOCTYPE, {params['task']!r}) and \\
        frappe.db.get_value(tasks.DOCTYPE, {params['task']!r}, "status") in ("queued", "running", "preparing"):
    frappe.db.set_value(tasks.DOCTYPE, {params['task']!r}, "status", "stopped", update_modified=False)
    frappe.db.commit()
print("{{}}")
""")
    form.cleanup(lab, manifest, params)
