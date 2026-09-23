"""One permission-aware query for sales lists, totals and exports.

**收银小票也是销售记录的一种来源**（`source = pos`），所以这一份查询要同时覆盖两处：

· 销售记录表 `ERP Sales Document`（手工录入 / 上传识别 / LINE）；
· 收银小票 `ERP Sale`（收银台落下的单）。

为什么是"合并读取"而不是"并表"：ERPNext 那边只有一张单（Sales Invoice）加
`is_created_using_pos` 标记；我们的两张单是分批长出来的（收银一批、销售一批），
表结构不一样（收银带班次/找零/收款方式，销售带客户/税/附件/识别）。这一轮先把
**看得见的那一层对齐**：同一个列表、同一列形状、同一个「来源」筛选，不动已存的单。
并表留作单独一步。

映射口径（与 `cashier/sales.py` 的落单一致）：收银单号就是系统号（`receipt_no`
已经与销售记录共用 `SI` 序列）、收银单永远是"已收款"、`completed → submitted`。
"""
import frappe

from pearnly_erp_portal.access import guard, rules
from pearnly_erp_portal.inventory import warehouses
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.sales import access, contracts

DOCTYPE = "ERP Sales Document"
POS = "ERP Sale"
POS_LINE = "ERP Sale Line"
SOURCE_POS = contracts.SOURCE_POS
# 收银单落下来就是收满的（顾客当场付钱），所以"已收款"那一档对收银单恒真。
PAYMENT_PAID = "paid"

# 收银小票取这几列就够拼出与销售记录一样的行。
POS_FIELDS = ("name", "receipt_no", "sold_at", "creation", "sale_type", "status",
              "grand_total", "paid_total", "change_amount", "shift", "terminal", "cashier",
              "note")

FIELDS = ("name", "system_no", "doc_date", "creation", "customer", "buyer_name", "party_type",
          "summary", "doc_kind", "source", "status", "payment_status", "payment_method",
          "payment_date", "subtotal", "vat_amount", "grand_total", "paid_amount",
          "stock_posted_at")

# 搜索只搜"人会在搜索框里打的字"：客户、我方单号、明细摘要、备注。
SEARCH_FIELDS = ("buyer_name", "system_no", "summary", "note")


def query(params=None):
    """列表、合计与导出共用的一份条件。"""
    params = params or {}
    filters = [[key, "=", value] for key, value in guard.scope_filters().items()]
    # 一租户多公司：销售记录只看**当前公司**（切换公司改的是用户默认值里的 company）。
    filters.append(["company", "=", companies.active_company()])
    for key in ("doc_kind", "payment_status", "source"):
        value = params.get(key)
        if value:
            filters.append([key, "in" if isinstance(value, list) else "=", value])
    group = params.get("group")
    if group in ("receipt", "quotation"):
        filters.append(["doc_kind", "=", group])
    elif group == "unpaid":
        filters.extend([["payment_status", "!=", "paid"], ["status", "!=", "void"]])
    datefield = "creation" if params.get("date_basis") == "upload" else "doc_date"
    if params.get("month"):
        month_start = frappe.utils.getdate(str(params["month"]) + "-01")
        filters.extend([[datefield, ">=", str(month_start)],
                        [datefield, "<", str(frappe.utils.add_months(month_start, 1))]])
    if params.get("date_from"):
        filters.append([datefield, ">=", str(frappe.utils.getdate(params["date_from"]))])
    if params.get("date_to"):
        end = str(frappe.utils.getdate(params["date_to"])) + (
            " 23:59:59.999999" if datefield == "creation" else "")
        filters.append([datefield, "<=", end])
    text = str(params.get("q") or "").strip()
    or_filters = [[field, "like", "%" + text + "%"] for field in SEARCH_FIELDS] if text else None
    return filters, or_filters, datefield


def _doc_rows(params=None, *, start=0, limit=20):
    """销售记录表那一侧（原样保留）。"""
    filters, or_filters, datefield = query(params)
    data = frappe.get_list(DOCTYPE, filters=filters, or_filters=or_filters, fields=list(FIELDS),
                           distinct=True, order_by=f"{datefield} desc, creation desc",
                           limit_start=max(0, int(start)), limit_page_length=max(0, int(limit)))
    for row in data:
        row["attachment_count"] = frappe.db.count("ERP Sales Attachment", {"parent": row.name})
    return data


def wanted_sources(params=None):
    """这次要看哪些来源；没给就是全部（含收银）。"""
    value = (params or {}).get("source")
    if not value:
        return None
    values = value if isinstance(value, (list, tuple)) else [value]
    wanted = {str(item).strip() for item in values if str(item).strip()}
    return wanted or None


def _pos_scope():
    """收银小票的取数范围：老板看全店，收银员只看自己的单（与收银页同一条规矩）。"""
    scope = [["warehouse", "in", warehouses.names_of() or [""]]]
    if rules.record_scope(guard.current_roles()) == rules.ALL_RECORDS:
        return scope
    return scope + [["cashier", "=", frappe.session.user]]


