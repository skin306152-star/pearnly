"""销售单 ``request_id`` 加唯一索引之前，把会挡住建索引的旧数据整理好（11-28，B ③；采购单早就是唯一的）。

为什么要唯一：同一个请求号同时到两次（LINE 连点「保存」、网页提交 + 卡片保存），拿到锁后的查重以前是普通读，
看不到另一路刚提交的那张，会建出两张同号的单。查重已改成加锁读（`sales.entry.post`），唯一索引是落库的最后一道。

放在 ``[pre_model_sync]``：模型同步加唯一索引时库里不能有重号、也不能有多个空串。本补丁只改 ``request_id`` 这一列：
- 空串改成 NULL（唯一索引允许多个 NULL，不允许多个空串）；
- 同号的，最早那张不动，后来的改成 ``<原号>#<单号>``（截到列宽），记一条错误日志列出改了哪些。不删任何单据。
11-26 线上 7 个 ERP 站点只读核过重号为 0；这一步是给万一有的站点兜底。可重复执行（第二次什么都不改）。
"""
import frappe

DOCTYPE = "ERP Sales Document"
WIDTH = 140


def plan():
    """要改的行：``(空串行数, [(单号, 新请求号), …])``。只读。"""
    table = f"`tab{DOCTYPE}`"
    blank = frappe.db.sql(f"select count(*) from {table} where request_id = ''")[0][0]
    renames = []
    for request_id, in frappe.db.sql(f"""select request_id from {table} where ifnull(request_id, '') != ''
            group by request_id having count(*) > 1"""):
        names = frappe.db.sql_list(f"select name from {table} where request_id = %s order by creation asc, name asc",
                                   (request_id,))
        for name in names[1:]:
            suffix = f"#{name}"
            renames.append((name, f"{request_id[:WIDTH - len(suffix)]}{suffix}"))
    return blank, renames


def execute():
    if not frappe.db.table_exists(DOCTYPE) or not frappe.db.has_column(DOCTYPE, "request_id"):
        return
    blank, renames = plan()
    if blank:
        frappe.db.sql(f"update `tab{DOCTYPE}` set request_id = null where request_id = ''")
    for name, request_id in renames:
        frappe.db.sql(f"update `tab{DOCTYPE}` set request_id = %s where name = %s", (request_id, name))
    if renames:
        frappe.log_error(title="ERP sales request_id made unique",
                         message="\n".join(f"{name} -> {request_id}" for name, request_id in renames)[:4000])
