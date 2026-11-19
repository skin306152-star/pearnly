"""库存只到「物料 × 仓库」（11-19 取消仓位，只有盘点保留仓位记录）：存量数据收敛。

1. 流水、期初、结存、按台的台与台流水、行物料记忆、单据头 / 行、物料默认仓位上的仓位清空（盘点的表不动）；
2. 带过仓位的每个「物料 × 仓库」按流水重放成一行 ``BAL-物料-仓库``（`stock.replay_balance`，与库存引擎同一套滚存）；
3. 删掉旧的带仓位的结存行（``BAL-<sha>``）。

数量：结存 = 流水之和，同一个「物料 × 仓库」各仓位的流水合在一起重放，合计数量与迁移前各仓位之和相等（同仓不同仓位之间的
挪动正负抵消）；成本按合并后的流水重算加权平均。可重跑：第二次没有带仓位的行、也没有名字不对的结存行，什么都不动。
DocType 里删掉的列 Frappe 迁移不删库列，这里按列还在不在判断（新装站点没有这些列）。
"""

import frappe

# 非盘点的表上的仓位列（DocType 里已删；旧站点库里还在）。盘点（ERP Stock Count Item / Entry）与仓位清单不动。
COLUMNS = (
    ("ERP Stock Movement", "location"),
    ("ERP Stock Opening", "location"),
    ("ERP Stock Balance", "location"),
    ("ERP Serial No", "location"),
    ("ERP Serial Movement", "location"),
    ("ERP Line Item Memory", "location"),
    ("ERP Stock Entry", "location"),
    ("ERP Stock Entry", "to_location"),
    ("ERP Stock Entry Line", "location"),
    ("ERP Stock Entry Line", "to_location"),
    ("Purchase Document", "location"),
    ("Purchase Line", "location"),
    ("ERP Sales Document", "location"),
    ("ERP Sales Document Line", "location"),
    ("ERP Sale Line", "location"),
    ("ERP Product", "default_location"),
)


def merge_targets(balances) -> list:
    """要重放的「物料 × 仓库」：带仓位的结存行，或名字不是 ``BAL-物料-仓库`` 的（旧的 ``BAL-<sha>``）。
    ``balances``：``[{name, product, warehouse, location}]``。纯函数，离线测试用。"""
    from pearnly_erp_portal.inventory import contracts

    wanted = set()
    for row in balances:
        if str(row.get("location") or "").strip() or row["name"] != contracts.position_name(
                "BAL", row["product"], row["warehouse"]):
            wanted.add((row["product"], row["warehouse"]))
    return sorted(wanted)


def stale_balances(balances) -> list:
    """合并后要删的结存行：名字不是 ``BAL-物料-仓库`` 的。"""
    from pearnly_erp_portal.inventory import contracts

    return sorted(row["name"] for row in balances
                  if row["name"] != contracts.position_name("BAL", row["product"], row["warehouse"]))


def execute():
    from pearnly_erp_portal.inventory import stock

    has_location = frappe.db.has_column("ERP Stock Balance", "location")
    balances = frappe.get_all("ERP Stock Balance",  # bounded: 迁移一次性读全部结存（生产各站点几行到几百行）
                              fields=["name", "product", "warehouse", *(["location"] if has_location else [])],
                              limit_page_length=0)
    targets = merge_targets(balances)
    for doctype, column in COLUMNS:
        if frappe.db.has_column(doctype, column):
            # 表名、列名是本模块写死的，不来自输入
            frappe.db.sql(f"update `tab{doctype}` set `{column}`='' where coalesce(`{column}`, '')!=''")
    if not targets:
        return
    stock.lock_products([product for product, _warehouse in targets])
    for product, warehouse in targets:
        stock.replay_balance(product, warehouse)
    for name in stale_balances(balances):
        frappe.db.delete("ERP Stock Balance", {"name": name})
    frappe.db.commit()
