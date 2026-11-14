"""主档上的只读来源 ``ledger``（公司接入第 3 批，11-14）：已经从 Express 同步来的客户 / 供应商 / 物料 / 仓库回填 ``express``。

只用一条 UPDATE 按「带账本编码」回填，不逐条保存（不生成修改记录、不改 modified）；回填后落库比对时这一列已一致，不会整本重写。
此前 MR.ERP 主档从不落进 ERP，所以带账本编码的都是 Express 的。
"""

import frappe

DOCTYPES = ("ERP Customer", "ERP Supplier", "ERP Product", "ERP Warehouse")


def execute():
    for doctype in DOCTYPES:
        if not frappe.db.has_column(doctype, "ledger"):
            frappe.reload_doc("pearnly_erp_portal", "doctype", frappe.scrub(doctype))
        frappe.db.sql(f"""update `tab{doctype}` set ledger = 'express'
            where ifnull(express_code, '') != '' and ifnull(ledger, '') = ''""")
