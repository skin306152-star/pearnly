"""流程用例：Cowork 版 CC「公司资料」引导（直写）真页面：选「添加备注」→ 填正文 → 复核 → 确定 → 结果卡「成功 · 正文」；
这家公司的备注里真有这一条。"""
from __future__ import annotations

import cowork_cc_guide_form_lib as form
import cowork_cc_guide_form_result_lib as lib

TITLE = "Cowork 版 CC 公司资料引导（直写）：添加备注 → 复核 → 确定 → 结果卡成功带正文；公司备注里真有这一条"


def prepare(lab, manifest):
    params = form.prepare(lab, manifest, tag_prefix="gco")
    return {**params, "text": f"flow note {params['tag']}"}


def steps(params, camera_code=None):
    return [*lib.steps_head(params, "company"), lib.answer({"action": "note_add", "text": params["text"]}),
            lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, done = by.get("点菜单") or {}, by.get("确定") or {}
    return [
        {"case": "业务菜单有「公司资料」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "company" and opened.get("keys") == ["action"]},
        {"case": "确定 → 结果卡「成功」、名字是这条备注的正文（不出内部编号）", "seen": done,
         "ok": done.get("status") == "done" and done.get("result") == "entered" and "成功" in (done.get("text") or "")
         and params["text"] in (done.get("text") or "")},
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "company")
    notes = lab.python(manifest["site"], f"""
from pearnly_cowork.companies import service
frappe.set_user({lib.USER!r})
print(json.dumps([row.get("text") or row.get("content") for row in (service.detail({params['company']!r}).get("notes") or [])]))
""")
    return [lib.done_case(runs),
            {"case": "这家公司的备注里真有这一条（正文一致）", "seen": notes, "ok": params["text"] in (notes or [])}]


cleanup = form.cleanup
