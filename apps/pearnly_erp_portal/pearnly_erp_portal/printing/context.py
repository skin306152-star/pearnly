"""按单据取打印要的数（Jinja 方法 `pearnly_print_context`，在 Print Format 里 `{% set ctx = pearnly_print_context(doc) %}`）。

- 出库单 → 领料单（ใบเบิกสินค้า / GOODS ISSUE SLIP）；入库单、带入库的采购单 → 收货单（ใบรับสินค้า / GOODS RECEIPT NOTE）。
- 抬头按**单据所属公司**取（出入库单按仓库的公司，采购单按单上的公司），不读当前用户的默认公司：名称、地址、电话、邮箱、Logo。
  接了 Express 的公司名称 / 地址 / 电话同步时以 Express 为准（`express.locks.COMPANY_LOCKED`）。没传 Logo 用 App 自带的 PEARNLY。
  Logo 内嵌成 data URI：PDF 生成器（wkhtmltopdf）拿不到私有文件地址。
- ERP 没有的栏位（领用部门、领用人、申请数、PO / DO 号、运输方式、签字……）一律留空线，不拿建单人之类冒充（打印评估第 4 节）。
- 作废的单照样能打，版面上加「已作废」水印（只在作废单上出现）。
"""
import base64
import mimetypes

import frappe

from pearnly_erp_portal.printing import contracts

STOCK = "ERP Stock Entry"
PURCHASE = "Purchase Document"
ISSUE, RECEIPT = "issue", "receipt"
DEFAULT_LOGO = ("public", "images", "print", "pearnly-logo.svg")  # 正式品牌包（2026-09-05 定稿）的横版矢量 lockup
FONT_DIR = ("public", "fonts", "print")
FONT_FAMILY = "PearnlyPrint"
FONT_WEIGHTS = {400: "Regular", 700: "Bold"}  # 字体文件 `<家族>-<字重>.ttf`，有哪个嵌哪个


def pearnly_print_context(doc) -> dict:
    """只打库里已有、当前用户读得到的单：按名字从库里重新取一遍再算。原生打印接口（`printview.get_html_and_style`）收得下
    页面传来的整份单据 JSON，不重取的话伪造一张单、填上别家公司的供应商，就能把对方的税号印出来（验收 10-26）。"""
    if doc.doctype not in (STOCK, PURCHASE) or not doc.name or not frappe.db.exists(doc.doctype, doc.name):
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    doc = frappe.get_doc(doc.doctype, doc.name)
    if not doc.has_permission("read"):
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    return _stock(doc) if doc.doctype == STOCK else _purchase(doc)


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
    row = frappe.db.get_value("ERP Company", company, ["company_name", "address", "phone", "contact_email", "logo"],
                              as_dict=True) if company else None
    row = row or frappe._dict()
    return {"name": row.company_name or "", "address": " ".join(str(row.address or "").split()),
            "phone": row.phone or "", "email": row.contact_email or "", "logo": logo(row, company)}


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


def _supplier(name, fallback=None) -> dict:
    fallback = fallback or {}
    row = frappe.db.get_value("ERP Supplier", name, ["supplier_name", "tax_id", "address", "phone", "express_code"],
                              as_dict=True) if name else None
    row = row or frappe._dict()
    return {"code": row.express_code or "", "name": fallback.get("name") or row.supplier_name or "",
            "address": " ".join(str(fallback.get("address") or row.address or "").split()),
            "phone": row.phone or "", "email": "", "tax_id": fallback.get("tax_id") or row.tax_id or ""}


def _base(kind, doc, company, number, posting_date, remarks, void) -> dict:
    # 单据标题（泰英）是样板版面的一部分，写在模板里（`templates/goods_*.html`）
    return {"kind": kind, "company": header(company), "fonts_css": fonts_css(),
            "number": number or doc.name, "date": contracts.day(posting_date), "remarks": str(remarks or "").strip(),
            "void": bool(void), "currency": "THB"}


def _stock(doc) -> dict:
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
        ctx["supplier"] = _supplier(doc.supplier)
        ctx["warehouse_check"] = contracts.warehouse_check(_is_main(doc.warehouse, company), warehouse)
    ctx["page_count"] = len(ctx["pages"])
    return ctx


def _purchase(doc) -> dict:
    """带入库的采购单打成收货单：供应商取单上的名称 / 税号 / 地址；对方单号不冒充送货单号（DO No. 留空，主会话 10-26）。"""
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
                                                 "address": doc.supplier_address}),
               warehouse_check=contracts.warehouse_check(_is_main(warehouse_id, doc.company), warehouse or ""),
               currency=str(doc.currency or "THB").strip().upper() or "THB")
    ctx["page_count"] = len(ctx["pages"])
    return ctx


__all__ = ["header", "logo", "pearnly_print_context"]
