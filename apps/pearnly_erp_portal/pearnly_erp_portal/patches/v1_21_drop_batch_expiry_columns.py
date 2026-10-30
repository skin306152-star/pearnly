"""批次 / 有效期彻底删除（用户 10-30）：10-29 只从页面去掉、列隐藏保留，用户在原生字段选择 / 筛选 / 报表视图里还看得到。
字段已从 DocType 拿掉；Frappe 的模型同步只加 / 改列、不删列，这里把库里的列真正删掉。存量值不迁移（没有代码读它们），
删前打印每列有值的行数留底（发布回读用）。重跑无副作用。"""
import frappe

COLUMNS = {"ERP Product": ("track_batch", "track_expiry"), "Purchase Line": ("batch_no", "expiry_date"),
           "ERP Stock Entry Line": ("batch_no",)}


def execute():
    for doctype, columns in COLUMNS.items():
        for column in columns:
            if not frappe.db.has_column(doctype, column):
                continue
            filled = frappe.db.sql(f"select count(*) from `tab{doctype}` where ifnull(`{column}`, '') not in ('', '0')")[0][0]
            frappe.db.sql_ddl(f"ALTER TABLE `tab{doctype}` DROP COLUMN `{column}`")
            print(f"{doctype}.{column}: dropped ({filled} rows had a value)")
            _forget_views(doctype, column)


def _forget_views(doctype, column):
    """原生列表 / 报表视图存在个人设置里的列（`__UserSettings`）：提到这一列的那份设置删掉（下次打开回到默认视图），
    免得存着的报表视图去查一个已经没有的列。"""
    if frappe.db.table_exists("__UserSettings", cached=False):
        frappe.db.sql("delete from `__UserSettings` where doctype=%s and data like %s", (doctype, f'%"{column}"%'))
