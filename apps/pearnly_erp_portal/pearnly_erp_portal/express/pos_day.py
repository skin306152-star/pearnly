"""收银小票 → Express 现销日汇总（出入库第一期 B3a，10-25）：一家公司一天一张 HS（所有收银机合一张，现销客户），载荷 3 的 POS 日汇总，
与 Cowork 的 POS 导入同一份共用约定（`express_rules.contracts.pos`）。

- 每晚（`nightly`）把这家公司前几天还没汇总的完成小票按销售日合成「收银日汇总」（:data:`DOCTYPE`），小票记它属于哪张
  （ERP Sale.express_summary）；离线晚到的同日小票下一晚另补一张（序号 +1）。作废的不进。
- 退货（主会话 10-25）：**同一天**卖、同一天退、原小票还没汇总的退货小票并进当天这张，冲减走共用约定 ``pos_contract.net_day``
  （与 Cowork 同一处定）：净额为 0 的行与付款方式不写，一天全退（全部为 0）整张不写，冲成负数停下交人（``express.pos_net_negative``）。
- 退货单 SR（B3b，10-26）：原小票已经进了某张 HS 之后才来的退货（跨天的，或同一天晚到的）不再冲 HS，每晚按「退货那天 × 原小票所在的
  HS」合成一张退货日汇总（同一个 DocType，``doc_kind`` = SR、``against`` = 原 HS；SR 的原单号只能挂一张原单，所以按原 HS 分张），
  写成 Express 原生的退货单：退现金、货回仓、挂原 HS（Express 单号与合计交给写账规则，``refund`` 块）。原 HS 还没写进 Express 时先等
  （``express.pos_refund_waiting``，每分钟再看），写进去了自动接着写。原小票在「收银写 Express 起始日」之前（没进过 HS）的退货不写。
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
WRITTEN_STATES = ("written", "mismatch", "handled")  # 写进 Express 了（含人手工处理的；`masters.WRITTEN_STATES` 同一份）

NET_NEGATIVE = "express.pos_net_negative"  # 退货冲减后为负数（停下交人）
REFUND_WAITING = contracts.REFUND_WAITING  # 退货单的原 HS 还没写进 Express（等它写成再写，`push.poll` 每分钟再看）
HS, SR = "HS", "SR"  # 日汇总的单别：现销日汇总 / 退货日汇总
# 一张现销里混有免税商品（主会话 10-26）：Express 销项税记录（ISVAT）AMT01 只记应税行的税前合计，免税部分记 AMTRAT0。
# 依据 S 盘副本里 Express 原生写的单（验收 10-26 实录第 10 条：6412NLT 的 HH2200117 AMT01 28734.07 / AMTRAT0 1146，
# 整张免税的退货 SR002100392 AMT01 0 / AMTRAT0 6220.56）。逐行 VAT 没开的账套照同一写法：免税销售额在销项税报表里本就单列。
# CC 照载荷 `vat_fields` 写，不用发新版。
ISVAT_TAXABLE_FIELD = "AMT01"
ISVAT_EXEMPT_FIELD = "AMTRAT0"
VAT_FIELDS_PAYLOAD_VERSION = 9  # 带 `vat_fields` 的载荷：旧版 CC 会按整张税前写应税基数，诚实拒收
# 账套没开逐行 VAT（ISINFO.VAT_INITEM 不是 Y，Express 自己整张计税）而单里有免税商品：按公司问一次怎么写、记住复用（主会话 10-26）
EXEMPT_MODE = "exempt_mode"
EXEMPT_SPLIT, EXEMPT_WHOLE = "split", "whole"  # 按商品免税拆开写（建议）/ 照账套现在的设置整张计税
VAT_REGISTERED = "vat_registered"  # 单据上问一次的那一项：这家公司是不是增值税登记户（记在公司上，`masters.company`）
# 只汇总公司「收银写 Express 起始日」（第一次接上 Express 那天；已接的公司由补丁记成上线当天）及以后的小票：之前的不补写
# （会计可能已手录过、期间可能已关账；验收 10-25）。没有起始日的公司一张都不汇总。销售小票，加上原单同一天卖的退货小票。
# 自连接原单（o）而不是子查询：UPDATE 里子查询读同一张表 MariaDB 不一定认
_TABLES = """`tabERP Sale` s JOIN `tabERP Warehouse` w ON w.name = s.warehouse
    JOIN `tabERP Company` c ON c.name = w.company LEFT JOIN `tabERP Sale` o ON o.name = s.return_against"""
_RECEIPTS = """w.company = %(company)s AND s.status = 'completed'
    AND (s.sale_type = 'sale' OR (s.sale_type = 'refund' AND DATE(o.sold_at) = DATE(s.sold_at)
                                  AND COALESCE(o.express_summary, '') = ''))
    AND c.express_pos_from IS NOT NULL AND DATE(s.sold_at) >= c.express_pos_from"""
_SCOPE = f"FROM {_TABLES} WHERE {_RECEIPTS}"
# 退货单 SR 收的退货小票：原小票已经进了某张日汇总（HS），退货自己还没汇总
_REFUNDS = f"""FROM {_TABLES} WHERE w.company = %(company)s AND s.status = 'completed' AND s.sale_type = 'refund'
    AND COALESCE(s.express_summary, '') = '' AND COALESCE(o.express_summary, '') != ''"""
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
    # 先选再改（不在一条 UPDATE 里自连接判断原小票汇总了没有：原小票同一句里也在改，MariaDB 读到哪个值不确定）
    names = frappe.db.sql_list(f"""SELECT s.name {_SCOPE} AND COALESCE(s.express_summary, '') = ''
        AND DATE(s.sold_at) = %(day)s""", {"company": company, "day": day})
    return _summarize(company, day, HS, names)


def refund_groups(company, before) -> list:
    """这家公司 ``before`` 之前还没写退货单的退货小票按 (退货那天, 原 HS) 分成几组（早的在前，最多 :data:`DAYS_MAX` 组）。"""
    return frappe.db.sql(f"""SELECT DATE(s.sold_at) AS day, o.express_summary AS against {_REFUNDS}
        AND s.sold_at < %(before)s GROUP BY DATE(s.sold_at), o.express_summary ORDER BY day, against LIMIT {DAYS_MAX}""",
                         {"company": company, "before": before}, as_dict=True)


def summarize_refunds(company, day, against):
    """把这家公司 ``day`` 退的、原小票在 ``against``（HS）里的退货小票合成一张退货日汇总并排进写账队列 → 名；没有回 None。"""
    names = frappe.db.sql_list(f"""SELECT s.name {_REFUNDS} AND DATE(s.sold_at) = %(day)s AND o.express_summary = %(against)s""",
                               {"company": company, "day": day, "against": against})
    return _summarize(company, day, SR, names, against=against)


def _summarize(company, day, kind, names, against=None):
    if not names:
        return None
    # 加单别之前建的日汇总单别是空的，都是 HS
    seq = frappe.db.sql(f"""SELECT COUNT(*) FROM `tab{DOCTYPE}` WHERE company = %s AND day = %s
        AND COALESCE(NULLIF(doc_kind, ''), %s) = %s""", (company, day, HS, kind))[0][0] + 1
    doc = frappe.get_doc({"doctype": DOCTYPE, "company": company, "day": day, "seq": seq, "status": "submitted",
                          "doc_kind": kind, "against": against})
    doc.insert(ignore_permissions=True)
    # 再核一次完成状态：选出来之后被作废的小票不进（验收 10-26，毫秒级竞态）
    frappe.db.sql("""UPDATE `tabERP Sale` SET express_summary = %s WHERE name IN %s AND COALESCE(express_summary, '') = ''
        AND status = 'completed'""", (doc.name, tuple(names)))
    sign = _sign(kind)
    totals = frappe.db.sql(f"""SELECT COUNT(*) AS receipts, COALESCE(SUM(s.sale_type = %s), 0) AS count,
        COALESCE(SUM({sign} * s.grand_total), 0) AS total, COALESCE(SUM({sign} * s.vat_amount), 0) AS vat
        FROM `tabERP Sale` s WHERE s.express_summary = %s""", ("refund" if kind == SR else "sale", doc.name),
                           as_dict=True)[0]
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


def pending_refunds(company) -> int:
    """这家公司还没写进 Express 的退货小票张数（「待写退货」，公司页 Express 区）：原小票已进日汇总、退货自己还没进写成的
    退货单（没汇总、退货单在排队 / 等原日汇总 / 停下）。原小票在收银写 Express 起始日之前的不算（那些不写）。"""
    return frappe.db.sql(f"""SELECT COUNT(*) {_REFUNDS.replace("AND COALESCE(s.express_summary, '') = ''", "")}
        AND COALESCE(s.express_summary, '') != o.express_summary
        AND NOT EXISTS (SELECT 1 FROM `tab{DOCTYPE}` d WHERE d.name = s.express_summary AND d.express_state IN %(written)s)""",
                         {"company": company, "written": WRITTEN_STATES})[0][0]


def _sign(kind) -> str:
    """金额符号：现销日汇总里退货小票记负数；退货日汇总里全是退货小票，照正数记（退了多少）。"""
    return "1" if kind == SR else _SIGN


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
        # 现销日汇总合完再合退货单：同一天卖、同一天退、原小票还没汇总的退货已经并进 HS，剩下的才是要写 SR 的
        for group in refund_groups(company.name, today):
            summarize_refunds(company.name, group.day, group.against)
            frappe.db.commit()


def document(name, ref) -> dict:
    """这张日汇总交给写账规则的样子（同采购 / 销售的 `contracts.document`，另带 ``pos``）。

    税额（主会话 10-25）：标价含税，增值税登记户的小票逐行拆出税额（`cashier.vat`），日汇总合计各行税额、明细按各行税前额交
    （尾差由 `contracts.document` 落到税前）；还不知道这家公司是不是登记户（公司上没记、税务局也没查到）时停下，在单据上问一次。"""
    summary = frappe.db.get_value(DOCTYPE, name, ["company", "day", "first_receipt", "last_receipt", "sales_count",
                                                  "doc_kind", "against"], as_dict=True)
    refund = (summary.doc_kind or HS) == SR
    against = None
    if refund:
        # 退货单挂原 HS：原 HS 写进 Express 了才写（要它的 Express 单号；还在排队、停下、转人工的先等，`push.poll` 每分钟再看）
        against = frappe.db.get_value(DOCTYPE, summary.against, ["express_state", "express_docnum", "grand_total", "day"],
                                      as_dict=True) if summary.against else None
        if against and against.express_state == contracts.HANDLED:
            return {"ref": ref, "hold": contracts.REFUND_ORIGINAL_MANUAL}
        if not against or against.express_state not in WRITTEN_STATES or not against.express_docnum:
            return {"ref": ref, "hold": REFUND_WAITING}
        # 原 HS 在上一年度的账套（年度账套分开）：照 Express 原生写进退货那天的账套，挂原单号与原单合计、明细「AVGPR」按平均成本回冲
        # （B3b 实录点 ②，S 盘 4,079 张原生 SR 如此；主会话 10-26 定不再停人工）
        cross_year = _cross_year(summary.company, against.day, summary.day)
        against_book = _book_path(summary.company, against.day) if cross_year else ""
    if not companies.vat_registered(summary.company, lookup=True):
        return {"ref": ref, "hold": f"{contracts.CHOICE}:{VAT_REGISTERED}",
                "needed": [{"kind": VAT_REGISTERED, "subject": "company", "label": "",
                            "options": [{"code": companies.VAT_YES, "name": ""}, {"code": companies.VAT_NO, "name": ""}]}]}
    receipts = frappe.db.sql_list("SELECT name FROM `tabERP Sale` WHERE express_summary = %s", (name,))
    receipt_vat.refresh(receipts)
    sign = _sign(SR if refund else HS)
    sums = frappe.db.sql(f"""SELECT COALESCE(SUM({sign} * s.grand_total), 0) AS total,
        COALESCE(SUM({sign} * s.vat_amount), 0) AS vat FROM `tabERP Sale` s WHERE s.express_summary = %s""",
                         (name,), as_dict=True)[0]
    frappe.db.set_value(DOCTYPE, name, {"grand_total": sums.total, "vat_amount": sums.vat}, update_modified=False)
    summary.grand_total, summary.vat_amount = sums.total, sums.vat
    company = frappe.db.get_value("ERP Company", summary.company, ["tax_id", "branch"], as_dict=True) or {}
    rows = frappe.db.sql(f"""SELECT l.product, l.warehouse, l.unit, SUM({sign} * l.qty) AS qty,
        SUM({sign} * (l.line_total - COALESCE(l.vat_amount, 0))) AS amount, SUM(ABS(COALESCE(l.vat_amount, 0))) AS vat,
        SUM({sign} * l.line_total) AS gross
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
                                       "qty": contracts.d(0), "amount": contracts.d(0), "gross": contracts.d(0),
                                       "stock": bool(product.get("express_stock")) if code else bool(row.product),
                                       "loccod": warehouses.get(row.warehouse, "")})
        if factor != 1:
            line["factor"] = stock_entry._plain(factor)
        line["qty"] += contracts.d(row.qty)
        line["amount"] += contracts.d(row.amount)
        line["gross"] += contracts.d(row.gross)
        # 这一行没拆出税额（免税商品）：CC 桌面版摊各行含税额时不摊税给它（退货单明细的含税额，验收实测 10-26）
        line["vat_exempt"] = line.get("vat_exempt", True) and not contracts.d(row.vat)
    total, vat = contracts.d(summary.grand_total), contracts.d(summary.vat_amount)
    paid = {row.method: contracts.d(row.amount) for row in frappe.db.sql(
        f"""SELECT p.method, SUM({sign} * p.amount) AS amount FROM `tabERP Sale Payment` p
        JOIN `tabERP Sale` s ON s.name = p.parent AND p.parenttype = 'ERP Sale'
        WHERE s.express_summary = %s AND p.method != %s GROUP BY p.method""", (name, CASH), as_dict=True)}
    if refund:
        # 退货单：全是退货小票、照正数记（退回的数量、税前额与退给客户的钱），不冲减
        payments, items = payments_of(total, paid), [line for line in merged.values() if line["qty"] or line["amount"]]
    else:
        # 同一天的退货冲减（共用约定，与 Cowork 同一处定）：全部为 0 整张不写，冲成负数停下交人
        try:
            net = pos_contract.net_day(total, payments_of(total, paid), list(merged.values()))
        except pos_contract.NetError:
            return {"ref": ref, "items": [], "net_negative": True}
        if net is None:
            return {"ref": ref, "items": [], "empty": True}
        payments, items = list(net.payments), net.items
    # 免税行（这一行没拆出税额）只在增值税登记户的单里标：ISVAT AMT01 只记应税部分、免税部分记 AMTRAT0，明细免税行 VATCOD '0'
    exempt = companies.vat_registered(summary.company) == companies.VAT_YES and any(line.get("vat_exempt") for line in items)
    whole = False
    if exempt:
        mode = _exempt_mode(summary.company, summary.day)
        if not mode:
            return {"ref": ref, "hold": f"{contracts.CHOICE}:{EXEMPT_MODE}",
                    "needed": [{"kind": EXEMPT_MODE, "subject": "company", "label": "",
                                "options": [{"code": EXEMPT_SPLIT, "name": ""}, {"code": EXEMPT_WHOLE, "name": ""}]}]}
        exempt = mode == EXEMPT_SPLIT
        # 整张计税只管混有应税商品的单：全是免税商品（小票一分税都没拆出来）的一天照免税写、税额 0，同原生对全免税的单不计税
        # （验收复核测试包 d 10-26）
        if mode == EXEMPT_WHOLE and vat > 0:
            # 照账套现在的设置整张计税（主会话 10-26 选 A）：同 Express 没开逐行 VAT 的账套，整张按含税价倒算税额（合计 × 7/107），
            # 税前 = 合计 − 税额，销项税记录、凭证跟着这个；与收银小票逐行拆出的税额不同，日汇总上标明（`vat_whole_document`）
            from pearnly_erp_portal.cashier import contracts as cashier

            vat = cashier.included_vat(total)
            whole = True
    lines = [{**{key: value for key, value in line.items() if key != "gross" and (key != "vat_exempt" or exempt)},
              "qty": stock_entry._plain(line["qty"]), "amount": contracts.money(line["amount"])} for line in items]
    if whole:
        # 整张计税各行税前 = 含税额 × 100/107（同 Express 没开逐行 VAT 的账套）：按各行含税额摊整张税前，免税商品也照含税价算；
        # CC 桌面版再按税前占比摊回各行含税额就是收银小票的含税额（价含税的单明细记含税，主会话 10-26 转试点窗口发现）
        for line, amount in zip(lines, contracts.spread([contracts.d(line["gross"]) for line in items], total - vat)):
            line["amount"] = contracts.money(amount)
    if exempt:
        # 免税行与应税行分两组各自摊到税前合计（复核 10-26：有整单折扣时统一按比例摊，免税行明细 44.79 而 AMTRAT0 是收银小票按含税额
        # 摊折扣的 45.00）：免税组合计 = 收银小票的免税净额（同 `vat_fields` 的 AMTRAT0），应税组摊其余，明细与销项税记录对得上
        exempt_net = (-1 if refund else 1) * receipt_vat.exempt_total(receipts)
        base = contracts.d(total) - contracts.d(vat)
        groups = ([index for index, line in enumerate(lines) if line.get("vat_exempt")],
                  [index for index, line in enumerate(lines) if not line.get("vat_exempt")])
        for indexes, target in zip(groups, (exempt_net, base - exempt_net)):
            for index, amount in zip(indexes, contracts.spread([contracts.d(lines[i]["amount"]) for i in indexes], target)):
                lines[index]["amount"] = contracts.money(amount)
    body = contracts.document(ref=ref, direction="sales", doc_date=str(summary.day),
                              # 参考号写进 Express 的 YOUREF / 销项税 REFNUM（≤15 字节）：同 Cowork 的 POS 导入用共用约定的
                              # `day_ref`（放不下就缩写，完整起止在 `pos` 里）；10-25 实验栈：「首-尾」27 字被 CC 拦下
                              number=pos_contract.day_ref(summary.first_receipt, summary.last_receipt,
                                                          frappe.utils.getdate(summary.day)),
                              company=company, party={"code": "", "name": CASH_PARTY}, paid=True, has_vat=vat > 0,
                              total=total, vat=vat, lines=lines)
    if exempt:
        # 退货日汇总全是退货小票：免税净额照正数算（`exempt_total` 对退货记负数），与原 HS 同一套规则，两边应税基数对得上。
        # 整张都是免税的也带（AMT01 0、AMTRAT0 全额，同原生 SR002100392），CC 桌面版照样写销项税记录
        fields = vat_fields(receipts, contracts.d(body["subtotal"]), sign=-1 if refund else 1)
        if fields:
            body["vat_fields"] = fields
    frappe.db.set_value(DOCTYPE, name, "vat_whole_document", 1 if whole else 0, update_modified=False)
    body["pos"] = {"first": summary.first_receipt, "last": summary.last_receipt, "count": int(summary.sales_count or 0),
                   "payments": [[label, contracts.money(amount)] for label, amount in payments]}
    if refund:
        # 挂原 HS：Express 单号（SONUM）与原单含税合计（IVCAMT），写账规则据此出退货单 SR（退现金、货回仓）
        body["refund"] = {"against": against.express_docnum, "against_total": contracts.money(against.grand_total),
                          **({"cross_year": True, "against_book": against_book} if cross_year else {})}
    return body


