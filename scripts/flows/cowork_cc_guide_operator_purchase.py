"""流程用例：Cowork 版 CC 代录对话里的采购引导（documents 源）真页面从题卡到结果——题卡逐题点 → 复核卡 → 确定 → 结果卡交进代录；
库里引导运行 done、同意来自页面、一份写入计划、票交进代录任务一次。做法与限制（实验栈没有识别，单据用 run.start(doc_ids=…) 交进来）
见 `cowork_cc_guide_operator_lib.py`。"""
from __future__ import annotations

import cowork_cc_guide_operator_lib as lib

TITLE = "Cowork 版 CC 代录采购引导：题卡逐题点 → 复核卡（一张票、合计 107）→ 确定 → 结果卡交进代录；运行 done、同意来自页面、一份写入计划、票进代录任务一次"
BUSINESS = "purchase"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, BUSINESS)


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
