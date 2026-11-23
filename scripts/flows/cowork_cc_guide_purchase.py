"""流程用例：Cowork 版 CC 采购引导（documents 源，直写对话）真页面从题卡到结果——题卡逐题点 → 复核卡 →「立即录入」→ 结果卡；
库里引导运行 done、同意是页面上这一下、票推向本对话账套 T.8。做法与限制（实验栈没有识别，单据用 run.start(doc_ids=…) 交进来）
见 `cowork_cc_guide_docs_lib.py`。"""
from __future__ import annotations

import cowork_cc_guide_docs_lib as lib

TITLE = "Cowork 版 CC 采购引导：题卡逐题点 → 复核卡（一张票、合计 107）→「立即录入」→ 结果卡已交出；运行 done、同意来自页面、推向本对话 T.8"
BUSINESS = "purchase"


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, BUSINESS)


steps, judge, verify, cleanup = lib.steps, lib.judge, lib.verify, lib.cleanup
