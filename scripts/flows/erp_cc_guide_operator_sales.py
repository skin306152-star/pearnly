"""流程用例：ERP 版 CC 代录对话里的销售引导（documents 源）真页面从题卡到结果——题卡逐题点 → 复核卡 → 确定 → 结果卡交进代录；
库里引导运行 done、同意来自页面、一份写入计划、票交进代录任务一次。做法与限制（实验栈没有识别，收单记录用 run.start(doc_ids=…)
交进来）见 `erp_cc_guide_operator_lib.py`。"""
from __future__ import annotations

import erp_cc_guide_operator_lib as lib

TITLE = "ERP 版 CC 代录销售引导：题卡逐题点 → 复核卡 → 确定 → 结果卡交进代录；运行 done、同意来自页面、一份写入计划、票进代录任务一次"
BUSINESS = "sales"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, BUSINESS)


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
