"""收银台不再靠"默认仓"找仓：把每台收银台**卖货的仓抄到它自己身上**。

背景：`ERP Warehouse.is_default` 这一位本轮删掉（用户 2026-09-24："不要做默认仓，依赖全部删掉"），
而收银台以前是"自己没配仓 → 拿这家公司的默认仓"。标记一删那句兜底就没了，
所以要在**标记还在的时候**（本 patch 放在 `[pre_model_sync]`，跑在模型同步之前）
把当时**实际生效**的那个仓写成收银台上的显式值：

· 优先抄这家公司 `is_default = 1` 的那个仓（= 以前真正生效的那个，行为不变）；
· 没有标记（历史数据）就抄这家公司**最早**的那个仓；
· 一个仓都没有的公司跳过 —— 那种站点本来也卖不了货，之后会在开班/落单时明确报
  "这台收银台还没配仓库"，而不是悄悄落到某个仓上。

已经配了仓的收银台**不动**（只补空的）。
"""

import frappe


def execute():
    if not frappe.db.has_column("ERP Warehouse", "is_default"):
        # 已经同步过（标记列没了）：没有可抄的"当时生效值"，不改任何东西。
        return
    for company in frappe.get_all("ERP Company", pluck="name"):
        place = frappe.db.get_value("ERP Warehouse", {"company": company, "is_default": 1}, "name") \
            or frappe.db.get_value("ERP Warehouse", {"company": company}, "name",
                                   order_by="creation asc")
        if not place:
            continue
        empty = frappe.db.sql(
            """select name from `tabERP POS Terminal`
               where company = %s and (warehouse is null or warehouse = '')""",
            (company,), pluck=True)
        for terminal in empty:
            frappe.db.set_value("ERP POS Terminal", terminal, "warehouse", place,
                                update_modified=False)
