"""把仓库表上 `is_default` 那一列真正删掉。

为什么还要一条 patch：Frappe 的模型同步**只会加/改列，不会替你删列** ——
把字段从 DocType 的 JSON 里拿掉之后，库里的 `is_default` 会一直留着（谁都不读了，
但"依赖全部删掉"这句话就没落地）。值本身在上一条 patch
（`v1_5_terminal_warehouse`，跑在模型同步**之前**）里已经抄到收银台上了，这里只是清列。
"""

import frappe


def execute():
    if frappe.db.has_column("ERP Warehouse", "is_default"):
        frappe.db.sql_ddl("ALTER TABLE `tabERP Warehouse` DROP COLUMN `is_default`")
