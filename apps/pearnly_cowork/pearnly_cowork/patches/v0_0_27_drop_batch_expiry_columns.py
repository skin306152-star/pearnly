"""批次 / 有效期彻底删除（用户 10-30）：字段已从 Cowork Product 拿掉；模型同步不删列，这里把库里的两列真正删掉。
存量值不迁移（没有代码读它们），删前打印有值的行数留底。重跑无副作用。"""
import frappe

DOCTYPE = "Cowork Product"


def execute():
    for column in ("track_batch", "track_expiry"):
        if not frappe.db.has_column(DOCTYPE, column):
            continue
        filled = frappe.db.sql(f"select count(*) from `tab{DOCTYPE}` where ifnull(`{column}`, 0) != 0")[0][0]
        frappe.db.sql_ddl(f"ALTER TABLE `tab{DOCTYPE}` DROP COLUMN `{column}`")
        print(f"{DOCTYPE}.{column}: dropped ({filled} rows had a value)")
        if frappe.db.table_exists("__UserSettings", cached=False):  # 存着这一列的个人列表 / 报表视图回到默认
            frappe.db.sql("delete from `__UserSettings` where doctype=%s and data like %s", (DOCTYPE, f'%"{column}"%'))
