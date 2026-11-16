"""未收款的销售单不该带收款方式（11-16 验收首跑）：「收款方式」原来是没有空选项的 Select，开单传空时 Frappe 补成第一项 cash，
未收款的单也存成了「现金」。字段已加空选项（不再补），这里把已有的改回空。

判据只认「未收款且已收金额为 0」——开单与改收款状态的服务端（`sales.entry`）对这种单一律写空，所以它们的 cash 只可能是补出来的；
部分收款、已收款的单一个不动（真收了现金的不改）。重跑无变化。
"""
import frappe

DOCTYPE = "ERP Sales Document"


def execute():
    if not frappe.db.has_column(DOCTYPE, "payment_method"):
        return
    frappe.db.sql(
        f"""update `tab{DOCTYPE}` set payment_method = null
            where payment_status = 'unpaid' and ifnull(paid_amount, 0) = 0 and payment_method = 'cash'""")
