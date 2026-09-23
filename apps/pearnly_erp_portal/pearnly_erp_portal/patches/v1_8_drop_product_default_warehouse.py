"""把商品表上 `default_warehouse` 那一列真正删掉。

为什么还要一条 patch：Frappe 的模型同步**只会加/改列，不会替你删列** —— 字段从 DocType
的 JSON 里拿掉之后，库里的 `default_warehouse` 会一直留着（谁都不读了，但"依赖全部删掉"
这句话就没落地）。

值本身不需要搬运：进货那一侧已经改成认**收银台上配的仓**（`warehouses.receiving` →
`masters.company.selling_warehouse`），商品上那个默认仓留着只会让两边各说各话
（用户 2026-09-24："退货退款作废后的物料仓库数量、金额，以及收发存报表，数据要关联上"，
结论是"进货就进卖货那个仓"）。
"""

import frappe


def execute():
    if frappe.db.has_column("ERP Product", "default_warehouse"):
        frappe.db.sql_ddl("ALTER TABLE `tabERP Product` DROP COLUMN `default_warehouse`")
