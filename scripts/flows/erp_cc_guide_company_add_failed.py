"""流程用例：ERP 版 CC「添加公司」结果卡的同步失败路径（主会话 11-28：同步中之后失败了，结果卡上要有事实状态，不能显示成没事）。
备数同 `erp_cc_guide_company_add`；这一次在站点里用同一个引导入口（`run.start` / `answer` / `confirm`）把添加做完，再把这家的主档同步
记成失败（公司页同一个字段）；页面打开对话读结果卡：「成功 · 公司名 · 同步失败」，不再写同步中、不再等。"""
from __future__ import annotations

import erp_cc_guide_company_add as base
import erp_cc_guide_form_lib as lib

TITLE = "ERP 版 CC 添加公司结果卡：添加后主档同步失败 → 打开对话，结果卡写成功 · 公司名 · 同步失败（不再写同步中）"


def prepare(lab, manifest):
    params = base.seed(lab, manifest, tag_prefix="eaf")
    made = lab.python(manifest["site"], f"""
from pearnly_core.assistant.guide import run
frappe.set_user({lib.USER!r})
found = run.start({params['session']!r}, "company_add")
found = run.answer(found["run"], {{"companies": [{params['identity']!r}]}})
run.confirm(found["run"], found["version"])
frappe.db.commit()
print(json.dumps({{"company_id": frappe.db.get_value("ERP Company", {{"tax_id": {params['tax']!r}}}, "name")}}))
""")
    params = {**params, **made}
    # 主档同步记成失败：公司设置一律经 lab.set_fields 改（格结束时统一还原；这家公司 cleanup 时整个删掉）
    lab.set_fields(manifest["site"], "ERP Company", params["company_id"], {"express_sync_state": "failed"})
    return params


def steps(params, camera_code=None):
    return [lib.form.open_conversation(params), lib.form.read_result()]


def judge(params, results, camera_code=None):
    done = next((r.get("value") or {} for r in results if r.get("step") == "读结果"), {})
    text = done.get("text") or ""
    return [
        {"case": "结果卡「成功」、公司名、「同步失败」，不写同步中", "seen": done,
         "ok": done.get("result") == "entered" and "成功" in text and params["name"] in text and "同步失败" in text
         and "同步中" not in text},
        lib.no_internal_case(done),
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "company_add")
    row = base.company_row(lab, manifest, params)
    return [{"case": "引导运行办完", "seen": runs, "ok": len(runs) == 1 and runs[0].get("status") == "done"},
            {"case": "这家公司建了、同步记为失败（备数）", "seen": row, "ok": row.get("express_sync_state") == "failed"}]


cleanup = base.cleanup
