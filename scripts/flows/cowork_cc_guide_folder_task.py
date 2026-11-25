"""流程用例：Cowork 版 CC「定时任务」引导（直写）真页面：选「删除」→ 选这家公司的那条任务 → 复核 → 允许卡「允许」→ 结果卡「成功」、
已删除 1 条；任务表里这条真没了。备数：在本对话公司（连接 + 账套 T.8）下建一条定时任务（只经本域写 `folder_intake.access.write`）。
「立即运行」在实验栈只能验到「已排上」（没有 CC 桌面版来领），不在本流程里。"""
from __future__ import annotations

import cowork_cc_guide_form_lib as form
import cc_conversation_ledger as ledger
import cowork_cc_guide_form_result_lib as lib

TITLE = "Cowork 版 CC 定时任务引导（直写）：删除 → 选任务 → 复核 → 允许 → 结果卡已删除 1 条；任务表里这条真没了"
RULE = "Cowork Folder Intake Rule"

EXTRA = """
from pearnly_cowork.folder_intake import access as folder_access
frappe.set_user(%(user)r)
rule = frappe.get_doc({"doctype": %(rule)r, "title": "Flow folder " + %(tag)r, "workspace": ws, "connection": conn["name"], "rule_owner": %(user)r, "enabled": 1,
    "account_set_key": %(book)r, "account_label": "Flow T8", "direction": "purchase", "posting_kind": "service",
    "root_id": "flowroot", "folder": "Invoices\\\\" + %(tag)r, "run_day": 1, "run_time": "22:00", "run_request": 0})
folder_access.write(rule)
frappe.db.commit()
extra = {"rule": rule.name, "rule_title": rule.title}
"""


def prepare(lab, manifest):
    import uuid

    tag = "gft" + uuid.uuid4().hex[:6]
    return form.prepare(lab, manifest, extra=EXTRA % {"user": lib.USER, "rule": RULE, "tag": tag, "book": ledger.T8},
                        tag_prefix="gft")


def steps(params, camera_code=None):
    return [*lib.steps_head(params, "folder_task"), lib.answer({"action": "delete", "tasks": params["rule"]}),
            lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, answered, done = (by.get(k) or {} for k in ("点菜单", "作答", "确定"))
    picked = [page.get("did") for page in answered.get("pages") or ()]
    return [
        {"case": "业务菜单有「定时任务」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "folder_task" and opened.get("keys") == ["action"]},
        {"case": "选「删除」、清单里有这家公司的这条任务、选上后到复核卡", "seen": answered,
         "ok": picked[:2] == ["pick:delete", "pick:" + params["rule"]] and answered.get("status") == "reviewing"},
        {"case": "允许卡点「允许」→ 结果卡「成功」、已删除 1 条", "seen": done,
         "ok": bool(done.get("enabled")) and done.get("status") == "done" and done.get("result") == "entered"
         and "已删除 1 条" in (done.get("text") or "")},
        lib.no_internal_case(done),
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "folder_task")
    # lab.python 只认最后一行 JSON 对象 / 数组（裸 false 会被当成没有输出）
    left = lab.python(manifest["site"], f'print(json.dumps({{"left": bool(frappe.db.exists({RULE!r}, {params["rule"]!r}))}}))')
    return [lib.done_case(runs), {"case": "任务表里这条真删了（删了 1 条，与结果卡一致）", "seen": left, "ok": left == {"left": False}}]


def cleanup(lab, manifest, params):
    if params.get("rule"):
        lab.python(manifest["site"], f"""
if frappe.db.exists({RULE!r}, {params['rule']!r}):
    frappe.db.delete({RULE!r}, {{"name": {params['rule']!r}}})
    frappe.db.commit()
print("{{}}")
""")
    form.cleanup(lab, manifest, params)
