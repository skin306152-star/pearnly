"""流程用例：ERP 版 CC 直写对话里的「单据」引导删除（点菜单 → 操作「删除」→ 方向「销售」→ 选这张已提交的单 → 复核 → 允许卡「允许」→
结果卡「已跳过」带网页同一句「只能删除草稿单据」；单子还在）。同网页列表「删除」只删草稿。做法见 `erp_cc_guide_documents_lib.py`。"""
from __future__ import annotations

import erp_cc_guide_documents_lib as lib

TITLE = "ERP 版 CC「单据」引导（直写）：选删除 → 选销售 → 选已提交的单 → 复核 → 允许 → 结果卡已跳过、只能删除草稿单据；单子还在"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest)


def steps(params, camera_code=None):
    return [lib.open_conversation(params), lib.open_business("documents"),
            lib.answer({"action": "delete", "direction": "sales"}), lib.pick(params["doc"]), lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, answered, chose, done = (by.get(k) or {} for k in ("点菜单", "作答", "选单", "确定"))
    picked = [page.get("did") for page in answered.get("pages") or ()]
    # 清单取得快时通用作答片段已点了唯一那张（first:<码>），否则「选单」那一步等清单取回再点
    took = picked[2:3] == ["first:" + params["doc"]] or (bool(chose.get("picked")) and chose.get("status") == "reviewing")
    return [
        {"case": "业务菜单有「单据」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "documents" and opened.get("keys") == ["action"]},
        {"case": "选「删除」「销售」、清单里有这张（本公司本月）、选上后到复核卡", "seen": {"answered": answered, "pick": chose},
         "ok": picked[:2] == ["pick:delete", "pick:sales"] and took
         and (chose.get("status") or answered.get("status")) == "reviewing"},
        {"case": "允许卡点「允许」→ 结果卡已跳过，带「只能删除草稿单据」", "seen": done,
         "ok": bool(done.get("enabled")) and done.get("status") == "done" and done.get("result") == "held"
         and "只能删除草稿单据" in (done.get("text") or "")},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lib.runs(lab, manifest, params)
    runs, doc = out.get("runs") or [], out.get("doc") or {}
    return [
        {"case": "引导运行办完（done），同意是页面上这一下", "seen": runs,
         "ok": len(runs) == 1 and runs[0].get("status") == "done" and str(runs[0].get("consent") or "").startswith("guide:")},
        {"case": "已提交的单没删（同网页只删草稿）", "seen": doc, "ok": doc.get("status") == "submitted"},
    ]


cleanup = lib.cleanup
