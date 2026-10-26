"""按单据取打印要的数（Jinja 方法 `pearnly_print_context`，在 Print Format 里 `{% set ctx = pearnly_print_context(doc) %}`）。

- 出库单 → 领料单（ใบเบิกสินค้า / GOODS ISSUE SLIP）；入库单、带入库的采购单 → 收货单（ใบรับสินค้า / GOODS RECEIPT NOTE）。
- 抬头按**单据所属公司**取（出入库单按仓库的公司，采购单按单上的公司），不读当前用户的默认公司：名称、地址、电话、邮箱、Logo。
  接了 Express 的公司名称 / 地址 / 电话同步时以 Express 为准（`express.locks.COMPANY_LOCKED`）。没传 Logo 用 App 自带的 PEARNLY。
  Logo 内嵌成 data URI：PDF 生成器（wkhtmltopdf）拿不到私有文件地址。
- ERP 没有的栏位（领用部门、领用人、申请数、PO / DO 号、运输方式、签字……）一律留空线，不拿建单人之类冒充（打印评估第 4 节）。
- 作废的单照样能打，版面上加「已作废」水印（只在作废单上出现）。
- 第二批（主会话 10-26 审定，`docs/work/2026-10-26-print-batch2-评估.md`）：采购订单、报价单 / 销售单（A4 横，同第一批风格）、
  收银小票（含退货）与交班小结（热敏 80 mm）、盘点（进行中打点数表、结束后打结果表，A4 竖）。抬头公司：采购 / 销售单按单上的公司，
  小票 / 交班按收银台的公司，盘点按仓库的公司。客户 / 供应商档只在属于本单公司或是共用档时读（编码、电话、税号、分店），
  否则只印单上自己存的名称 / 地址。
"""
import base64
import mimetypes

import frappe

from pearnly_erp_portal.printing import contracts

STOCK = "ERP Stock Entry"
PURCHASE = "Purchase Document"
SALES = "ERP Sales Document"
SALE = "ERP Sale"
SHIFT = "ERP Shift"
COUNT = "ERP Stock Count"
COUNT_ITEM = "ERP Stock Count Item"
ISSUE, RECEIPT = "issue", "receipt"
ORDER = "order"  # 采购订单格式（`Pearnly Purchase Order`）调用时带的 kind
DEFAULT_LOGO = ("public", "images", "print", "pearnly-logo.svg")  # 正式品牌包（2026-09-05 定稿）的横版矢量 lockup
FONT_DIR = ("public", "fonts", "print")
FONT_FAMILY = "PearnlyPrint"
FONT_WEIGHTS = {400: "Regular", 700: "Bold"}  # 字体文件 `<家族>-<字重>.ttf`，有哪个嵌哪个


def pearnly_print_context(doc, kind=None) -> dict:
    """只打库里已有、当前用户读得到的单：按名字从库里重新取一遍再算。原生打印接口（`printview.get_html_and_style`）收得下
    页面传来的整份单据 JSON，不重取的话伪造一张单、填上别家公司的供应商，就能把对方的税号印出来（验收 10-26）。
    ``kind``：同一类单据有两种格式时由 Print Format 指定（采购单：收货单 / 采购订单）。"""
    if doc.doctype not in PRINTABLE or not doc.name or not frappe.db.exists(doc.doctype, doc.name):
        _refuse()
    doc = frappe.get_doc(doc.doctype, doc.name)
    if not doc.has_permission("read"):
        _refuse()
    return _printer(doc.doctype)(doc, kind)


def _refuse():
    frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)


