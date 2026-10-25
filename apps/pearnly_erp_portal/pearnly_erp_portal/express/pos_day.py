"""收银小票 → Express 现销日汇总（出入库第一期 B3a，10-25）：一家公司一天一张 HS（所有收银机合一张，现销客户），载荷 3 的 POS 日汇总，
与 Cowork 的 POS 导入同一份共用约定（`express_rules.contracts.pos`）。

- 每晚（`nightly`）把这家公司前几天还没汇总的完成小票按销售日合成「收银日汇总」（:data:`DOCTYPE`），小票记它属于哪张
  （ERP Sale.express_summary）；离线晚到的同日小票下一晚另补一张（序号 +1）。作废的、退货小票不进（退货另做 SR，B3b）。
- 日汇总交给写账队列同采购 / 销售（`push`）：明细按品 × 仓 × 单位汇总，金额按含税合计摊成税前（`contracts.document`）；
  当天票号起止、张数、各付款方式金额作为 POS 事实（HS 借现金，PromptPay / 刷卡 / 转账各记一张「借 收款科目 / 贷 现金」，
  收款科目按付款方式问一次、按公司记住，共用写账规则 ``receipt``）。现销客户照名称「เงินสด」交给写账规则认，认不准就问一次。
- 公司按小票的仓库（仓库属于公司）。
"""
import frappe

from pearnly_core.express_rules.contracts import pos as pos_contract

from pearnly_erp_portal.express import contracts

DOCTYPE = "ERP POS Day Summary"
SALE = "ERP Sale"
CASH = "cash"
# 付款方式写进 Express 凭证摘要的写法（泰文，与 Express 里会计手录的叫法一致；现金按共用约定认作现金）
LABELS = {CASH: "เงินสด", "promptpay": "พร้อมเพย์", "card": "บัตรเครดิต", "transfer": "โอนเงิน"}
CASH_PARTY = "เงินสด"
DAYS_MAX = 62  # 一晚最多补几天（停机很久后回来，分几晚补完）

_SCOPE = """FROM `tabERP Sale` s JOIN `tabERP Warehouse` w ON w.name = s.warehouse
    WHERE w.company = %(company)s AND s.status = 'completed' AND s.sale_type = 'sale'"""


def pending_days(company, before) -> list:
    """这家公司 ``before`` 之前还没汇总的完成小票落在哪几天（最多 :data:`DAYS_MAX` 天，早的在前）。"""
    rows = frappe.db.sql(f"""SELECT DISTINCT DATE(s.sold_at) AS day {_SCOPE}
        AND COALESCE(s.express_summary, '') = '' AND s.sold_at < %(before)s ORDER BY day LIMIT {DAYS_MAX}""",
                         {"company": company, "before": before}, as_dict=True)
    return [row.day for row in rows]


def summarize(company, day):
    """把这家公司 ``day`` 还没汇总的完成小票合成一张日汇总并排进写账队列 → 日汇总名；一张都没有回 None。"""
    seq = frappe.db.count(DOCTYPE, {"company": company, "day": day}) + 1
    doc = frappe.get_doc({"doctype": DOCTYPE, "company": company, "day": day, "seq": seq, "status": "submitted"})
    doc.insert(ignore_permissions=True)
    frappe.db.sql(f"""UPDATE `tabERP Sale` s JOIN `tabERP Warehouse` w ON w.name = s.warehouse
        SET s.express_summary = %(summary)s
        WHERE w.company = %(company)s AND s.status = 'completed' AND s.sale_type = 'sale'
        AND COALESCE(s.express_summary, '') = '' AND DATE(s.sold_at) = %(day)s""",
                  {"summary": doc.name, "company": company, "day": day})
    totals = frappe.db.sql("""SELECT COUNT(*) AS count, COALESCE(SUM(grand_total), 0) AS total,
        COALESCE(SUM(vat_amount), 0) AS vat FROM `tabERP Sale` WHERE express_summary = %s""", (doc.name,), as_dict=True)[0]
    if not totals.count:
        frappe.delete_doc(DOCTYPE, doc.name, ignore_permissions=True, force=True)
        return None
    first, last = receipt_range(doc.name)
    frappe.db.set_value(DOCTYPE, doc.name, {"sales_count": totals.count, "grand_total": totals.total,
                                            "vat_amount": totals.vat, "first_receipt": first, "last_receipt": last},
                        update_modified=False)
    from pearnly_erp_portal.express import push

    push.queue(DOCTYPE, doc.name)
    return doc.name


def _natural(value):
    import re

    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", str(value or ""))]


