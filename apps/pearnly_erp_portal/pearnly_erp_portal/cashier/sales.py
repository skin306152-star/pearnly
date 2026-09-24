"""收银落单：销售、退货、小票号。

几条不变量：

1. **一笔收银只落一笔销售、只产生一次库存影响。** 幂等锚是 `client_uuid`，重复提交
   返回同一张单，不重复扣库存。
2. **收款未结清不能落单**（结算规则在 `contracts.settle`），零元单不收付款。
3. **班次必须开着**：带了班次就用它；收银员没带班次时用本人当前开着的班，一个都没有
   就拒绝 —— 没有班的销售在交班时对不上账。
4. **退货必须有原单**，且退货数量不得超过原单已售数量；退货把库存加回来。
"""

import frappe
from frappe import _
from frappe.utils import now, nowdate

from pearnly_erp_portal.access import guard, listing, rules
from pearnly_erp_portal.cashier import contracts, shifts, terminals
from pearnly_erp_portal.inventory import positions, stock, warehouses
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.numbering import service as numbering

DT = "ERP Sale"
PRODUCT = "ERP Product"

NOT_FOUND = "Sale not found"
LINES_REQUIRED = "Provide at least one line item"
SHIFT_REQUIRED = "Open a shift before selling"
SHIFT_CLOSED = "This shift is closed"
INVALID = "Invalid value"
REFUND_NEEDS_ORIGINAL = "A refund needs the original sale"
REFUND_TOO_MUCH = "A refund cannot be more than the original sale"
ALREADY_VOID = "This sale is already voided"
# 收了这家公司没开的收款方式（建公司时勾的那几种之外）。
METHOD_NOT_ENABLED = "This payment method is not enabled for this company"

LIST_FIELDS = ("name", "receipt_no", "customer", "sold_at", "sale_type", "status", "shift",
               "terminal", "cashier", "warehouse", "return_against", "subtotal", "discount_total",
               "vat_amount", "grand_total", "paid_total", "change_amount", "note")


def _customer(name) -> str:
    """收银台上的客户（可空 = 散客）。给了就必须是主档里启用着的客户。"""
    value = str(name or "").strip()
    if not value:
        return ""
    row = frappe.db.get_value("ERP Customer", value, ["customer_name", "is_active"], as_dict=True)
    if not row or not row.is_active:
        frappe.throw(_("Customer not found"))
    return value


def _customer_names(names) -> dict:
    """客户内部的 `CUS-#####` → 抬头。列表与汇总屏要给**人看的名字**，不是单号。"""
    wanted = sorted({str(n) for n in names if n})
    if not wanted:
        return {}
    return {row["name"]: row["customer_name"] for row in frappe.get_all(
        "ERP Customer", filters={"name": ["in", wanted]},
        fields=["name", "customer_name"], limit_page_length=0)}


