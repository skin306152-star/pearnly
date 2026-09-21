"""采购域：单据生命周期、供应商与采购设置。

`contracts` 是纯规则（金额、流转、去重判据），不依赖 Frappe；`service` 负责单据、
`suppliers` 负责供应商、`settings` 负责采购设置；`api` 只做参数解析。
"""
