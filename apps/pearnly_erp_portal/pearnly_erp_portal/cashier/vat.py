"""收银小票的税额（主会话 10-25）：标价就是含税价，应收合计、实收、找零、交班都不变，只把同一个合计拆成税前加税额存起来。

- 公司是增值税登记户（`masters.company.vat_registered`）、商品勾了「คิด VAT」（``ERP Product.vat_applicable``）的行，
  税额 = 行含税净额 × 7/107（整单折扣按行摊，`contracts.line_vats`）；其余行为 0。小票税额 = 各行之和。
- 退货小票按原小票那一行的税额、按退的数量比例算（`contracts.refund_line_vat`）。
- 公司状态后来才知道、或改了：还没写进 Express 的小票按新状态重算（:func:`refresh_company`）；写日汇总前再按当时的状态重算一次
  （`express.pos_day`），日汇总合计各行税额。
"""
import frappe

from pearnly_erp_portal.cashier import contracts
from pearnly_erp_portal.masters import company as companies

SALE = "ERP Sale"
LINE = "ERP Sale Line"


def sale_line_vats(company, lines, discount_total=0) -> list:
    """开小票时各行的税额（``lines`` 是要落库的行，带 ``product`` / ``line_total``）。只看公司上记着的状态，不查税务局。"""
    if companies.vat_registered(company) != companies.VAT_YES:
        return [contracts.d(0)] * len(lines)
    taxable = _taxable([line.get("product") for line in lines])
    return contracts.line_vats([line["line_total"] for line in lines],
                               [taxable.get(line.get("product"), False) for line in lines], discount_total)


def refund_line_vats(lines) -> list:
    """退货各行的税额：按原小票那一行（``original_line``）的税额与数量比例。"""
    originals = {row.name: row for row in frappe.get_all(  # bounded: 这张退货单的原行
        LINE, filters={"name": ["in", [line.get("original_line") for line in lines if line.get("original_line")] or [""]]},
        fields=["name", "qty", "vat_amount"], limit_page_length=0)}
    return [contracts.refund_line_vat(originals[line["original_line"]].vat_amount, originals[line["original_line"]].qty,
                                      line["qty"]) if line.get("original_line") in originals else contracts.d(0)
            for line in lines]


def _taxable(products) -> dict:
    names = sorted({name for name in products if name})
    if not names:
        return {}
    return {row.name: bool(row.vat_applicable) for row in frappe.get_all(  # bounded: 这张小票的商品
        "ERP Product", filters={"name": ["in", names]}, fields=["name", "vat_applicable"], limit_page_length=0)}


CHUNK = 500


def refresh(names) -> int:
    """按各自公司此刻的状态重算这些小票的行税额与小票税额（先销售、后退货，退货要读原行重算后的税额）。合计不动。回税额变了的张数
    （重跑结果不变，第二次是 0）。
    按不含税价另加 VAT 开的小票（接口显式 has_vat，应收 ≠ 小计 − 折扣）税额是开单时算好的，不动。"""
    names = list(names)
    rows = []
    for start in range(0, len(names), CHUNK):
        rows += frappe.get_all(SALE, filters={"name": ["in", names[start:start + CHUNK]]},  # bounded: 调用方给的小票，分批
                               fields=["name", "sale_type", "warehouse", "subtotal", "discount_total", "grand_total",
                                       "vat_amount"], limit_page_length=0)
    rows.sort(key=lambda row: row.sale_type != "sale")
    company_of = {}
    changed = 0
    for row in rows:
        if row.sale_type == "sale" and contracts.d(row.grand_total) != contracts.d(row.subtotal) - contracts.d(row.discount_total):
            continue
        if row.warehouse not in company_of:
            company_of[row.warehouse] = frappe.db.get_value("ERP Warehouse", row.warehouse, "company")
        lines = frappe.get_all(LINE, filters={"parent": row.name, "parenttype": SALE},  # bounded: 这张小票的行
                               fields=["name", "product", "qty", "line_total", "original_line", "vat_amount"],
                               order_by="idx asc", limit_page_length=0)
        if row.sale_type == "sale":
            vats = sale_line_vats(company_of[row.warehouse], lines, row.discount_total or 0)
        elif companies.vat_registered(company_of[row.warehouse]) == companies.VAT_YES:
            vats = refund_line_vats(lines)
        else:
            vats = [contracts.d(0)] * len(lines)
        for line, value in zip(lines, vats):
            if contracts.d(line.vat_amount) != value:
                frappe.db.set_value(LINE, line.name, "vat_amount", value, update_modified=False)
        total = sum(vats, contracts.d(0))
        if contracts.d(row.vat_amount) != total:
            frappe.db.set_value(SALE, row.name, "vat_amount", total, update_modified=False)
            changed += 1
    return changed


def exempt_total(names):
    """这些小票里免税商品行（没勾「คิด VAT」）的净额合计（扣过摊到的整单折扣，退货记负数）：现销日汇总里混有免税商品时，
    Express 销项税记录的应税基数要把这部分扣掉（`express.pos_day.vat_fields`）。"""
    total = contracts.d(0)
    rows = frappe.get_all(SALE, filters={"name": ["in", list(names) or [""]]},  # bounded: 一张日汇总里的小票
                          fields=["name", "sale_type", "discount_total"], limit_page_length=0)
    for row in rows:
        lines = frappe.get_all(LINE, filters={"parent": row.name, "parenttype": SALE},  # bounded: 这张小票的行
                               fields=["product", "line_total"], order_by="idx asc", limit_page_length=0)
        taxable = _taxable([line.product for line in lines])
        nets = contracts.line_nets([line.line_total for line in lines], row.discount_total or 0)
        exempt = sum((net for line, net in zip(lines, nets) if not taxable.get(line.product, False)), contracts.d(0))
        total += -exempt if row.sale_type == "refund" else exempt
    return total


def refresh_company(company) -> int:
    """公司状态定了或改了：这家公司还没写进 Express 的完成小票（没进日汇总的、日汇总还没写进去的）按新状态重算。
    写进去了的（含人标记「已在 Express 手工处理」的）不动：跟 `express.masters.WRITTEN_STATES` 同一口径。"""
    from pearnly_erp_portal.express.masters import WRITTEN_STATES

    names = frappe.db.sql_list("""SELECT s.name FROM `tabERP Sale` s JOIN `tabERP Warehouse` w ON w.name = s.warehouse
        LEFT JOIN `tabERP POS Day Summary` d ON d.name = s.express_summary
        WHERE w.company = %s AND s.status = 'completed'
        AND (COALESCE(s.express_summary, '') = '' OR COALESCE(d.express_state, '') NOT IN %s)""",
                               (company, WRITTEN_STATES))
    return refresh(names) if names else 0


__all__ = ["exempt_total", "refresh", "refresh_company", "refund_line_vats", "sale_line_vats"]
