"""流程用例：同 `erp_cc_operator_intake`（采购票），票上的物料行认不出（新物料）：开始前卡片的物料题有「新建」（推荐、排第一），
点它提交记下、开录后提交成 ERP 采购单（11-20 线上 H3：物料题只剩「其他」打字，提交 remember 报 417「请求无效」）。"""
from __future__ import annotations

import erp_cc_operator_intake as purchase
from erp_cc_operator_intake import cleanup, judge, steps, verify  # noqa: F401  同一套步骤与断言（多两条断言）

TITLE = "ERP CC 代录放票、物料行认不出：开始前卡片物料题点「新建」提交记下、开录提交成 ERP 采购单（11-20 线上 H3）"


def prepare(lab, manifest):
    return purchase.prepare(lab, manifest, new_item=True)