def receipt_range(summary):
    numbers = frappe.db.sql_list("SELECT receipt_no FROM `tabERP Sale` WHERE express_summary = %s", (summary,))
    numbers = sorted((str(number).strip() for number in numbers if str(number or "").strip()), key=_natural)
    return (numbers[0], numbers[-1]) if numbers else (summary, summary)


def nightly():
    """每晚：接了 Express 的公司，昨天及更早还没汇总的完成小票按天合成日汇总（没接 Express 的站点什么都不做）。"""
    from pearnly_erp_portal.express import masters

    today = frappe.utils.nowdate()
    for company in masters.linked_companies():
        for day in pending_days(company.name, today):
            summarize(company.name, day)
            frappe.db.commit()


def document(name, ref) -> dict:
    """这张日汇总交给写账规则的样子（同采购 / 销售的 `contracts.document`，另带 ``pos``）。"""
    summary = frappe.db.get_value(DOCTYPE, name, ["company", "day", "grand_total", "vat_amount", "first_receipt",
                                                  "last_receipt", "sales_count"], as_dict=True)
    company = frappe.db.get_value("ERP Company", summary.company, ["tax_id", "branch"], as_dict=True) or {}
    rows = frappe.db.sql("""SELECT l.product, l.warehouse, l.unit, SUM(l.qty) AS qty, SUM(l.line_total) AS amount
        FROM `tabERP Sale Line` l JOIN `tabERP Sale` s ON s.name = l.parent AND l.parenttype = 'ERP Sale'
        WHERE s.express_summary = %s GROUP BY l.product, l.warehouse, l.unit ORDER BY MIN(l.idx)""", (name,), as_dict=True)
    products, warehouses, lines = {}, {}, []
    for row in rows:
        product = products.get(row.product)
        if row.product and product is None:
            product = products[row.product] = frappe.db.get_value(
                "ERP Product", row.product, ["product_name", "express_code", "express_stock"], as_dict=True) or {}
        product = product or {}
        if row.warehouse and row.warehouse not in warehouses:
            warehouses[row.warehouse] = frappe.db.get_value("ERP Warehouse", row.warehouse, "express_code") or ""
        code = product.get("express_code") or ""
        lines.append({"name": product.get("product_name") or code or "-", "code": code, "unit": row.unit,
                      "qty": row.qty, "amount": row.amount,
                      "stock": bool(product.get("express_stock")) if code else bool(row.product),
                      "loccod": warehouses.get(row.warehouse, "")})
    total, vat = contracts.d(summary.grand_total), contracts.d(summary.vat_amount)
    paid = {row.method: contracts.d(row.amount) for row in frappe.db.sql(
        """SELECT p.method, SUM(p.amount) AS amount FROM `tabERP Sale Payment` p
        JOIN `tabERP Sale` s ON s.name = p.parent AND p.parenttype = 'ERP Sale'
        WHERE s.express_summary = %s AND p.method != %s GROUP BY p.method""", (name, CASH), as_dict=True)}
    payments = payments_of(total, paid)
    body = contracts.document(ref=ref, direction="sales", doc_date=str(summary.day),
                              # 参考号写进 Express 的 YOUREF / 销项税 REFNUM（≤15 字节）：同 Cowork 的 POS 导入用共用约定的
                              # `day_ref`（放不下就缩写，完整起止在 `pos` 里）；10-25 实验栈：「首-尾」27 字被 CC 拦下
                              number=pos_contract.day_ref(summary.first_receipt, summary.last_receipt,
                                                          frappe.utils.getdate(summary.day)),
                              company=company, party={"code": "", "name": CASH_PARTY}, paid=True, has_vat=vat > 0,
                              total=total, vat=vat, lines=lines)
    body["pos"] = {"first": summary.first_receipt, "last": summary.last_receipt, "count": int(summary.sales_count or 0),
                   "payments": [[label, contracts.money(amount)] for label, amount in payments]}
    return body


def payments_of(total, paid) -> list:
    """当天各付款方式 → ``[(写法, 金额)]``：非现金照收到的（找零只在现金上），现金 = 含税合计 − 非现金（找零已扣），和恰好等于
    合计（共用约定的核对）。没有一笔非现金时整天记现金。"""
    out = [(LABELS.get(method, method), amount) for method, amount in sorted(paid.items()) if amount > 0]
    cash = contracts.d(total) - sum((amount for _label, amount in out), contracts.d(0))
    if cash or not out:
        out.insert(0, (LABELS[CASH], cash))
    return out


__all__ = ["DOCTYPE", "LABELS", "document", "nightly", "payments_of", "pending_days", "receipt_range", "summarize"]
