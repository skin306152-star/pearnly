"""ERP 版 CC「单据」引导（总表第 14 行单据维护，`express.cc_guide_pending` 的 documents 声明 + `express.guide_pending`）真页面流程共用片段：
`erp_cc_guide_documents.py`（导出）、`erp_cc_guide_documents_delete.py`（删除）各调一次。

备数同 `erp_cc_guide_pending.prepare`：照 `erp_cc_operator_intake.prepare` 开一条识别完成的销售收单记录，用网页同一个入账函数
（``intake.submit_chat``，备数进程里不排写账）提交成销售单（已提交，不是草稿），再开一段干净的直写对话（同一家公司、本月）。
页面上全靠点（不调引导接口、不经模型）：点业务菜单「单据」→ 操作 → 方向「销售」→（删除时）选这张 → 复核 →（删除走允许卡）→ 结果卡。
点卡步骤用 `cowork_cc_guide_form_lib` 的通用片段（只认页面上的 data-slot，与产品无关）。
"""
from __future__ import annotations

import json

import cowork_cc_guide_form_lib as form
import erp_cc_guide_direct_lib as direct_lib
import erp_cc_operator_intake as intake_flow

USER = intake_flow.USER


def prepare(lab, manifest):
    params = intake_flow.prepare(lab, manifest, direction="sales", mode="chat")
    try:
        made = lab.python(manifest["site"], f"""
from pearnly_erp_portal.express import push
from pearnly_erp_portal.integrations import intake
frappe.set_user({USER!r})
queue, push.queue = push.queue, lambda doctype, name: None  # 备数提交不排写账（只在这个进程里）
try:
    created = intake.submit_chat({params['intake']!r})
finally:
    push.queue = queue
from pearnly_core.assistant import sessions
clean = sessions.create(project={params['company']!r}, period=frappe.utils.today()[:7], mode="chat")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": clean["id"]}}, "title", {params['tag'] + '-documents'!r})
system_no = frappe.db.get_value("ERP Sales Document", created["name"], "system_no")
frappe.db.commit()
print(json.dumps({{"document": created["name"], "clean": clean["id"], "system_no": system_no, "month": frappe.utils.today()[:7]}}))
""")
    except Exception:
        intake_flow.cleanup(lab, manifest, params)
        raise
    return {**params, **made, "doc": "S:" + made["document"], "session": made["clean"], "cases": []}


def runs(lab, manifest, params):
    return lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['clean']!r}, "business": "documents"}},
                      fields=["name", "status", "consent"], limit_page_length=3)
doc = frappe.db.get_value("ERP Sales Document", {params['document']!r}, ["status"], as_dict=True)
print(json.dumps({{"runs": runs, "doc": doc}}, default=str))
""")


def cleanup(lab, manifest, params):
    if params.get("clean"):
        lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
if frappe.db.exists("Pearnly Assistant Session", {{"session_id": {params['clean']!r}}}):
    sessions.delete({params['clean']!r})
frappe.db.commit()
print("{{}}")
""")
    direct_lib.cleanup(lab, manifest, params)


def pick(code: str, key: str = "items"):
    """停在选单那一页时：等清单从服务端取回、点这一张、下一步（ERP 实验栈取清单比 Cowork 慢，通用作答片段可能先于选项读页）。"""
    return {"name": "选单", "js": form.JS + form.HELP + f"const W={json.dumps(code)}, K={json.dumps(key)}; "
            "const qn=()=>card()?.querySelector('[data-slot=\"cc-guide-question\"]'); "
            "if(!qn()||qn().dataset.key!==K) return {skipped: true, status: card()?.dataset.status||''}; "
            "const opt=()=>qn()?.querySelector('[data-slot=\"cc-guide-option\"][data-code=\"'+W+'\"]'); "
            "await until(()=>opt(),160); const o=opt(); if(o&&!o.hasAttribute('data-checked')) o.click(); await sleep(300); "
            "const next=card()?.querySelector('[data-slot=\"cc-askq-next\"]'); const before=card()?.innerText||''; "
            "const enabled=!!next&&!next.disabled; next?.click(); await until(()=>card()&&card().innerText!==before,120); await sleep(600); "
            "return {picked: !!o, enabled, status: card()?.dataset.status||''};"}


open_conversation, open_business, answer, confirm = form.open_conversation, form.open_business, form.answer, form.confirm
