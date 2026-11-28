"""C2（ERP 表单引导补结果步骤，主会话 11-25 批）：ERP 版 CC 表单引导结果卡真页面流程的共用片段——盘点、公司资料、收发存报表。
结果步骤都是共用层 `pearnly_core.assistant.guide.form_result`（只照确定回包画，回包没有的不显示）。

造数据：在实验室 ERP 站点的那家公司（`erp_lib.company`）开一段干净的直写对话（本月）；各业务另有的备数由调用方的 ``extra`` 站点代码造。
页面上全靠点（同 Cowork 那套点法 `cowork_cc_guide_form_lib`：点业务菜单 → 一页一题作答 → 复核 → 确定 → 结果卡）。
断言比对服务端应有状态（名字 / 文件名与库里一致），不只看卡片出现。
"""
from __future__ import annotations

import json
import uuid

import cowork_cc_guide_form_lib as form
import cowork_cc_guide_form_result_lib as result_lib
import erp_lib

USER = "admin@erp.regression"
steps_head = result_lib.steps_head
answer, confirm, no_internal_case = form.answer, form.confirm, result_lib.no_internal_case


def prepare(lab, manifest, extra: str = "", tag_prefix: str = "eg"):
    """开对话；``extra`` 是接在后面的站点代码（能用 ``company`` / ``sid``），给 ``extra`` 字典赋值并进参数。"""
    tag = tag_prefix + uuid.uuid4().hex[:6]
    company = erp_lib.company(manifest)
    out = lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.db.set_value("User", {USER!r}, "language", "zh")
frappe.set_user({USER!r})
company = {company!r}
made = sessions.create(project=company, period=frappe.utils.today()[:7], mode="chat")
sid = made["id"]
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": sid}}, "title", {tag!r})
frappe.db.commit()
extra = {{}}
""" + extra + """
frappe.db.commit()
print(json.dumps({"company": company, "session": sid, **extra}, default=str))
""")
    return {**out, "tag": tag}


def run_row(lab, manifest, params, business) -> list:
    return result_lib.run_row(lab, manifest, params, business)


done_case = result_lib.done_case


def chat_files(lab, manifest, params) -> list:
    return lab.python(manifest["site"], f"""
from pearnly_core.assistant import files
frappe.set_user({USER!r})
frappe.local.pearnly_assistant = {{"session_id": {params['session']!r}}}
print(json.dumps([row.get("filename") for row in files.attachments()]))
""")


def cleanup(lab, manifest, params, extra: str = ""):
    """删对话（连引导运行）；``extra`` 删本流程建的业务数据。成功、失败、超时都跑。"""
    lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
params = json.loads({json.dumps(json.dumps(params, default=str))})
""" + extra + f"""
if params.get("session") and frappe.db.exists("Pearnly Assistant Session", {{"session_id": params["session"]}}):
    sessions.delete(params["session"])
frappe.db.commit()
print("{{}}")
""")
