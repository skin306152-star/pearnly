"""流程用例：ERP 版 CC「公司资料」引导（直写）真页面：选「添加备注」→ 填正文 → 复核 → 确定 → 结果卡「成功 · 正文」；
这家公司的知识库里真有这一条。"""
from __future__ import annotations

import erp_cc_guide_form_lib as lib

TITLE = "ERP 版 CC 公司资料引导（直写）：添加备注 → 复核 → 确定 → 结果卡成功带正文；公司知识库里真有这一条"


def prepare(lab, manifest):
    params = lib.prepare(lab, manifest, tag_prefix="eco")
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
        {"case": "确定 → 结果卡「成功」、名字是这条备注的正文（不出笔记编号）", "seen": done,
         "ok": done.get("status") == "done" and done.get("result") == "entered" and "成功" in (done.get("text") or "")
         and params["text"] in (done.get("text") or "")},
        lib.no_internal_case(done),
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "company")
    notes = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company_notes
print(json.dumps([row.get("text") for row in company_notes.listing({params['company']!r})], default=str))
""")
    return [lib.done_case(runs),
            {"case": "这家公司的知识库里真有这一条（正文一致）", "seen": notes, "ok": params["text"] in (notes or [])}]


def cleanup(lab, manifest, params):
    lib.cleanup(lab, manifest, params, extra="""
for name in frappe.get_all("ERP Company Note", filters={"company": params.get("company") or "-", "text": params.get("text") or "-"},
                           pluck="name", limit_page_length=5):
    frappe.delete_doc("ERP Company Note", name, force=True, ignore_permissions=True)
""")