def _get(name):
    if not name or not frappe.db.exists(DT, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    from pearnly_erp_portal.cashier.access import require_record

    doc = frappe.get_doc(DT, name)
    require_record(doc)
    return doc


def _receipt_no(doc) -> str:
    """收银单号：与销售记录**同一套编号定义**，从同一个计数器续号。

    用户 2026-09-23 口径："收银这边的每一笔订单编号，要和销售记录的订单编号同步，
    业务逻辑自动延续"。所以这里不再自己按天发 `R20260923-0001`（那样是**另一套**号，
    两边各发各的、还会撞），改成走共享取号 `numbering.allocate(direction="sales")`：
    与销售记录共用 `sales-SI-{佛历年}` 那一行计数器与同一把行锁，两个入口开的单落在
    **同一个序列**里连续（谁先开谁拿前一个号）。

    历史数据里的 `R…` 号按原样留着：它不是本系统的号
    （`numbering.contracts.split` 认不出这种形状），不改写已经给出去的号码。
    """
    day = str(doc.sold_at or nowdate())[:10]
    return numbering.allocate(
        direction="sales", on=day,
        exists=lambda value: bool(frappe.db.exists(DT, {"receipt_no": value})))


def _resolve_shift(shift, terminal):
    """没带班次时用本人当前开着的班；一个都没有就拒绝（否则交班对不上账）。"""
    if shift:
        doc = shifts._own_shift(shift)
        if doc.status != contracts.OPEN:
            frappe.throw(_(SHIFT_CLOSED))
        return doc
    current = shifts.current_shift(terminal=terminal)
    if not current:
        frappe.throw(_(SHIFT_REQUIRED))
    return frappe.get_doc("ERP Shift", current["shift"]["name"])


def _stock_lines(lines) -> list[dict]:
    """只有带商品的行才动库存；服务行不动。"""
    out = []
    for line in lines:
        if not line.get("product"):
            continue
        product = frappe.db.get_value(PRODUCT, line["product"], ["name", "unit"], as_dict=True)
        if not product:
            frappe.throw(_("Product not found"), frappe.DoesNotExistError)
        out.append({"product": product["name"], "qty": line["qty"],
                    "unit": line.get("unit") or product["unit"],
                    "warehouse": line.get("warehouse"), "location": line.get("location", "")})
    return out


def create_sale(*, lines, payments, sold_at=None, shift=None, terminal=None,
                discount_total=0, has_vat=False, vat_rate=7, warehouse=None, client_uuid=None,
                note=None, customer=None) -> dict:
    """落一笔销售。

    `has_vat` 默认 **False**：收银台上顾客付的就是货架标价，直接当应收，不再往上加
    7%。要按不含税价开票的场景由调用方显式传 True（含税拆分属开票侧，见销售记录批次）。

    `customer` 可空（= 散客）。给了就核一下主档里确实有、且启用着 —— 收银台上选错一个停用客户，
    账单就挂到别人名下去了。
    """
    guard.require("cashier.write")
    if not isinstance(lines, list) or not lines:
        frappe.throw(_(LINES_REQUIRED))

    if client_uuid:
        existing = frappe.db.get_value(DT, {"client_uuid": client_uuid}, "name")
        if existing:
            _get(existing)
            return {"name": existing, "deduped": True}

    shift_doc = _resolve_shift(shift, terminal)
    terminal_doc = frappe.get_doc("ERP POS Terminal", terminals.resolve(terminal or shift_doc.terminal))
    # **先定这台收银台真正卖货的那个仓**，再铺行（与商品区那条 `counter.warehouse_for`
    # 同一口径 —— 界面上显示的可用量就是按它算的）。
    # 两个来源：单据上明确给的（`warehouse`）→ 用它；否则就是**收银台自己配的那个仓**
    # （`terminals.place_of`；"默认仓"那个概念已经删了，没配仓就是明确报错，不猜）。
    # 顺序要紧：**先把仓定死再铺行**。以前 `positions.prepare` 会回落"商品自己的默认仓"，
    # 于是行落到商品仓上、单据却写着收银台的仓（实测过：单据 `WH-00174`、行 `WH-00101`，
    # 库存从行那个仓出）。那个商品级默认仓本轮已经删掉，商品区/单据/行三处从此一个仓。
    warehouse_id = (warehouses.required(warehouse, company=terminal_doc.company)
                    if str(warehouse or "").strip() else terminals.place_of(terminal_doc))
    lines = [positions.prepare(line, warehouse_id) for line in lines]

    try:
        totals = contracts.sale_totals(lines, discount_total=discount_total,
                                       has_vat=has_vat in (True, 1, "1", "true"), vat_rate=vat_rate)
        normalized, paid_total, change = contracts.settle(payments, totals["grand_total"])
    except contracts.SettlementError as exc:
        frappe.throw(f"{_(INVALID)}: {exc.detail}")
    # 收款方式也得是**这家公司开了的那几种**（建公司时勾的，见 `masters/company.payment_methods_of`）：
    # 服务端兜底，免得关掉"转账"之后老页面还能塞一张转账进来。
    allowed = companies.payment_methods_of(terminal_doc.company)
    rejected = [row["method"] for row in normalized if row.get("method") not in allowed]
    if rejected:
        frappe.throw(_(METHOD_NOT_ENABLED))

    doc = frappe.get_doc({
        "doctype": DT,
        "customer": _customer(customer),
        "sold_at": sold_at or now(),
        "sale_type": contracts.SALE,
        "status": "completed",
        "shift": shift_doc.name,
        "terminal": terminal_doc.name,
        "cashier": frappe.session.user,
        "warehouse": warehouse_id,
        "subtotal": totals["subtotal"],
        "discount_total": totals["discount_total"],
        "vat_amount": totals["vat_amount"],
        "grand_total": totals["grand_total"],
        "paid_total": paid_total,
        "change_amount": change,
        "client_uuid": client_uuid,
        "note": note,
        "lines": [
            {"product": line.get("product"), "description": line.get("description"),
             "qty": line["qty"], "unit": line.get("unit"),
             "warehouse": line.get("warehouse"), "location": line.get("location", ""),
             "unit_price": line["unit_price"], "discount": line.get("discount", 0),
             "line_total": contracts.line_total(line["qty"], line["unit_price"],
                                                line.get("discount", 0))}
            for line in lines
        ],
        "payments": normalized,
    })
    doc.receipt_no = _receipt_no(doc)
    doc.insert(ignore_permissions=True)
    _move_stock(doc, direction="out")
    frappe.db.commit()
    return {"name": doc.name, "receipt_no": doc.receipt_no, "grand_total": float(doc.grand_total),
            "change_amount": float(doc.change_amount or 0), "deduped": False}


def _move_stock(doc, *, direction: str) -> dict | None:
    """动库存。销售出库、退货入库；幂等锚是单据名，重复调用不会扣两次。"""
    from pearnly_erp_portal.inventory import stock

    lines = _stock_lines([{"product": row.product, "qty": row.qty, "unit": row.unit,
                          **positions.saved(row, doc.warehouse)}
                          for row in doc.get("lines") or []])
    if not lines:
        return None
    # `direction` 是**货的走向**：out = 货离店，in = 货回店。销售出货，退货与作废回货。
    movement_type = "sale_out" if direction == "out" else "stock_in"
    return stock.apply_lines(
        warehouse=doc.warehouse, movement_type=movement_type, lines=lines,
        posting_date=str(doc.sold_at)[:10],
        ref_type="sale" if doc.sale_type == contracts.SALE else "refund",
        ref_id=doc.name, doc_no=doc.receipt_no, client_uuid=f"sale:{doc.name}:{direction}",
        # 收银已经过了 cashier.write 这道闸；卖货的出货不是在"管理库存"。
        internal=True,
    )


def pos_export_lines(names) -> list[dict]:
    """收银小票的明细，按销售记录明细的字段名给出（导出把两者写进同一张表）。

    两边明细字段基本同名（`ERP Sale Line` 少了 `line_no`/`vat_applicable`/`discount_pct`），
    缺的那几列留空即可 —— 导出表头取自销售明细的 meta，所以这里只要给出能对上的键。
    """
    names = [name for name in (names or []) if name]
    if not names:
        return []
    lines = frappe.get_all("ERP Sale Line", filters={"parent": ["in", names]},
                           fields=["parent", "product", "description", "qty", "unit", "unit_price",
                                   "discount", "line_total", "warehouse", "location", "idx"],
                           order_by="parent asc, idx asc", limit_page_length=0)
    for line in lines:
        line["line_no"] = line.get("idx")
    return lines


# 列头排序白名单。客户那一列显示的是抬头（`customer_name` 是回填的），库里只有 link，
# 所以它不在这里 —— 那一列头保持不可点。
SORT_FIELDS = {
    "receipt_no": "receipt_no", "sold_at": "sold_at", "sale_type": "sale_type",
    "status": "status", "grand_total": "grand_total",
}


def list_sales(*, shift=None, cashier=None, date_from=None, date_to=None, sale_type=None,
               status=None, q=None, limit=50, start=0, current_only=False,
               sort_by=None, sort_order=None) -> list[dict]:
    """销售记录。非管理员只能看自己的单（与旧线上"成员只看自己录入的记录"一致）。"""
    if current_only:
        guard.require("cashier.read")
        from pearnly_erp_portal.cashier.shifts import current_shift
        active = current_shift()
        if not active:
            return []
        shift = active["shift"]["name"]
    else:
        guard.require("receipts.read")
    # 一租户多公司：只看当前公司那几个仓库的小票。
    filters: dict = dict(warehouses.scope_filter())
    if rules.record_scope(guard.current_roles()) != rules.ALL_RECORDS:
        filters["cashier"] = frappe.session.user
    elif cashier:
        filters["cashier"] = cashier
    if shift:
        filters["shift"] = shift
    if sale_type:
        filters["sale_type"] = sale_type
    if status:
        filters["status"] = status
    if date_from and date_to:
        filters["sold_at"] = ["between", [f"{date_from} 00:00:00", f"{date_to} 23:59:59"]]
    or_filters = None
    if q:
        # 搜索框的口径（收银台「最近订单」那格写的是"按单据编号或客户名称搜索"）：
        # 单号、内部单号，**以及客户抬头** —— 客户名不在本表上，先按名字取出那几家的编号再挂进来
        # （`customer` 是 Link，列表里显示的是抬头，见 `_customer_names`）。
        term = f"%{q}%"
        or_filters = [["receipt_no", "like", term], ["name", "like", term]]
        customers = frappe.get_all("ERP Customer", filters={"customer_name": ["like", term]},
                                   pluck="name", limit_page_length=0)
        if customers:
            or_filters.append(["customer", "in", customers])
    rows = frappe.get_list(DT, filters=filters, or_filters=or_filters, fields=list(LIST_FIELDS),
                           order_by=listing.order_by(sort_by, sort_order, SORT_FIELDS, "sold_at desc"),
                           limit_start=int(start),
                           limit_page_length=int(limit))
    # 每行带上客户抬头（列表显示的是人看的名字，不是 `CUS-#####`）。
    names = _customer_names([row.get("customer") for row in rows])
    for row in rows:
        row["customer_name"] = names.get(row.get("customer"), "")
    return rows


def get_sale(name) -> dict:
    guard.require("cashier.read" if guard.has("cashier.read") else "receipts.read")
    doc = _get(name)
    if (rules.record_scope(guard.current_roles()) != rules.ALL_RECORDS
            and doc.cashier != frappe.session.user):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    data = {field: doc.get(field) for field in LIST_FIELDS}
    # 收银员显示**用户名**（用户 2026-09-24："全局可以涉及到姓名的地方，全部改为用户名"）：
    # 汇总屏那行"Sold by"读的是 `owner`，页面上不能再摆一个邮箱/姓名出来。
    from pearnly_erp_portal.team import service as team

    data["cashier_name"] = team.username_of(doc.cashier)
    data["customer_name"] = _customer_names([doc.customer]).get(doc.customer, "")
    data["lines"] = [
        {"name": row.name, "product": row.product, "description": row.description, "qty": row.qty,
         "original_line": row.original_line, **positions.public(row, doc.warehouse),
         "unit": row.unit, "unit_price": row.unit_price, "discount": row.discount,
         "line_total": row.line_total}
        for row in doc.get("lines") or []]
    data["payments"] = [
        {"method": row.method, "amount": row.amount, "ref": row.ref}
        for row in doc.get("payments") or []]
    data["refunds"] = frappe.get_all(
        DT, filters={"return_against": doc.name, "status": "completed"},
        fields=["name", "receipt_no", "grand_total"], limit_page_length=0)
    return data


def refund(name, *, lines=None, payments=None, reason=None, client_uuid=None) -> dict:
    """退货：按原单退，数量不得超过已售；库存按原样加回。"""
    guard.require("cashier.write" if guard.has("cashier.write") else "receipts.write")
    frappe.db.sql("select name from `tabERP Sale` where name=%s for update", (name,))
    if client_uuid:
        existing = frappe.db.get_value(DT, {"client_uuid": client_uuid}, "name")
        if existing:
            _get(existing)
            return {"name": existing, "deduped": True}
    original = _get(name)
    if (rules.record_scope(guard.current_roles()) != rules.ALL_RECORDS
            and original.cashier != frappe.session.user):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    if original.status != "completed":
        frappe.throw(_(ALREADY_VOID))
    if original.sale_type != contracts.SALE:
        frappe.throw(_(REFUND_NEEDS_ORIGINAL))

    already = frappe.get_all(DT, filters={"return_against": original.name, "status": "completed"},
                             pluck="name")
    remaining = {row.name: contracts.d(row.qty) for row in original.lines}
    # Pre-upgrade refunds had no source-line key. Allocate those to matching positions only.
    for refund_name in already:
        rows = frappe.get_all("ERP Sale Line", filters={"parent": refund_name},
                              fields=["original_line", "product", "warehouse", "location", "qty", "unit"],
                              limit_page_length=0)
        for row in rows:
            quantity = contracts.d(row.qty)
            for source in original.lines:
                if row.original_line:
                    matches = source.name == row.original_line
                else:
                    matches = (source.product == row.product and source.unit == row.unit
                               and positions.saved(source, original.warehouse)
                               == positions.saved(row, original.warehouse))
                if matches:
                    used = min(remaining[source.name], quantity)
                    remaining[source.name] -= used
                    quantity -= used
                if quantity <= 0:
                    break

    requested = lines or [{"original_line": row.name, "qty": remaining[row.name]}
                          for row in original.lines if remaining[row.name] > 0]
    wanted = []
    for line in requested:
        candidates = [row for row in original.lines if (
            row.name == line.get("original_line") if line.get("original_line") else
            row.product == line.get("product")
            and ("location" not in line or (row.location or "") == line["location"])
            and (not line.get("warehouse") or (row.warehouse or original.warehouse) == line["warehouse"]))]
        if len(candidates) != 1:
            frappe.throw(_("Select the original sale line"))
        source = candidates[0]
        quantity = contracts.d(line.get("qty"))
        if quantity <= 0 or quantity > remaining[source.name]:
            frappe.throw(_(REFUND_TOO_MUCH))
        remaining[source.name] -= quantity
        wanted.append({"product": source.product, "qty": quantity, "unit": source.unit,
                       "unit_price": source.unit_price, "description": source.description,
                       "discount": contracts.d(source.discount) * quantity / contracts.d(source.qty),
                       "original_line": source.name, **positions.saved(source, original.warehouse)})
    if not wanted:
        frappe.throw(_(REFUND_TOO_MUCH))

    try:
        totals = contracts.sale_totals(wanted, has_vat=False, vat_rate=0)
        normalized, paid_total, change = contracts.settle(payments or [], totals["grand_total"])
    except contracts.SettlementError as exc:
        frappe.throw(f"{_(INVALID)}: {exc.detail}")

    doc = frappe.get_doc({
        "doctype": DT, "sold_at": now(), "sale_type": contracts.REFUND, "status": "completed",
        "shift": original.shift, "terminal": original.terminal, "cashier": frappe.session.user,
        "warehouse": original.warehouse, "return_against": original.name,
        "subtotal": totals["subtotal"], "vat_amount": totals["vat_amount"],
        "grand_total": totals["grand_total"], "paid_total": paid_total, "change_amount": change,
        "client_uuid": client_uuid, "note": reason,
        "lines": [{"product": line["product"], "description": line.get("description"),
                   "qty": line["qty"], "unit": line.get("unit"),
                   "original_line": line["original_line"],
                   "warehouse": line["warehouse"], "location": line["location"],
                   "unit_price": line["unit_price"], "discount": line.get("discount", 0),
                   "line_total": contracts.line_total(line["qty"], line["unit_price"],
                                                      line.get("discount", 0))}
                  for line in wanted],
        "payments": normalized,
    })
    doc.receipt_no = _receipt_no(doc)
    doc.insert(ignore_permissions=True)
    _move_stock(doc, direction="in")
    frappe.db.commit()
    return {"name": doc.name, "receipt_no": doc.receipt_no,
            "grand_total": float(doc.grand_total), "return_against": original.name}


def void_sale(name, *, reason=None) -> dict:
    """作废：把库存还回去，单据留痕。已退过的单不允许作废。"""
    guard.require("cashier.write" if guard.has("cashier.write") else "receipts.write")
    frappe.db.sql("select name from `tabERP Sale` where name=%s for update", (name,))
    doc = _get(name)
    if (rules.record_scope(guard.current_roles()) != rules.ALL_RECORDS
            and doc.cashier != frappe.session.user):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    if doc.status != "completed":
        frappe.throw(_(ALREADY_VOID))
    if frappe.db.exists(DT, {"return_against": doc.name, "status": "completed"}):
        frappe.throw(_("Void the refunds first"))
    stock.reverse_movements(ref_type="sale" if doc.sale_type == contracts.SALE else "refund",
                            ref_id=doc.name, client_uuid=f"sale-void:{doc.name}", reason=reason)
    doc.status = "void"
    doc.void_reason = reason
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}
