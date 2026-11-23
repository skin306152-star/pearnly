"""流程用例：ERP 版 CC **直写**对话里的销售引导（documents 源）真页面从题卡到结果——题卡逐题点 → 复核卡 → 确定 → 结果卡；
库里引导运行 done、同意来自页面、一份直写写入计划、收单记录用网页同一个入账函数提交成 ERP 单（来源 cc）并排上写账。
做法与限制（实验栈没有识别，收单记录用 run.start(doc_ids=…) 交进来）见 `erp_cc_guide_direct_lib.py`。"""
from __future__ import annotations

import erp_cc_guide_direct_lib as lib

TITLE = "ERP 版 CC 直写销售引导：题卡逐题点 → 复核卡 → 确定 → 结果卡；运行 done、同意来自页面、一份直写写入计划、提交成 ERP 单并排上写账"
BUSINESS = "sales"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, BUSINESS)


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
