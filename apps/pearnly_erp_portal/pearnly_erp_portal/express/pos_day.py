"""收银小票 → Express 现销日汇总（出入库第一期 B3a，10-25）：一家公司一天一张 HS（所有收银机合一张，现销客户），载荷 3 的 POS 日汇总，
与 Cowork 的 POS 导入同一份共用约定（`express_rules.contracts.pos`）。

- 每晚（`nightly`）把这家公司前几天还没汇总的完成小票按销售日合成「收银日汇总」（:data:`DOCTYPE`），小票记它属于哪张
  （ERP Sale.express_summary）；离线晚到的同日小票下一晚另补一张（序号 +1）。作废的不进。
- 退货（主会话 10-25）：**同一天**卖、同一天退的退货小票并进当天这张，冲减走共用约定 ``pos_contract.net_day``（与 Cowork 同一处定）：
  净额为 0 的行与付款方式不写，一天全退（全部为 0）整张不写，冲成负数停下交人（``express.pos_net_negative``）；跨天的退货不进，
  另写退货单 SR（B3b）。
- 日汇总交给写账队列同采购 / 销售（`push`）：明细按品 × 仓 × 单位汇总，金额按含税合计摊成税前（`contracts.document`）；
  当天票号起止、张数、各付款方式金额作为 POS 事实（HS 借现金，PromptPay / 刷卡 / 转账各记一张「借 收款科目 / 贷 现金」，
  收款科目按付款方式问一次、按公司记住，共用写账规则 ``receipt``）。现销客户照名称「เงินสด」交给写账规则认，认不准就问一次。
- 公司按小票的仓库（仓库属于公司）。
"""
import frappe

from pearnly_core.express_rules.contracts import pos as pos_contract

from pearnly_erp_portal.cashier import vat as receipt_vat
from pearnly_erp_portal.express import contracts, stock_entry
from pearnly_erp_portal.masters import company as companies

DOCTYPE = "ERP POS Day Summary"
SALE = "ERP Sale"
CASH = "cash"
# 付款方式写进 Express 凭证摘要的写法（泰文，与 Express 里会计手录的叫法一致；现金按共用约定认作现金）
LABELS = {CASH: "เงินสด", "promptpay": "พร้อมเพย์", "card": "บัตรเครดิต", "transfer": "โอนเงิน"}
CASH_PARTY = "เงินสด"
DAYS_MAX = 62  # 一晚最多补几天（停机很久后回来，分几晚补完）

NET_NEGATIVE = "express.pos_net_negative"  # 退货冲减后为负数（停下交人）
VAT_REGISTERED = "vat_registered"  # 单据上问一次的那一项：这家公司是不是增值税登记户（记在公司上，`masters.company`）
# 只汇总公司「收银写 Express 起始日」（第一次接上 Express 那天；已接的公司由补丁记成上线当天）及以后的小票：之前的不补写
# （会计可能已手录过、期间可能已关账；验收 10-25）。没有起始日的公司一张都不汇总。销售小票，加上原单同一天卖的退货小票。
# 自连接原单（o）而不是子查询：UPDATE 里子查询读同一张表 MariaDB 不一定认
_TABLES = """`tabERP Sale` s JOIN `tabERP Warehouse` w ON w.name = s.warehouse
    JOIN `tabERP Company` c ON c.name = w.company LEFT JOIN `tabERP Sale` o ON o.name = s.return_against"""
_RECEIPTS = """w.company = %(company)s AND s.status = 'completed'
    AND (s.sale_type = 'sale' OR (s.sale_type = 'refund' AND DATE(o.sold_at) = DATE(s.sold_at)))
    AND c.express_pos_from IS NOT NULL AND DATE(s.sold_at) >= c.express_pos_from"""
