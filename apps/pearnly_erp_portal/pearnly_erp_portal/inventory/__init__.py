"""库存域：仓库、出入库单据、不可变流水、物化结存、收发存报表。

`contracts` 是纯规则（滚动加权成本、单位换算、差异判据），不依赖 Frappe；`stock` 管
流水与结存，`documents` 管入库/出库单据，`warehouses` 管仓库，`report` 出收发存与
进销存报表，`openings` 管期初。采购确认后的自动入库在 `documents` 里接到采购域。
"""
