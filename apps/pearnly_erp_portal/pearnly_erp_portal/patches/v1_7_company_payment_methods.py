"""给已有公司补上"收几种钱"那几行。

`ERP Company` 新加了 `payment_methods` 子表（建公司时一次配齐用）。老公司没有这些行，
而收银**默认是四种全开**（`masters/company.POS_PAYMENT_METHODS`）—— 空表在读取时也会按
"四种全开"解释（`payment_methods_of` 有兜底），这里只是把那份默认**落成显式的行**，
让公司页那一节能直接改。
"""

import frappe

from pearnly_erp_portal.masters.company import POS_PAYMENT_METHODS


def execute():
    for name in frappe.get_all("ERP Company", pluck="name"):
        if frappe.db.exists("ERP Company Payment Method", {"parent": name}):
            continue
        doc = frappe.get_doc("ERP Company", name)
        for key in POS_PAYMENT_METHODS:
            doc.append("payment_methods", {"method": key, "label": key})
        doc.save(ignore_permissions=True)
