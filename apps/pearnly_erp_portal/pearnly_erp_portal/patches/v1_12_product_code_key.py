"""商品编码改成在「公司 + 编码」上唯一（2026-10-24）：补上已有商品的 ``code_key``。

编码原来是全租户唯一索引；一个租户挂几家公司、各自从 Express 同步商品后，同一编码在两家各有一件是常事，所以去掉了那个索引，
改由 ``code_key``（`ERPProduct.validate` 写）上的唯一索引兜并发发号。旧数据里同一个键（同公司同编码）出现不止一次的只给最早那件
写上，其余留空（不挡迁移；那几件是同步时 Express 重复编码多建的，已停用）。幂等。
"""
import frappe


def execute():
    if not frappe.db.has_column("ERP Product", "code_key"):
        return
    seen = set()
    rows = frappe.db.sql("""select name, code, express_company, code_key from `tabERP Product`
                            where ifnull(code, '') != '' order by creation asc, name asc""", as_dict=True)
    for row in rows:
        key = f"{row.express_company or ''}|{row.code}"
        folded = key.casefold().rstrip()  # 唯一索引按库的排序规则比（大小写、尾随空格当一样）
        wanted = None if folded in seen else key
        seen.add(folded)
        if row.code_key == wanted:
            continue
        frappe.db.savepoint("product_code_key")
        try:
            frappe.db.set_value("ERP Product", row.name, "code_key", wanted, update_modified=False)
        except Exception as error:  # noqa: BLE001  库认作同一个键（排序规则比这里宽）：这件留空，不挡迁移
            if not frappe.db.is_unique_key_violation(error):
                raise
            frappe.db.rollback(save_point="product_code_key")
