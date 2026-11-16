"""流程用例：同 `erp_cc_operator_intake`，放进来的是销售票（H4）。"""
from __future__ import annotations

import erp_cc_operator_intake as purchase
from erp_cc_operator_intake import cleanup, judge, steps, verify  # noqa: F401  同一套步骤与断言

TITLE = "ERP CC 代录放进来的销售票：开始前卡片作答记下、写入计划列入、开始后提交成 ERP 销售单并交进写账（H4）"


def prepare(lab, manifest):
    return purchase.prepare(lab, manifest, direction="sales")
