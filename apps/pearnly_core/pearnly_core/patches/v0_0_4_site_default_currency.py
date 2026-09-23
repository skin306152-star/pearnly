"""给站点补上全局默认货币（框架的 `format_currency` 依赖它）。

框架取货币符号那条路是
`format_currency(v, currency)` → `get_currency_symbol(currency)`：**不传币种时**它读
`frappe.boot.sysdefaults.currency`，而这个键来自 `DefaultValue` 表（`frappe.defaults.get_defaults()`）。

我们自己供的站点在初始化时没有写这一行（`erpnext` 那条安装流程会写，我们不装 `erpnext`），
于是链子断在这里：

    sysdefaults.currency 缺失 → get_currency_symbol(undefined)
    → frappe.model.get_value(":Currency", undefined, "symbol") 命中缓存里的**第一条**
    → 页面上的金额显示成 `د.إ 100.00`（本机实测就是这个）。

币种取值按"站点实际在用的那一个"来，顺序是 System Settings 的单值 → 采购设置里的记账本位币 →
没辙时的 THB。已经有了就一个字不动（客户自己配过的站点不受影响）。
"""

import frappe

FALLBACK = "THB"


def execute():
    if frappe.db.get_default("currency"):
        return
    frappe.defaults.set_global_default("currency", _currency_in_use())


def _currency_in_use() -> str:
    currency = frappe.db.get_single_value("System Settings", "currency")
    if currency:
        return currency
    if frappe.db.exists("DocType", "ERP Purchase Settings"):
        currency = frappe.db.get_single_value("ERP Purchase Settings", "base_currency")
        if currency:
            return currency
    return FALLBACK
