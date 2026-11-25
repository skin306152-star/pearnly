"""流程用例：ERP 版 CC 直写对话里的「单据」引导导出（点菜单 → 操作「导出」→ 方向「销售」→ 复核 → 确定 → 结果卡带这家公司本月销售
整张列表的工作簿，网页导出同一个函数）。做法见 `erp_cc_guide_documents_lib.py`。"""
from __future__ import annotations

import erp_cc_guide_documents_lib as lib

TITLE = "ERP 版 CC「单据」引导（直写）：选导出 → 选销售 → 复核 → 确定 → 结果卡带本月销售列表工作簿；运行 done"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest)


def steps(params, camera_code=None):
    return [lib.open_conversation(params), lib.open_business("documents"),
            lib.answer({"action": "export", "direction": "sales"}), lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, answered, done = (by.get(k) or {} for k in ("点菜单", "作答", "确定"))
    picked = [page.get("did") for page in answered.get("pages") or ()]
    return [
        {"case": "业务菜单有「单据」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "documents" and opened.get("keys") == ["action"]},
        {"case": "选「导出」、选「销售」后到复核卡（导出不选单）", "seen": answered,
         "ok": picked[:2] == ["pick:export", "pick:sales"] and answered.get("status") == "reviewing"},
        {"case": "点确定 → 结果卡带本月销售列表工作簿", "seen": done,
         "ok": bool(done.get("enabled")) and done.get("status") == "done" and done.get("result") == "ready"
         and done.get("files") == [f"sales-{params['month']}.xlsx"]},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lib.runs(lab, manifest, params)
    runs = out.get("runs") or []
    return [{"case": "引导运行办完（done），同意是页面上这一下", "seen": runs,
             "ok": len(runs) == 1 and runs[0].get("status") == "done" and str(runs[0].get("consent") or "").startswith("guide:")}]


cleanup = lib.cleanup