def _cross_year(company, original_day, refund_day) -> bool:
    """原 HS 与退货落在不同的账套（年度账套分开时）：Express 原生跨年退货的写法实录前先停人工（主会话 10-26）。"""
    from pearnly_erp_portal.express import push, target

    row = push._company(company)
    first = (target.pick(row, str(original_day)).get("book") or {}).get("key")
    second = (target.pick(row, str(refund_day)).get("book") or {}).get("key")
    return bool(first and second and first != second)


def _book_path(company, day) -> str:
    """这一天该写的账套所在位置（跨年退货：新年度账套没有现金客户档时 CC 桌面版从原年度这本复制）。"""
    from pearnly_erp_portal.express import push, target

    return str((target.pick(push._company(company), str(day)).get("book") or {}).get("path") or "")


def _exempt_mode(company, day):
    """单里有免税商品时怎么写：账套开了逐行 VAT（或电脑还没回报这一项）拆开写；没开的按公司问一次、记住复用（没问过回空）。"""
    from pearnly_erp_portal.express import choices, facts, push, target

    book = (target.pick(push._company(company), str(day)).get("book") or {}).get("key")
    if not book or facts.vat_initem(facts.load(book)) in (None, "Y"):
        return EXEMPT_SPLIT
    return choices.chosen(company, EXEMPT_MODE, "company")