def _pos_applies(params) -> bool:
    """收银单永远是"收据 + 已收款"；筛选条件把它排除掉时就不取。"""
    params = params or {}
    wanted = wanted_sources(params)
    if wanted is not None and SOURCE_POS not in wanted:
        return False
    kind = params.get("doc_kind")
    if kind and contracts.RECEIPT not in (kind if isinstance(kind, (list, tuple)) else [kind]):
        return False
    if params.get("group") == "quotation":
        return False
    if params.get("group") == "unpaid":
        return False
    status = params.get("payment_status")
    if status:
        asked = {str(item) for item in (status if isinstance(status, (list, tuple)) else [status])}
        if PAYMENT_PAID not in asked:  # 收银单永远是已收款，没点"已收款"就不列它
            return False
    return True


def _pos_rows(params=None, *, start=0, limit=20):
    """收银小票 → 与销售记录同一形状的行。"""
    params = params or {}
    filters = _pos_scope()
    datefield = "creation" if params.get("date_basis") == "upload" else "sold_at"
    if params.get("month"):
        month_start = frappe.utils.getdate(str(params["month"]) + "-01")
        filters += [[datefield, ">=", str(month_start)],
                    [datefield, "<", str(frappe.utils.add_months(month_start, 1))]]
    if params.get("date_from"):
        filters.append([datefield, ">=", str(frappe.utils.getdate(params["date_from"]))])
    if params.get("date_to"):
        end = str(frappe.utils.getdate(params["date_to"])) + (
            " 23:59:59.999999" if datefield == "creation" else " 23:59:59")
        filters.append([datefield, "<=", end])
    status = params.get("status")
    if status:
        filters.append(["status", "=", "completed" if status == contracts.SUBMITTED else status])
    text = str(params.get("q") or "").strip()
    or_filters = [[field, "like", "%" + text + "%"] for field in ("receipt_no", "note")] if text else None
    data = frappe.get_all(POS, filters=filters, or_filters=or_filters, fields=list(POS_FIELDS),
                          order_by=f"{datefield} desc, creation desc",
                          limit_start=max(0, int(start)), limit_page_length=max(0, int(limit)))
    return [_pos_shape(row) for row in data]


def _pos_shape(row) -> dict:
    """把一张收银小票摊成销售记录那一行的样子（列名一一对上，前端只认一套列）。"""
    completed = row.get("status") == "completed"
    return {
        "name": row.get("name"), "system_no": row.get("receipt_no") or row.get("name"),
        "doc_date": str(row.get("sold_at") or row.get("creation") or "")[:10],
        "creation": row.get("creation"),
        "customer": None, "buyer_name": "", "party_type": "",
        "summary": (row.get("note") or ""),
        "doc_kind": contracts.RECEIPT, "source": SOURCE_POS,
        "status": contracts.SUBMITTED if completed else row.get("status"),
        "payment_status": PAYMENT_PAID, "payment_method": None, "payment_date": None,
        "subtotal": None, "vat_amount": None, "grand_total": row.get("grand_total"),
        "paid_amount": row.get("paid_total"), "stock_posted_at": row.get("sold_at"),
        # 收银侧多出来的信息（销售记录的列不用，但小票弹窗与"来源=POS"要认）
        "pos_name": row.get("name"), "shift": row.get("shift"), "terminal": row.get("terminal"),
        "sale_type": row.get("sale_type"), "cashier": row.get("cashier"),
        "change_amount": row.get("change_amount"),
        "attachment_count": 0,
    }


def _attach_pos_summaries(rows):
    """给这一页的收银行补上"卖了什么"（一次查回，不在循环里查库）。"""
    names = [row["pos_name"] for row in rows if row.get("source") == SOURCE_POS and not row["summary"]]
    if not names:
        return
    lines = frappe.get_all(POS_LINE, filters={"parent": ["in", names]},
                           fields=["parent", "description", "product", "qty"],
                           order_by="idx asc", limit_page_length=0)
    grouped: dict = {}
    for line in lines:
        grouped.setdefault(line["parent"], []).append(line)
    for row in rows:
        if row.get("source") != SOURCE_POS or row["summary"]:
            continue
        parts = grouped.get(row["pos_name"]) or []
        row["summary"] = ", ".join(
            f"{(line.get('description') or line.get('product') or '')} ×{line.get('qty')}"
            for line in parts)[:140]


def _sort_key(row):
    return (str(row.get("doc_date") or ""), str(row.get("creation") or ""))


def rows(params=None, *, start=0, limit=20):
    """列表、合计与导出共用的一份取数：销售记录 + 收银小票（后者按来源开关）。"""
    guard.require("sales.read")
    if not _pos_applies(params):
        return _doc_rows(params, start=start, limit=limit)
    # 两侧各取"够切这一页"的条数（limit=0 表示整批），归并排序后再切这一页。
    need = 0 if not limit else max(0, int(start)) + max(0, int(limit))
    merged = sorted(_doc_rows(params, start=0, limit=need)
                    + _pos_rows(params, start=0, limit=need), key=_sort_key, reverse=True)
    window = merged[max(0, int(start)):] if not limit else merged[max(0, int(start)):max(0, int(start)) + int(limit)]
    _attach_pos_summaries(window)
    return window


def page(params=None, *, start=0, limit=20) -> dict:
    """列表一页 + 命中总数（同一个查询，翻页与导出不会各算一份）。"""
    return {"items": rows(params, start=start, limit=limit),
            "total": len(rows(params, limit=0))}


def get(name) -> dict:
    return access.get_document(name).as_dict()
