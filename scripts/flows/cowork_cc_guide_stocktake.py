"""流程用例：Cowork 版 CC「盘点」引导（直写）真页面：选「下载模板」→ 确定 → 结果卡「成功」带模板文件；文件真放进了这段对话（服务端同名）。"""
from __future__ import annotations

import cowork_cc_guide_form_lib as form
import cowork_cc_guide_form_result_lib as lib

TITLE = "Cowork 版 CC 盘点引导（直写）：选下载模板 → 确定 → 结果卡成功带模板文件；对话里真有这份文件"


def prepare(lab, manifest):
    return form.prepare(lab, manifest, tag_prefix="gst")


def steps(params, camera_code=None):
    return [*lib.steps_head(params, "stocktake"), lib.answer({"action": "template"}), lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, done = by.get("点菜单") or {}, by.get("确定") or {}
    return [
        {"case": "业务菜单有「盘点」，点了出引导卡、先问操作", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "stocktake" and opened.get("keys") == ["action"]},
        {"case": "确定 → 结果卡「成功」、一份文件", "seen": done,
         "ok": done.get("status") == "done" and done.get("result") == "entered" and "成功" in (done.get("text") or "")
         and done.get("files") == ["stocktake-template.xlsx"]},
        lib.no_internal_case(done),
    ]


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "stocktake")
    names = lib.chat_files(lab, manifest, params)
    return [lib.done_case(runs),
            {"case": "结果卡上的文件名就是服务端放进这段对话的那份", "seen": {"server": names},
             "ok": names == ["stocktake-template.xlsx"]}]


cleanup = form.cleanup
