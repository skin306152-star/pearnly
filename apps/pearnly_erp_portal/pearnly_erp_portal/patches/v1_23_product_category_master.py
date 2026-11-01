"""商品分类从裸整数改成分类主档（岳父 11-01：分类要能写中 / 泰 / 英文字和数字）。

原来 `ERP Product.category_id` 是 Int（旧系统 `products.category_id` 照搬，两边都没有分类表，数字本身没有名称）；字段已换成
`category`（Link → `ERP Product Category`）。这里把存量每个不同的数字建成一条同名分类（名称就是这个数字，以后在分类弹窗里改名，
挂着的商品一起变），再把商品挂过去，最后把旧列真删掉（模型同步不删列，留着会在原生字段选择 / 筛选里露出来，同 v1_21）。
0 是 Int 的默认值（= 没填），不建。幂等：同名分类已有就复用，已挂过 `category` 的商品不动；旧列没了就什么都不做。
打印建了几条、挂了几件留底（发布回读用）。"""
import frappe

PRODUCT = "ERP Product"
CATEGORY = "ERP Product Category"


def execute():
    if not frappe.db.table_exists(PRODUCT) or not frappe.db.has_column(PRODUCT, "category_id"):
        return
    rows = frappe.db.sql(f"""select name, category_id from `tab{PRODUCT}`
        where category_id is not null and category_id != 0 and ifnull(category, '') = ''""", as_dict=True)
    created = linked = 0
    by_value = {}
    for row in rows:
        text = str(int(row.category_id))
        if text not in by_value:
            name = frappe.db.get_value(CATEGORY, {"category_name": text}, "name")
            if not name:
                doc = frappe.get_doc({"doctype": CATEGORY, "category_name": text, "is_active": 1})
                doc.insert(ignore_permissions=True)
                name, created = doc.name, created + 1
            by_value[text] = name
        frappe.db.set_value(PRODUCT, row.name, "category", by_value[text], update_modified=False)
        linked += 1
    frappe.db.commit()
    frappe.db.sql_ddl(f"ALTER TABLE `tab{PRODUCT}` DROP COLUMN `category_id`")
    if frappe.db.table_exists("__UserSettings", cached=False):
        frappe.db.sql("delete from `__UserSettings` where doctype=%s and data like %s", (PRODUCT, '%"category_id"%'))
    print(f"ERP Product category: {created} categories created, {linked} products linked, category_id dropped")
