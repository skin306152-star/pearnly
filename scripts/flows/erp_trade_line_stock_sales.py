"""流程用例：ERP 销售单按行选出库（11-06 按行选物料与库存）。步骤与判据同 `erp_trade_line_stock.py`（采购），这里是销售那一份：
行上是「出库」勾选，认不到物料、勾了出库的那行随单新建物料并出库（评估 12 节 #2，与 LINE 一致），运费那行只记账、不出库。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import erp_trade_line_stock as base  # noqa: E402

TITLE = "ERP 销售：明细按行勾出库、批量编辑设整张，认不到的出库行随单新建"


def prepare(lab, manifest):
    return base.prepare(lab, manifest, direction="sales")


steps, judge, verify = base.steps, base.judge, base.verify
