"""单据打印（Frappe 原生 Print Format + `/printview` / PDF）：领料单、收货单照岳父样板 1:1（打印评估 10-25，主会话 10-26 审定）。

- `contracts`：纯规则（每页 10 行分页、数量 / 金额 / 日期写法、用途与仓库勾选），不依赖框架；
- `context`：按单据取打印要的数（抬头按单据所属公司、明细、供应商、作废水印），注册成 Jinja 方法 `pearnly_print_context`；
- `templates/`：版面（Jinja），标准 Print Format 的 `.html` 只是 include 这里的模板。
"""