_SCOPE = f"FROM {_TABLES} WHERE {_RECEIPTS}"
# 退货小票上的数量、金额、收款都是正数：并进日汇总时记负数
_SIGN = "(CASE WHEN s.sale_type = 'refund' THEN -1 ELSE 1 END)"


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
    frappe.db.sql(f"""UPDATE {_TABLES}
        SET s.express_summary = %(summary)s
        WHERE {_RECEIPTS}
        AND COALESCE(s.express_summary, '') = '' AND DATE(s.sold_at) = %(day)s""",
                  {"summary": doc.name, "company": company, "day": day})
    totals = frappe.db.sql(f"""SELECT COUNT(*) AS receipts, COALESCE(SUM(s.sale_type = 'sale'), 0) AS count,
        COALESCE(SUM({_SIGN} * s.grand_total), 0) AS total, COALESCE(SUM({_SIGN} * s.vat_amount), 0) AS vat
        FROM `tabERP Sale` s WHERE s.express_summary = %s""", (doc.name,), as_dict=True)[0]
    if not totals.receipts:
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
    """销售票号起止（同 Cowork 不含退款单）；只有退货小票时（晚到的同日退货）按退货小票。"""
    numbers = frappe.db.sql_list("SELECT receipt_no FROM `tabERP Sale` WHERE express_summary = %s AND sale_type = 'sale'",
                                 (summary,)) or \
        frappe.db.sql_list("SELECT receipt_no FROM `tabERP Sale` WHERE express_summary = %s", (summary,))
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
    """这张日汇总交给写账规则的样子（同采购 / 销售的 `contracts.document`，另带 ``pos``）。

    税额（主会话 10-25）：标价含税，增值税登记户的小票逐行拆出税额（`cashier.vat`），日汇总合计各行税额、明细按各行税前额交
    （尾差由 `contracts.document` 落到税前）；还不知道这家公司是不是登记户（公司上没记、税务局也没查到）时停下，在单据上问一次。"""
    summary = frappe.db.get_value(DOCTYPE, name, ["company", "day", "first_receipt", "last_receipt", "sales_count"],
                                  as_dict=True)
    if not companies.vat_registered(summary.company, lookup=True):
        return {"ref": ref, "hold": f"{contracts.CHOICE}:{VAT_REGISTERED}",
                "needed": [{"kind": VAT_REGISTERED, "subject": "company", "label": "",
                            "options": [{"code": companies.VAT_YES, "name": ""}, {"code": companies.VAT_NO, "name": ""}]}]}
    receipt_vat.refresh(frappe.db.sql_list("SELECT name FROM `tabERP Sale` WHERE express_summary = %s", (name,)))
    sums = frappe.db.sql(f"""SELECT COALESCE(SUM({_SIGN} * s.grand_total), 0) AS total,
        COALESCE(SUM({_SIGN} * s.vat_amount), 0) AS vat FROM `tabERP Sale` s WHERE s.express_summary = %s""",
                         (name,), as_dict=True)[0]
    frappe.db.set_value(DOCTYPE, name, {"grand_total": sums.total, "vat_amount": sums.vat}, update_modified=False)
    summary.grand_total, summary.vat_amount = sums.total, sums.vat
    company = frappe.db.get_value("ERP Company", summary.company, ["tax_id", "branch"], as_dict=True) or {}
    rows = frappe.db.sql(f"""SELECT l.product, l.warehouse, l.unit, SUM({_SIGN} * l.qty) AS qty,
        SUM({_SIGN} * (l.line_total - COALESCE(l.vat_amount, 0))) AS amount
        FROM `tabERP Sale Line` l JOIN `tabERP Sale` s ON s.name = l.parent AND l.parenttype = 'ERP Sale'
        WHERE s.express_summary = %s GROUP BY l.product, l.warehouse, l.unit ORDER BY MIN(l.idx)""", (name,), as_dict=True)
    products, warehouses, merged = {}, {}, {}
    for row in rows:
        product = products.get(row.product)
        if row.product and product is None:
            product = products[row.product] = frappe.db.get_value(
                "ERP Product", row.product, ["product_name", "express_code", "express_stock", "base_unit", "unit"],
                as_dict=True) or {}
        product = product or {}
        if row.warehouse and row.warehouse not in warehouses:
            warehouses[row.warehouse] = frappe.db.get_value("ERP Warehouse", row.warehouse, "express_code") or ""
        code = product.get("express_code") or ""
        # 小票行可以是包装单位：同出入库单照行上单位交、带 ERP 的换算率，CC 按 Express 主档的包装单位写（验收 10-25）
        factor = stock_entry.base_unit(row.product, product, row.unit, strict=False)[1] if row.product else contracts.d(1)
        key = (row.product or row.unit, row.warehouse, row.unit)
        line = merged.setdefault(key, {"name": product.get("product_name") or code or "-", "code": code, "unit": row.unit,
                                       "qty": contracts.d(0), "amount": contracts.d(0),
                                       "stock": bool(product.get("express_stock")) if code else bool(row.product),
                                       "loccod": warehouses.get(row.warehouse, "")})
        if factor != 1:
            line["factor"] = stock_entry._plain(factor)
        line["qty"] += contracts.d(row.qty)
        line["amount"] += contracts.d(row.amount)
    total, vat = contracts.d(summary.grand_total), contracts.d(summary.vat_amount)
    paid = {row.method: contracts.d(row.amount) for row in frappe.db.sql(
        f"""SELECT p.method, SUM({_SIGN} * p.amount) AS amount FROM `tabERP Sale Payment` p
        JOIN `tabERP Sale` s ON s.name = p.parent AND p.parenttype = 'ERP Sale'
        WHERE s.express_summary = %s AND p.method != %s GROUP BY p.method""", (name, CASH), as_dict=True)}
    # 同一天的退货冲减（共用约定，与 Cowork 同一处定）：全部为 0 整张不写，冲成负数停下交人
    try:
        net = pos_contract.net_day(total, payments_of(total, paid), list(merged.values()))
    except pos_contract.NetError:
        return {"ref": ref, "items": [], "net_negative": True}
    if net is None:
        return {"ref": ref, "items": [], "empty": True}
    payments = list(net.payments)
    lines = [{**line, "qty": stock_entry._plain(line["qty"]), "amount": contracts.money(line["amount"])}
             for line in net.items]
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
    """当天各付款方式 → ``[(写法, 金额)]``：非现金照收到的（找零只在现金上；同一天的退货记负数），现金 = 含税合计 − 非现金
    （找零已扣），和恰好等于合计（共用约定的核对）。没有一笔非现金时整天记现金。净额为 0 的付款方式不列。"""
    out = [(LABELS.get(method, method), amount) for method, amount in sorted(paid.items()) if amount != 0]
    cash = contracts.d(total) - sum((amount for _label, amount in out), contracts.d(0))
    if cash or not out:
        out.insert(0, (LABELS[CASH], cash))
    return out


__all__ = ["DOCTYPE", "LABELS", "NET_NEGATIVE", "document", "nightly", "payments_of", "pending_days", "receipt_range", "summarize"]
