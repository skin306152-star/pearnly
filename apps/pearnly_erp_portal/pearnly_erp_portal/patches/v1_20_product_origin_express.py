"""商品来由补值（10-30）：「来由」10-29 随盘点当场新建加上，Express 主档同步只在新建商品时写 `express`，之前同步进来的商品
来由是空的（生产 polymer、mrerp 等）。带 Express 编码、来由为空的补成 `express`；其余空来由的商品（手建 / 导入 / 采购销售
自动建）分不清，保持空。只补空值，重跑无副作用；打印补前空、补后空的条数（发布回读用）。"""
import frappe

DOCTYPE = "ERP Product"
EMPTY = "ifnull(express_code, '') != '' and ifnull(origin, '') = ''"


def execute():
    if not frappe.db.table_exists(DOCTYPE):
        return
    before = frappe.db.sql(f"select count(*) from `tab{DOCTYPE}` where {EMPTY}")[0][0]
    frappe.db.sql(f"update `tab{DOCTYPE}` set origin=%s where {EMPTY}", ("express",))
    after = frappe.db.sql(f"select count(*) from `tab{DOCTYPE}` where {EMPTY}")[0][0]
    frappe.db.commit()
    print(f"ERP Product origin: {before} synced products without an origin before, {after} after")