def _data_uri(content: bytes, name: str) -> str:
    mime = "image/svg+xml" if name.lower().endswith(".svg") else mimetypes.guess_type(name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def fonts_css() -> str:
    """打印用的泰文字体（随 App 分发的开源字体，`public/fonts/print`）内嵌成 ``@font-face``：PDF 生成器关着本机文件访问、
    也不一定连得上站点自己，内嵌最稳。目录里没有字体文件时回空串（版面退回镜像自带的 Loma）。"""
    import os

    folder = frappe.get_app_path("pearnly_erp_portal", *FONT_DIR)
    names = sorted(name for name in os.listdir(folder) if name.lower().endswith(".ttf")) if os.path.isdir(folder) else []
    faces = []
    for weight, style in FONT_WEIGHTS.items():
        found = next((name for name in names if name.rsplit(".", 1)[0].endswith("-" + style)), None)
        if not found:
            continue
        with open(os.path.join(folder, found), "rb") as handle:
            data = base64.b64encode(handle.read()).decode()
        # `src:` 后换行再写 `url(...)`：Frappe 出 PDF 前的 `scrub_urls` 给「冒号后紧跟 url(」的写法补 ` !important`
        # （给背景图的变通），补在 @font-face 的 src 上整条作废、字体回落 Loma（10-26 实测，第一批的 PDF 也是 Loma）。
        faces.append(f'@font-face {{ font-family: "{FONT_FAMILY}"; font-weight: {weight}; font-style: normal;\n'
                     f'  src:\n    url(data:font/ttf;base64,{data}) format("truetype"); }}')
    return "\n".join(faces)


def logo(company_row, company=None) -> str:
    url = str(company_row.get("logo") or "").strip()
    if url and company:
        # 只认挂在这家公司 Logo 上的文件（`masters.company._attach_logo` 挂的），不按地址去读别处的私有文件
        file_name = frappe.db.get_value("File", {"file_url": url, "attached_to_doctype": "ERP Company",
                                                 "attached_to_name": company}, "name")
        if file_name:
            try:
                # 按路径读原始字节（`File.get_content()` 遇到能按 UTF-8 解码的内容会回字符串）
                with open(frappe.get_doc("File", file_name).get_full_path(), "rb") as handle:
                    return _data_uri(handle.read(), url)
            except (OSError, frappe.DoesNotExistError):
                pass  # 文件丢了：回落 PEARNLY，不让整张单打不出来
    with open(frappe.get_app_path("pearnly_erp_portal", *DEFAULT_LOGO), "rb") as handle:
        return _data_uri(handle.read(), DEFAULT_LOGO[-1])


def header(company) -> dict:
    row = frappe.db.get_value("ERP Company", company, ["company_name", "address", "phone", "contact_email", "logo",
                                                       "tax_id", "branch", "vat_registered"],
                              as_dict=True) if company else None
    row = row or frappe._dict()
    return {"name": row.company_name or "", "address": " ".join(str(row.address or "").split()),
            "phone": row.phone or "", "email": row.contact_email or "", "logo": logo(row, company),
            "tax_id": row.tax_id or "", "branch": contracts.company_branch(row.branch),
            "vat": (row.vat_registered or "") == "yes"}


def _products(names) -> dict:
    names = sorted({name for name in names if name})
    if not names:
        return {}
    return {row.name: row for row in frappe.get_all("ERP Product", filters={"name": ["in", names]},  # bounded: 这张单的商品
                                                    fields=["name", "product_name", "code", "express_code", "unit"],
                                                    limit_page_length=0)}


def _code(product) -> str:
    return (product or {}).get("express_code") or (product or {}).get("code") or ""


def _is_main(warehouse, company) -> bool:
    """主仓 = 这家公司最先建的那个仓（开户自带；接了 Express 的公司第一次同步时它被认作 Express 主仓，`masters._warehouses`）。"""
    if not warehouse or not company:
        return False
    first = frappe.get_all("ERP Warehouse", filters={"company": company}, pluck="name",  # bounded: 最早一个
                           order_by="creation asc", limit=1)
    return bool(first) and first[0] == warehouse


def _party(doctype, name, company, fields) -> frappe._dict:
    """客户 / 供应商档：只在属于本单公司（接 Express 的档记着 `express_company`）或是共用档时读，否则当作没有
    （`masters.access` 同一口径：有 `express_company` 的档只在那家公司可见）。"""
    row = frappe.db.get_value(doctype, name, [*fields, "express_company"], as_dict=True) if name else None
    if not row or (row.express_company and row.express_company != company):
        return frappe._dict()
    return row


def _supplier(name, fallback=None, company=None) -> dict:
    fallback = fallback or {}
    row = _party("ERP Supplier", name, company, ["supplier_name", "tax_id", "address", "phone", "express_code",
                                                 "branch_type", "branch_no"])
    return {"code": row.express_code or "", "name": fallback.get("name") or row.supplier_name or "",
            "address": " ".join(str(fallback.get("address") or row.address or "").split()),
            "phone": row.phone or "", "email": "", "tax_id": fallback.get("tax_id") or row.tax_id or "",
            "branch": fallback.get("branch") or contracts.branch_text(row.branch_type, row.branch_no)}


def _base(kind, doc, company, number, posting_date, remarks, void) -> dict:
    # 单据标题（泰英）是样板版面的一部分，写在模板里（`templates/goods_*.html`）
    return {"kind": kind, "company": header(company), "fonts_css": fonts_css(),
            "number": number or doc.name, "date": contracts.day(posting_date), "remarks": str(remarks or "").strip(),
            "void": bool(void), "currency": "THB"}


def _stock(doc, kind=None) -> dict:
    company = frappe.db.get_value("ERP Warehouse", doc.warehouse, "company")
    products = _products(line.product for line in doc.lines)
    rows = []
    for line in doc.lines:
        product = products.get(line.product) or {}
        cost = contracts.d(line.unit_cost)
        rows.append({"code": _code(product), "name": product.get("product_name") or "",
                     "unit": line.unit or product.get("unit") or "", "qty": contracts.qty(line.qty),
                     "price": contracts.money(cost) if cost is not None else "",
                     "amount": contracts.money(cost * contracts.d(line.qty)) if cost is not None else ""})
    kind = ISSUE if doc.direction == "out" else RECEIPT
    ctx = _base(kind, doc, company, doc.doc_no, doc.posting_date, doc.reason, doc.status == "void")
    warehouse = frappe.db.get_value("ERP Warehouse", doc.warehouse, "warehouse_name") or ""
    ctx.update(pages=contracts.pages(rows), warehouse=warehouse)
    if kind == ISSUE:
        ctx["purpose"] = contracts.issue_purpose(doc.purpose, doc.reason)
    else:
        ctx["supplier"] = _supplier(doc.supplier, company=company)
        ctx["warehouse_check"] = contracts.warehouse_check(_is_main(doc.warehouse, company), warehouse)
    ctx["page_count"] = len(ctx["pages"])
    return ctx


def _purchase(doc, kind=None) -> dict:
    """带入库的采购单打成收货单：供应商取单上的名称 / 税号 / 地址；对方单号不冒充送货单号（DO No. 留空，主会话 10-26）。
    ``kind = order``（`Pearnly Purchase Order` 格式）只给采购订单打采购订单。"""
    if kind == ORDER:
        return _order(doc)
    products = _products(line.product for line in doc.lines)
    rows = []
    stock_lines = [line for line in doc.lines if (line.item_type or "goods") == "goods"]
    for line in stock_lines:
        product = products.get(line.product) or {}
        # 单价按行金额 ÷ 数量（有行折扣时与金额对得上；单据币种原样，表头写单据币种）
        qty = contracts.d(line.qty)
        price = contracts.d(line.line_total) / qty if qty else contracts.d(line.unit_price)
        rows.append({"code": _code(product) or line.code or "", "name": product.get("product_name") or line.description or "",
                     "unit": line.unit or product.get("unit") or "", "qty": contracts.qty(line.qty),
                     "price": contracts.money(price),
                     "amount": contracts.money(line.line_total)})
    warehouse_id = next((line.warehouse for line in stock_lines if line.warehouse), "")
    warehouse = frappe.db.get_value("ERP Warehouse", warehouse_id, "warehouse_name") if warehouse_id else ""
    ctx = _base(RECEIPT, doc, doc.company, doc.system_no, doc.doc_date, doc.notes, doc.status == "void")
    ctx.update(pages=contracts.pages(rows), warehouse=warehouse or "",
               supplier=_supplier(doc.supplier, {"name": doc.supplier_name, "tax_id": doc.supplier_tax_id,
                                                 "address": doc.supplier_address}, doc.company),
               warehouse_check=contracts.warehouse_check(_is_main(warehouse_id, doc.company), warehouse or ""),
               currency=str(doc.currency or "THB").strip().upper() or "THB")
    ctx["page_count"] = len(ctx["pages"])
    return ctx


def _units(doc) -> dict:
    """按台商品选中的台，按明细行号（= 行 idx）分好（`inventory.serials.line_serials` 同一口径）。"""
    out = {}
    for row in doc.get("serials") or []:
        out.setdefault(int(row.line_no or 0), []).append(
            {"serial_no": row.serial_no or "", "secondary_no": row.secondary_no or "", "color": row.color or ""})
    return out


def _trade_rows(doc, *, units=True) -> list:
    """采购 / 销售单明细：编码、品名、数量、单位、单价、折扣、金额；按台商品行下印车号（报价单阶段还没选台）。"""
    products = _products(line.product for line in doc.lines)
    by_line = _units(doc) if units else {}
    rows = []
    for line in doc.lines:
        product = products.get(line.product) or {}
        rows.append({"code": _code(product) or line.get("code") or "",
                     "name": line.description or product.get("product_name") or "",
                     "unit": line.unit or product.get("unit") or "", "qty": contracts.qty(line.qty),
                     "price": contracts.money(line.unit_price),
                     "discount": contracts.money(line.discount) if contracts.d(line.discount) else "",
                     "amount": contracts.money(line.line_total),
                     "sub": contracts.vehicle_lines(by_line.get(int(line.idx or 0), []))})
    return rows


def _totals(rows) -> list:
    """合计区：``[(标签键, 金额, 必印)]`` → ``[{key, value}]``；值为 0 或空的可选项不印。泰英标签在模板 `_labels.html`。"""
    out = []
    for key, value, always in rows:
        if always or contracts.d(value):
            out.append({"key": key, "value": contracts.money(value or 0)})
    return out


def _order(doc) -> dict:
    """采购订单：只给 doc_kind = purchase_order 的单。"""
    if doc.doc_kind != "purchase_order":
        _refuse()
    rows = _trade_rows(doc)
    ctx = _base(ORDER, doc, doc.company, doc.system_no, doc.doc_date, doc.notes, doc.status == "void")
    ctx.update(
        title="order", pages=contracts.pages(rows), currency=_currency(doc),
        party=_supplier(doc.supplier, {"name": doc.supplier_name, "tax_id": doc.supplier_tax_id,
                                       "address": doc.supplier_address,
                                       "branch": contracts.branch_text(doc.supplier_branch_type, doc.supplier_branch_no)},
                        doc.company),
        info=[("required_date", contracts.day(doc.due_date)), ("ref_no", doc.doc_no or ""),
              ("requested_by", doc.requester or ""), ("payment_terms", "")],
        totals=_totals([("subtotal", doc.subtotal, True), ("discount", doc.discount_total, False),
                        ("vat", doc.vat_amount, bool(doc.has_vat)), ("rounding", doc.rounding, False),
                        ("grand_total", doc.grand_total, True), ("wht", doc.wht_amount, False),
                        ("net_payable", doc.net_payable, bool(contracts.d(doc.wht_amount)))]),
        signs=["prepared", "approved", "supplier_ack"])
    ctx["page_count"] = len(ctx["pages"])
    return ctx


def _currency(doc) -> str:
    return str(doc.get("currency") or "THB").strip().upper() or "THB"


def _customer(doc) -> dict:
    """客户：名称 / 地址取单上存的；编码、电话、税号、分店从客户档取（只取本单公司或共用档，主会话 10-26 §12-3）。"""
    row = _party("ERP Customer", doc.customer, doc.company, ["express_code", "contact_phone", "tax_id", "branch_type",
                                                             "branch_no", "customer_name", "address"])
    return {"code": row.express_code or "", "name": doc.buyer_name or row.customer_name or "",
            "address": " ".join(str(doc.address or row.address or "").split()), "phone": row.contact_phone or "",
            "tax_id": row.tax_id or "", "branch": contracts.branch_text(row.branch_type, row.branch_no)}


def _sales(doc, kind=None) -> dict:
    """报价单 / 销售单。销售单标题按税票口径写死：接了 Express 打送货单（并印 Express 单号），没接打非税收据。"""
    from pearnly_erp_portal.express import push

    quotation = doc.doc_kind == contracts.QUOTATION
    linked = (not quotation) and push.linked(doc.company)
    rows = _trade_rows(doc, units=not quotation)
    ctx = _base(doc.doc_kind, doc, doc.company, doc.system_no, doc.doc_date, doc.note, doc.status == "void")
    # 信息框：值是付款方式时标 `method`，模板按付款方式标签表印
    info = [("valid_until", ""), ("payment_terms", "")] if quotation else [
        ("express_no", doc.express_docnum or "") if linked else None,
        ("payment", {"method": doc.payment_method} if doc.payment_method else ""),
        ("paid_date", contracts.day(doc.payment_date)),
        ("paid_amount", contracts.money(doc.paid_amount) if contracts.d(doc.paid_amount) else "")]
    ctx.update(
        title=contracts.sales_title(doc.doc_kind, linked), pages=contracts.pages(rows), currency="THB",
        party=_customer(doc), info=[item for item in info if item],
        totals=_totals([("subtotal", doc.subtotal, True), ("header_discount", doc.header_discount, False),
                        ("vat_included" if doc.price_includes_vat else "vat", doc.vat_amount, bool(doc.has_vat)),
                        ("grand_total", doc.grand_total, True)]),
        signs=["quoted", "approved", "accepted"] if quotation else ["delivered", "received", "collected"])
    ctx["page_count"] = len(ctx["pages"])
    return ctx


def _terminal(name) -> frappe._dict:
    return frappe.db.get_value("ERP POS Terminal", name, ["terminal_name", "company"], as_dict=True) or frappe._dict()


def _user_name(user) -> str:
    return (frappe.db.get_value("User", user, "full_name") or user or "") if user else ""


def _sale(doc, kind=None) -> dict:
    """收银小票（热敏 80 mm）：销售打收据、退货打退货单并印原单号与原因（标题见 `contracts.pos_title`）；公司按收银台。"""
    terminal = _terminal(doc.terminal)
    products = _products(line.product for line in doc.lines)
    units = _units(doc)
    rows = []
    for line in doc.lines:
        product = products.get(line.product) or {}
        rows.append({"name": line.description or product.get("product_name") or "", "qty": contracts.qty(line.qty),
                     "unit": line.unit or product.get("unit") or "", "price": contracts.money(line.unit_price),
                     "discount": contracts.money(line.discount) if contracts.d(line.discount) else "",
                     "amount": contracts.money(line.line_total),
                     "sub": contracts.vehicle_lines(units.get(int(line.idx or 0), []))})
    refund = doc.sale_type == "refund"
    original = ""
    if refund and doc.return_against and frappe.db.exists(SALE, doc.return_against):
        source = frappe.get_doc(SALE, doc.return_against)
        # 原单要同一收银台、当前用户读得到才印单号
        if source.terminal == doc.terminal and source.has_permission("read"):
            original = source.receipt_no or ""
    payments = [{"method": row.method, "amount": contracts.money(row.amount)} for row in doc.get("payments") or []]
    ctx = _base("pos", doc, terminal.company, doc.receipt_no, doc.sold_at, doc.note if refund else "",
                doc.status == "void")
    ctx.update(thermal=True, title=contracts.pos_title(doc.sale_type), rows=rows, refund=refund, original=original,
               time=str(doc.sold_at or "")[11:16], terminal=terminal.terminal_name or "",
               cashier=_user_name(doc.cashier), payments=payments,
               totals=_totals([("subtotal", doc.subtotal, True), ("discount", doc.discount_total, False),
                               ("pos_total", doc.grand_total, True)]),
               vat=contracts.money(doc.vat_amount) if contracts.d(doc.vat_amount) else "",
               change=contracts.money(doc.change_amount) if contracts.d(doc.change_amount) else "",
               paper_mm=contracts.thermal_height_mm(len(rows) + sum(len(row["sub"]) for row in rows),
                                                    len(payments) + 6))
    return ctx


def _shift(doc, kind=None) -> dict:
    """交班小结（热敏 80 mm）：只打已交班的；汇总数用收银台同一份 `shifts.summary`（权限已由读权限核过）。"""
    from pearnly_erp_portal.cashier import shifts

    if doc.status != "closed":
        _refuse()
    terminal = _terminal(doc.terminal)
    data = shifts.summary(doc.name, own_only=False)
    balances = [{"method": row.method, "expected": contracts.money(row.expected_amount),
                 "counted": contracts.money(row.counted_amount), "difference": contracts.money(row.difference)}
                for row in doc.get("balances") or []]
    methods = [{"method": method, "value": contracts.money(amount)}
               for method, amount in (data.get("by_method") or {}).items()]
    ctx = _base("shift", doc, terminal.company, f"{terminal.terminal_name or doc.terminal} #{doc.shift_seq or ''}",
                doc.closed_at, doc.note, False)
    ctx.update(thermal=True, title="shift", terminal=terminal.terminal_name or "",
               cashier=_user_name(doc.cashier), opened=_stamp(doc.opened_at), closed=_stamp(doc.closed_at),
               counts={"sales": data.get("sales_count", 0), "refunds": data.get("refund_count", 0)},
               totals=_totals([("sales", data.get("gross"), True), ("refunds", data.get("refunded"), True),
                               ("net", data.get("net"), True)]),
               methods=methods, balances=balances,
               cash=_totals([("opening_float", doc.opening_float, True), ("expected_cash", doc.expected_cash, True),
                             ("counted_cash", doc.counted_cash, True), ("difference", doc.cash_diff, True)]),
               paper_mm=contracts.thermal_height_mm(len(methods) + 2 * len(balances), 14))
    return ctx


def _stamp(value) -> str:
    return f"{contracts.day(value)} {str(value)[11:16]}".strip() if value else ""


def _count(doc, kind=None) -> dict:
    """盘点：进行中打点数表（盲盘，不印账面数）；结束 / 过账后打结果表（账面 / 实点 / 差异）。明细是独立表，
    读权限已在盘点单上核过（`inventory.access`），这里只按本单取。"""
    company = frappe.db.get_value("ERP Warehouse", doc.warehouse, "company")
    mode = contracts.count_mode(doc.status)
    items = frappe.get_all(COUNT_ITEM, filters={"count": doc.name},  # bounded: 这一张盘点单的品项
                           fields=["code", "product_name", "unit", "location", "book_qty", "counted", "counted_qty",
                                   "difference"], order_by="location asc, code asc, creation asc", limit_page_length=0)
    rows = []
    for item in items:
        row = {"code": item.code or "", "name": item.product_name or "", "unit": item.unit or "",
               "location": item.location or ""}
        if mode == "result":
            row.update(book=contracts.qty(item.book_qty), counted=contracts.qty(item.counted_qty) if item.counted else "",
                       difference=contracts.qty(item.difference) if item.counted else "")
        rows.append(row)
    ctx = _base("count", doc, company, doc.name, doc.posted_at or doc.closed_at or doc.creation, doc.notes, False)
    warehouse = frappe.db.get_value("ERP Warehouse", doc.warehouse, "warehouse_name") or ""
    ctx.update(mode=mode, title="count_sheet" if mode == "sheet" else "count_result",
               pages=contracts.pages(rows, contracts.COUNT_ROWS_PER_PAGE), warehouse=warehouse,
               count_title=doc.title or "", item_total=len(rows),
               counted_total=sum(1 for item in items if item.counted),
               difference_total=sum(1 for item in items if item.counted and contracts.d(item.difference)))
    ctx["page_count"] = len(ctx["pages"])
    return ctx


PRINTABLE = (STOCK, PURCHASE, SALES, SALE, SHIFT, COUNT)


def _printer(doctype):
    return {STOCK: _stock, PURCHASE: _purchase, SALES: _sales, SALE: _sale, SHIFT: _shift, COUNT: _count}[doctype]


__all__ = ["header", "logo", "pearnly_print_context"]
