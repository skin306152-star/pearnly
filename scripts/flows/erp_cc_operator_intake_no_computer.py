"""流程用例：同 `erp_cc_operator_intake`（采购票），发单人自己一台登录过 CC 的电脑都没有：开始后 CC 被告知「CC 桌面版未连接」、
不说「已交给电脑」，对话里的代录进度写明未连接（11-16 H 组：只写「等待电脑」、CC 说已交给 CC Desktop）。"""
from __future__ import annotations

import erp_cc_operator_intake as purchase
from erp_cc_operator_intake import cleanup, judge, steps, verify  # noqa: F401  同一套步骤与断言（多一步、两条断言）

TITLE = "ERP CC 代录放票、发单人没有自己的电脑：开始后 CC 说未连接、对话进度写明「CC 桌面版未连接」（11-16 H 组）"


def prepare(lab, manifest):
    return purchase.prepare(lab, manifest, no_computer=True)
