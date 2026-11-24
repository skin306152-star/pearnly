"""流程用例：ERP 版 CC 代录对话里交进来一张**票面不是本对话月份**的收单记录（P4 11-24）。原来确定时不问就交；现在题卡最前面先问
「票面 X · 本对话 Y」——放进本次代录照录（按票面日期）/ 本次不录（不预选）。点「照录」→ 复核 → 确定 → 结果卡交进代录任务。

造数据同 `erp_cc_guide_operator_lib`（`erp_cc_operator_intake.prepare`），只把收单记录的票面日期改成上个月（同一会计年度，本对话账套
覆盖它）。页面点法同代录那条（有推荐点推荐，没有点第一项：别月题的第一项是「照录」）。"""
from __future__ import annotations

import json

import erp_cc_guide_operator_lib as lib
import erp_cc_operator_intake as intake_flow

TITLE = "ERP 版 CC 代录对话里的别月票：题卡先问「票面 X · 本对话 Y」→ 点照录 → 复核 → 确定 → 交进代录任务一次"
USER = lib.USER


def prepare(lab, manifest):
    params = intake_flow.prepare(lab, manifest, direction="purchase")
    try:
        moved = lab.python(manifest["site"], f"""
row = frappe.get_doc("ERP LINE Intake", {params['intake']!r})
result = json.loads(row.result or "{{}}")
day = str(frappe.utils.add_months(frappe.utils.getdate(frappe.utils.today()).replace(day=1), -1))
result["doc_date"] = day
frappe.db.set_value("ERP LINE Intake", row.name, "result", json.dumps(result, ensure_ascii=False))
frappe.db.commit()
from pearnly_core.assistant.guide import run
frappe.set_user({USER!r})
try:
    found = run.start({params['session']!r}, "purchase", doc_ids=[{params['doc_id']!r}], at="asking")
    frappe.db.commit()
    started = {{"run": found["run"], "status": found["status"], "stage": found.get("stage") or ""}}
except Exception as error:
    frappe.db.rollback()
    started = {{"run": "", "error": repr(error)[:300]}}
print(json.dumps({{"day": day, "started": started}}, default=str))
""")
    except Exception:
        intake_flow.cleanup(lab, manifest, params)
        raise
    started = moved.get("started") or {}
    return {**params, "business": "purchase", "day": moved.get("day") or "", "run": started.get("run") or "", "started": started}


steps = lib.steps


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    asked, done = by.get("题卡") or {}, by.get("确定") or {}
    first = (asked.get("asked") or [{}])[0]
    rows = done.get("rows") or []
    month = params["day"][:7]
    return [
        {"case": f"题卡第一题是别月票那题：票面 {month} · 本对话（标题带两个月份），第一项「放进本次代录照录」被点上",
         "seen": {"started": params.get("started"), **asked},
         "ok": month in (first.get("title") or "") and "本对话" in (first.get("title") or "")
         and "照录" in (first.get("picked") or "") and not asked.get("stuck")},
        {"case": "点确定：结果卡列这一张、已交进代录（不再是「请求无效」）", "seen": done,
         "ok": bool(done.get("clicked")) and done.get("status") == "done" and [r.get("doc") for r in rows] == [params["doc_id"]]
         and all(r.get("status") not in ("failed", "withdrawn", "") for r in rows)},
    ]


def verify(lab, manifest, params, camera_code=None):
    return lib.verify(lab, manifest, params, camera_code)


cleanup = lib.cleanup
