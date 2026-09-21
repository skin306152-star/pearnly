"""收银域：收银台、班次与销售。

`contracts` 是纯规则（付款结算与找零、销售金额、班次现金），不依赖 Frappe；`terminals`
管收银台，`shifts` 管开班/交班，`sales` 管落单与退货，`api` 只做参数解析。
"""
