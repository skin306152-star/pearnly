"""采购明细分类记忆：按「供应商 + 品名」记住上次入账选的分类 / 子分类（与 Cowork 同一规则，用户 2026-09-30）。

- 套用：先找同一供应商的同一品名；没见过的品名，只有这个供应商记过的品名**全是同一个分类**时才按供应商套
  （卖不同东西的供应商不猜，「一般供应商」也就不会被套成一个分类）。已停用或已删的分类不套。
- 学习：每次入账（网页、LINE、更正）按明细行上最终的分类记下，后一次覆盖前一次。
- 只填空着的分类，不改人已经选的。记忆表只经这里读写（`ERP Purchase Category Memory`）。
"""
import hashlib
import json
import re
import unicodedata

import frappe

MEMORY = "ERP Purchase Category Memory"
CATEGORY = "ERP Purchase Category"


def item_key(name):
    # 只为比对：大小写、空白、标点不算区别；泰文声调符保留（与 Cowork `history.categories._item_key` 同一口径）。
    return re.sub(r"[\s.,()\-_/\\'\"&*#:;]+", "", unicodedata.normalize("NFC", str(name or "")).lower())[:140]


def memory_name(supplier, key):
    return hashlib.sha256(json.dumps([supplier or "", key or ""], ensure_ascii=False).encode()).hexdigest()


def _usable(category, subcategory):
    """记下的分类现在还能用：大类启用且是顶层；子分类启用且挂在这个大类下，否则只套大类。"""
    if not category:
        return None
    row = frappe.db.get_value(CATEGORY, category, ["is_active", "parent_category"], as_dict=True)
    if not row or not row.is_active or row.parent_category:
        return None
    if subcategory:
        sub = frappe.db.get_value(CATEGORY, subcategory, ["is_active", "parent_category"], as_dict=True)
        if not sub or not sub.is_active or sub.parent_category != category:
            subcategory = None
    return {"category": category, "subcategory": subcategory or None}


def suggest(supplier, description):
    if not supplier:
        return None
    key = item_key(description)
    if key:
        row = frappe.db.get_value(MEMORY, memory_name(supplier, key), ["category", "subcategory"], as_dict=True)
        if row:
            return _usable(row.category, row.subcategory)
    choices = frappe.get_all(MEMORY, filters={"supplier": supplier}, fields=["category", "subcategory"],
                             distinct=True, limit_page_length=2)
    if len(choices) == 1:
        return _usable(choices[0].category, choices[0].subcategory)
    return None


def fill(supplier, lines):
    """给没选分类的明细行填上记住的分类；返回填了几行。"""
    filled = 0
    for row in lines or []:
        if not isinstance(row, dict) or row.get("category") or not str(row.get("description") or "").strip():
            continue
        found = suggest(supplier, row["description"])
        if found:
            row["category"] = found["category"]
            if found["subcategory"]:
                row["subcategory"] = found["subcategory"]
            filled += 1
    return filled


def learn(supplier, lines):
    """入账后记下每一行的分类（同一张单同一品名以最后一行为准）。"""
    if not supplier:
        return
    latest = {}
    for row in lines or []:
        description, category = str(row.get("description") or "").strip(), row.get("category")
        key = item_key(description)
        if key and category:
            latest[key] = (description[:140], category, row.get("subcategory") or None)
    for key, (description, category, subcategory) in latest.items():
        name = memory_name(supplier, key)
        if frappe.db.exists(MEMORY, name):
            frappe.db.set_value(MEMORY, name, {"item_name": description, "category": category,
                                               "subcategory": subcategory})
        else:
            frappe.get_doc({"doctype": MEMORY, "supplier": supplier, "item_key": key, "item_name": description,
                            "category": category, "subcategory": subcategory}).insert(ignore_permissions=True)


def matched_supplier(result):
    """识别结果对应的已有供应商（只认唯一匹配；认不出、有歧义都当没有，不影响识别）。"""
    from pearnly_erp_portal.masters.selection import match_party

    try:
        return match_party("supplier", str(result.get("supplier_name") or "").strip(),
                           tax_id=result.get("supplier_tax_id"), branch_type=result.get("supplier_branch_type"),
                           branch_no=result.get("supplier_branch_no"))
    except frappe.ValidationError:
        return None