def vat_fields(receipts, base, sign=1) -> dict:
    """混有免税商品的现销 / 退货日汇总写进 Express 销项税记录的金额栏（`ISVAT_TAXABLE_FIELD` / `ISVAT_EXEMPT_FIELD`）：
    应税基数 = 税前合计 − 免税行净额；没有免税行回空（CC 照整张税前写，同以前）。``sign`` = -1：退货日汇总（全是退货）。"""
    exempt = sign * receipt_vat.exempt_total(receipts)
    if exempt <= 0:
        return {}
    fields = {ISVAT_TAXABLE_FIELD: contracts.money(base - exempt)}
    if ISVAT_EXEMPT_FIELD:
        fields[ISVAT_EXEMPT_FIELD] = contracts.money(exempt)
    return fields


def payments_of(total, paid) -> list:
    """当天各付款方式 → ``[(写法, 金额)]``：非现金照收到的（找零只在现金上；同一天的退货记负数），现金 = 含税合计 − 非现金
    （找零已扣），和恰好等于合计（共用约定的核对）。没有一笔非现金时整天记现金。净额为 0 的付款方式不列。"""
    out = [(LABELS.get(method, method), amount) for method, amount in sorted(paid.items()) if amount != 0]
    cash = contracts.d(total) - sum((amount for _label, amount in out), contracts.d(0))
    if cash or not out:
        out.insert(0, (LABELS[CASH], cash))
    return out


__all__ = ["DOCTYPE", "HS", "EXEMPT_MODE", "ISVAT_EXEMPT_FIELD", "ISVAT_TAXABLE_FIELD", "LABELS", "NET_NEGATIVE", "REFUND_WAITING", "SR",
           "VAT_FIELDS_PAYLOAD_VERSION", "document", "nightly", "payments_of", "pending_days", "receipt_range", "refund_groups",
           "pending_refunds", "summarize", "summarize_refunds", "vat_fields"]
