"""采购 / 销售明细「这一行对哪件物料、动不动库存」的预选与记忆（11-06 按行选物料与库存，评估
docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 4.2、15.3）。

- 键：公司 × 共用写账规则的 ``item_subject``（``方向|对方税号或名称|规范品名``，与 ERP Express Choice 的 ``item`` 同一个键，
  `pearnly_core.express_rules.push.choices.item_subject`）。同一个供应商给两家公司供货，一家管库存、一家只记账，所以带公司。
- 套用：顺序见 `line_rules.decide`。只填空着的行，不改人已经选的。记住的物料停用了、或是别家公司 Express 的档，当没记过。
- 学习：每次提交成功（网页、LINE 卡片打开的页面、修改）按行上**最终**的值覆盖记下；作废不撤销（下次人改了就覆盖）。
- 记忆表只经这里读写（`ERP Line Item Memory`），不开列表、不给前端接口。调用方各自授权（采购 / 销售录入岗位）。
"""
import hashlib
import json

import frappe

from pearnly_core.express_rules.push.choices import item_subject, norm_name
from pearnly_erp_portal.products import line_rules

MEMORY = "ERP Line Item Memory"
DIRECTIONS = ("purchase", "sales")


def subject(direction, party_tax, party_name, description) -> str:
    return item_subject(direction, party_tax or "", party_name or "", description or "")[:280]


def memory_name(company, key) -> str:
    return hashlib.sha256(json.dumps([company or "", key or ""], ensure_ascii=False).encode()).hexdigest()


def _item_key(description) -> str:
    return norm_name(description)[:140]


def _product(name, company):
    """这件物料在这家公司还能用：启用着、不是别家公司 Express 的档。"""
    if not name:
        return None
    from pearnly_erp_portal.masters import access as master_access

    row = frappe.db.get_value("ERP Product", name, ["name", "is_active", master_access.OWNER, "express_code",
                                                    "express_stock"], as_dict=True)
    if not row or not row.is_active or not master_access.usable(row.get(master_access.OWNER), company):
        return None
    return row


def _usable(row, company):
    if not row or row.get("item_type") not in (line_rules.GOODS, line_rules.SERVICE):
        return None
    if row.get("product") and not _product(row["product"], company):
        return None
    if row["item_type"] == line_rules.GOODS and not row.get("product"):
        return None  # 记的是入库却没物料：提交后不会这样，当没记过
    return {"product": row.get("product") or None, "item_type": row["item_type"]}


def remembered(company, direction, party_tax, party_name, description):
    row = frappe.db.get_value(MEMORY, memory_name(company, subject(direction, party_tax, party_name, description)),
                              ["product", "item_type"], as_dict=True)
    return _usable(row, company)


def agreed(company, direction, description):
    """这家公司这个方向下，这个品名在各家对方那里记的结论全都一样才用（一般供应商 / 散客、没记过这家对方时）。"""
    key = _item_key(description)
    if not key:
        return None
    rows = frappe.get_all(MEMORY, filters={"company": company, "direction": direction, "item_key": key},
                          fields=["product", "item_type"], limit_page_length=20)
    found = {(row.product or "", row.item_type or "") for row in rows}
    if len(found) != 1:
        return None
    return _usable(rows[0], company)


def matched(line, company, direction):
    """按条码 / 编码（采购）、品名认出的已有物料 ``({product, express_non_stock} 或 None, 认出几条)``：同提交时的认法。"""
    from pearnly_erp_portal.masters.selection import match_product
    from pearnly_erp_portal.products import service

    name = None
    if direction == "purchase":
        for field in ("barcode", "code"):
            if line.get(field):
                name = service.find(field, line[field], company)
                if name:
                    break
    count = 1 if name else 0
    if not name:
        name, count = match_product(str(line.get("description") or "").strip(), company)
    row = _product(name, company)
    if not row:
        return None, count
    return {"product": row.name, "express_non_stock": bool(row.express_code) and not row.express_stock}, 1


def suggest(line, *, company, direction, party_tax="", party_name="", expense=False, typed=False) -> dict:
    """一行的预选（`line_rules.decide`）。``typed``：行上的 ``item_type`` 是人定的（网页提交）；否则是识别给的。"""
    chosen = {"product": line.get("product"),
              "item_type": line.get("item_type") if typed else None}
    if chosen["product"] or chosen["item_type"]:
        return line_rules.decide(chosen=chosen)
    description = str(line.get("description") or "").strip()
    if line.get(line_rules.NON_CLAIMABLE_VAT):
        return line_rules.decide(non_claimable=True)
    memory = remembered(company, direction, party_tax, party_name, description) if description else None
    consensus = None if memory else agreed(company, direction, description)
    found = None
    if not (memory or consensus):
        found, _count = matched(line, company, direction)
    return line_rules.decide(remembered=memory, agreed=consensus, matched=found,
                             recognised=line.get("item_type") if direction == "purchase" else None, expense=expense)


def apply(line, result):
    """把预选写到这一行（只填空着的）：类型、物料（单位换成物料的，同按名认的口径）。"""
    line["item_type"] = result["item_type"]
    if result.get("product") and not line.get("product"):
        from pearnly_erp_portal.masters.selection import own_unit

        line["product"] = result["product"]
        line["unit"] = own_unit(frappe.get_doc("ERP Product", result["product"]), line.get("unit"))
    return line


def fill(lines, *, company, direction, party_tax="", party_name="", expense=False, typed=False):
    """整张单的行逐行预选并写上；返回每行的预选结果（与 ``lines`` 同序）。"""
    results = []
    for line in lines or []:
        if not isinstance(line, dict) or not str(line.get("description") or "").strip():
            results.append(None)
            continue
        result = suggest(line, company=company, direction=direction, party_tax=party_tax, party_name=party_name,
                         expense=expense, typed=typed)
        apply(line, result)
        results.append(result)
    return results


def learn(company, direction, party_tax, party_name, lines, document=None):
    """提交成功后记下每一行最终的选择（同一张单同一品名以最后一行为准）。"""
    if direction not in DIRECTIONS or not company:
        return
    latest = {}
    for row in lines or []:
        description = str(row.get("description") or "").strip()
        item_type = row.get("item_type") or line_rules.GOODS
        if not _item_key(description) or (item_type == line_rules.GOODS and not row.get("product")):
            continue
        latest[subject(direction, party_tax, party_name, description)] = {
            "item_name": description[:140], "item_key": _item_key(description), "product": row.get("product") or None,
            "item_type": item_type, "last_document": document}
    for key, values in latest.items():
        name = memory_name(company, key)
        if frappe.db.exists(MEMORY, name):
            frappe.db.set_value(MEMORY, name, values, update_modified=True)
        else:
            frappe.get_doc({"doctype": MEMORY, "company": company, "direction": direction, "subject": key,
                            **values}).insert(ignore_permissions=True)


__all__ = ["agreed", "apply", "fill", "learn", "matched", "memory_name", "remembered", "subject", "suggest"]
