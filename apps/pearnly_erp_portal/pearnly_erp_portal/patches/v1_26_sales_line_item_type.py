"""销售明细加「入库 / 出库」类型 `item_type`（11-06 按行选物料与库存，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 4.3）。

存量行按当时实际的结果回填，不动任何库存：这一行有物料、且这张单过过出库（有它的出库流水）→ ``goods``；其余 → ``service``
（没物料的行、报价单、草稿从来不出库）。上线前这张单上有物料的行一律出库，所以按「这张单有这件物料的流水」判就是当时的结果。
"""
import frappe

DOCTYPE = "ERP Sales Document"


def execute():
    frappe.reload_doc("pearnly_erp_portal", "doctype", "erp_sales_document_line")
    frappe.db.sql("""update `tabERP Sales Document Line` set item_type = 'service'
        where parenttype = %s""", (DOCTYPE,))
    frappe.db.sql("""update `tabERP Sales Document Line` line set line.item_type = 'goods'
        where line.parenttype = %s and ifnull(line.product, '') != ''
          and exists (select 1 from `tabERP Stock Movement` move
                      where move.ref_type = %s and move.ref_id = line.parent and move.product = line.product)""", (DOCTYPE, DOCTYPE))
