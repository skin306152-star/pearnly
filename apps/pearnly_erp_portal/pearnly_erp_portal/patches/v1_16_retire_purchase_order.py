"""采购单不再有「采购订单」（用户 2026-10-28：「采购订单删掉吧」）：采购单就是「已付款 + 货已到」的进货单，没有先下单流程。

已有的采购订单单改成 `purchasing.contracts.RETIRED_KINDS` 给的那一种（与旧调用方传 purchase_order 时的落地口径同一处），
别的数据不动；打印格式「Pearnly Purchase Order」随源码目录删掉，站点上同步过的记录一并删。10-28 生产四个 ERP 站点
（mrerp、testerp、polymer、gize）只读统计采购订单都是 0 张。重跑无副作用。
"""
import frappe

from pearnly_erp_portal.purchasing import contracts

DOCTYPE = "Purchase Document"
FORMAT = "Pearnly Purchase Order"


def execute():
    if frappe.db.table_exists(DOCTYPE):
        for old, new in contracts.RETIRED_KINDS.items():
            frappe.db.sql(f"update `tab{DOCTYPE}` set doc_kind=%s where doc_kind=%s", (new, old))
    if frappe.db.exists("Print Format", FORMAT):
        frappe.delete_doc("Print Format", FORMAT, force=True, ignore_permissions=True)
