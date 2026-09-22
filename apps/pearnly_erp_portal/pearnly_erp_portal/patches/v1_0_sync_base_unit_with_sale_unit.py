"""把"库存单位与售卖单位不一致、又没有换算行"的商品同步成售卖单位。

为什么：库存的记数单位改成**库存单位**（旧线上 `services/inventory/ledger.py` 就是折算成
`base_unit` 落账，`unit` 只是默认售卖单位）。而这之前建出来的商品，`base_unit` 拿的是
DocType 默认值"ชิ้น"，`unit` 却是商户填的售卖单位 —— 那些商品的流水本来就是按售卖单位
记的（一次卖出记 1 个单位），于是把它们同步成售卖单位，历史数量的含义不变，
收银也就不会再撞上"这个单位没在商品上定义"。

两者不同、且**单位表里确实有那一行换算**的（商户自己分过家的），一律不碰。
"""

import frappe


def execute():
    rows = frappe.db.sql(
        """
        SELECT p.name, p.unit, p.base_unit
        FROM `tabERP Product` p
        WHERE IFNULL(p.unit, '') != '' AND IFNULL(p.base_unit, '') != IFNULL(p.unit, '')
        """,
        as_dict=True,
    )
    fixed = 0
    for row in rows:
        has_row = frappe.db.exists(
            "ERP Product Unit", {"parent": row.name, "unit_name": row.unit}
        )
        if has_row:
            continue
        frappe.db.set_value("ERP Product", row.name, "base_unit", row.unit,
                            update_modified=False)
        fixed += 1
    if fixed:
        frappe.db.commit()
        print(f"[pearnly_erp_portal] base_unit 同步成售卖单位的商品：{fixed}")
