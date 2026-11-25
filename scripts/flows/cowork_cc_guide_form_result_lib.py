"""C 堆（表单引导缺结果步骤，主会话 11-25 批）：Cowork 版 CC 四份表单引导的结果卡真页面流程共用片段——盘点、公司资料、任务看板、
文件夹定时任务。结果步骤都是共用层 `pearnly_core.assistant.guide.form_result`（只照确定回包画，回包没有的不显示）。

造数据、建对话同 `cowork_cc_guide_form_lib`（一条 Express 连接、同期两本账套、从目录建的公司、直写对话建在这家本月账套 T.8）；
各业务另有的备数（看板一块、文件夹任务一条）由调用方的 ``extra`` 站点代码造。页面上全靠点：点业务菜单 → 一页一题作答 →
复核 →（删除走允许卡）→ 结果卡。断言比对服务端应有状态（名字 / 文件名 / 张数与库里一致），不只看卡片出现。
"""
from __future__ import annotations

import json

import cowork_cc_guide_form_lib as form

USER = form.USER


def run_row(lab, manifest, params, business) -> list:
    return lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}, "business": {business!r}}},
                      fields=["name", "status", "consent"], limit_page_length=3)
print(json.dumps(runs, default=str))
""")


def done_case(runs) -> dict:
    return {"case": "引导运行办完（done），同意是页面上这一下", "seen": runs,
            "ok": len(runs) == 1 and runs[0].get("status") == "done" and str(runs[0].get("consent") or "").startswith("guide:")}


def chat_files(lab, manifest, params) -> list:
    """本对话里本站留存的文件名（工具 / 引导放进对话的那几份，`assistant.files.attachments`）。"""
    return lab.python(manifest["site"], f"""
from pearnly_core.assistant import files
frappe.set_user({USER!r})
frappe.local.pearnly_assistant = {{"session_id": {params['session']!r}}}
print(json.dumps([row.get("filename") for row in files.attachments()]))
""")


steps_head = lambda params, business: [form.open_conversation(params), form.open_business(business)]  # noqa: E731
answer, confirm = form.answer, form.confirm
