"""ERP 版 CC 的「待处理」（ADR-020 同一形状，2026-11-02）：本人看得到的公司里，写账本没成、要人处理的采购 / 销售单。

- 从单据上现有的状态现算，不新增存储。范围 = `contracts.needs_person`：停下等人（要人选一次、单据本身不对、电脑转了人工…，
  不含每分钟会自己再试的）、失败、写进去了但核对不上。单据要是已提交的（作废、草稿不算）。
- 只回 Cowork 页面已有的形状（`PendingItem`），字段是子集：`action` 一律 ``open``，`url` 是这张单在 ERP 里的页面（点开去那里处理，
  选一次、再写一次、标记已处理都在单据页上）。归哪家公司就是单据上的公司。
- 权限：公司在本人可见公司里（`masters.company.allowed_companies`），有这类单据的读权限，员工只看本人录入的（`guard.scope_filters`），
  同单据列表。
- **读账本状态的地方只有 `_ledger_rows` 一个函数**（后面「账本状态」改成通用字段时只改它）。
- 状态变了（共用写账通道交回 `push.job_changed`）：这张单进出「待处理」时，经共用层 `projects.announce` 给能看这家公司的人推
  ``pearnly.project``（``upsert``，``part = "pending"``），页面重取计数与列表（`notify`）。
"""
from __future__ import annotations

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.express import contracts

PURCHASE = "Purchase Document"
SALES = "ERP Sales Document"
# 单据类型 → (读权限, 单据页的路由, 对方名称字段)
KINDS = {PURCHASE: ("purchase.read", "pearnly-purchase", "supplier_name"),
         SALES: ("sales.read", "pearnly-sales", "buyer_name")}
PENDING_MAX = 500  # 每类单据最多看这么多张（同 Cowork 待处理的粗筛上限）
LIMIT_MAX = 200
# 停下的原因码 → 文案词条（与单据页 `public/desk/transactions.js` 的 EXPRESS_REASONS 同一张表，契约测试比对）
REASONS = {
    "express.company_tax_missing": "express.reason.taxMissing", "express.account_set_missing": "express.reason.noBook",
    "express.no_writer": "express.reason.noWriter", "express.masters_reading": "express.reason.reading",
    "express.stock_book_missing": "express.reason.noStockBook", "express.pos_net_negative": "express.reason.posNetNegative",
    "express.pos_refund_waiting": "express.reason.refundWaiting", "pos_refund_items_invalid": "express.reason.refundItems",
    "express.pos_refund_cross_year": "express.reason.refundCrossYear",
    "express.pos_refund_original_manual": "express.reason.refundOriginalManual",
    "express.warehouse_unmapped": "express.reason.warehouseUnmapped",
    "express.item_unmapped": "express.reason.itemUnmapped",
    "express.operator_owned": "express.reason.operatorOwned",
    "express.cc_desktop_not_connected": "CC Desktop not connected", "express.cc_desktop_offline": "express.reason.offline",
}
# 电脑回报的「[错误码] 原文」里认得的码（同单据页 EXPRESS_CODES）；其余码用 `express.error.<码>`，没有译文的原样
CODES = {"PERIOD_LOCKED": "express.reason.periodLocked", "STOCK_INSUFFICIENT": "express.reason.stockInsufficient"}
STATES = {contracts.HELD: "Pending", contracts.FAILED: "Failed", contracts.MISMATCH: "Mismatch"}


# ---------------- 纯判断 ----------------


def reason_code(state, reason) -> str:
    """粗分（同 Cowork `PendingItem.reason_code` 的取值）：要人选 / 失败 / 其余停下。"""
    if str(reason or "").startswith(contracts.CHOICE):
        return "choice_needed"
    return "failed" if state == contracts.FAILED else "manual"


def _error_code(reason) -> str:
    text = str(reason or "")
    if text.startswith("[") and "]" in text:
        code = text[1:text.index("]")]
        if code and all(ch.isupper() or ch.isdigit() or ch == "_" for ch in code):
            return code
    return ""


def reason_label(state, reason) -> str:
    """给人看的一句（按当前语言）：认得的原因码给译文；要人选一次的、没有原因的写状态；电脑回报的码有译文给译文，没有原样。"""
    reason = str(reason or "")
    if not reason or reason.startswith(contracts.CHOICE):
        return _(STATES.get(state, "Pending"))
    if reason in REASONS:
        return _(REASONS[reason])
    code = _error_code(reason)
    if code in CODES:
        return _(CODES[code])
    if code:
        key = f"express.error.{code}"
        text = _(key)
        if text != key:
            return text
    return reason


def counts(items) -> dict:
    """`{"total": n, "companies": {公司: n}, "unassigned": 0}`（同 Cowork `pending.counts`；ERP 的单都有公司）。"""
    by_company = {}
    for item in items:
        by_company[item["company"]] = by_company.get(item["company"], 0) + 1
    return {"total": sum(by_company.values()), "companies": by_company, "unassigned": 0}


def matches(item, search) -> bool:
    """页面搜索的口径（同 Cowork `pending.matches`）：单号、对方、单据号、原因、金额、日期。"""
    needle = str(search or "").strip().lower()
    if not needle:
        return True
    parts = (item.get("invoice_no"), item.get("counterparty"), item.get("document"), item.get("reason_label"),
             item.get("amount"), item.get("date"))
    return needle in " ".join(str(part or "") for part in parts).lower()


# ---------------- 读 ----------------


def visible(user=None) -> list:
    from pearnly_erp_portal.masters import company as companies

    return list(companies.allowed_companies(user or frappe.session.user))


def _ledger_rows(doctype, *, companies=None, names=None, attention=True, user=None) -> list:
    """**账本状态只在这里读**（``express_state`` / ``express_reason``；后面改成通用「账本状态」时只改这里），回的行统一成
    ``ledger_state`` / ``ledger_reason``。

    - ``attention``（默认）：这几家公司（``companies``）里「写账本没成、要人处理」的单，已提交的、员工只看本人录入的，
      新 → 旧，最多 `PENDING_MAX` 张；
    - ``attention=False`` + ``names``：这几张单此刻的账本状态（状态变了时比对进出「待处理」用，不按人筛）。"""
    party = KINDS[doctype][2]
    if attention:
        if not companies:
            return []
        filters = {"company": ["in", list(companies)], "status": "submitted",
                   "express_state": ["in", list(contracts.ATTENTION_STATES)], **guard.scope_filters(user)}
    else:
        if not names:
            return []
        filters = {"name": ["in", list(names)]}
    rows = frappe.get_all(doctype, filters=filters,
                          fields=["name", "company", "status", "doc_date", "system_no", party, "grand_total",
                                  "express_state", "express_reason", "modified"],
                          order_by="modified desc, name desc", limit_page_length=PENDING_MAX)
    out = []
    for row in rows:
        if attention and not contracts.needs_person(row.express_state, row.express_reason):
            continue
        out.append({"doctype": doctype, "name": row.name, "company": row.company, "status": row.status,
                    "date": str(row.doc_date or ""), "number": row.system_no or "", "party": row.get(party) or "",
                    "total": row.grand_total, "ledger_state": row.express_state or "",
                    "ledger_reason": row.express_reason or "", "modified": str(row.modified or "")})
    return out


def ledger_of(doctype, name) -> dict | None:
    """一张单此刻的账本状态（`_ledger_rows` 读）；不是这两类单、没有这张回 None。"""
    if doctype not in KINDS or not name:
        return None
    found = _ledger_rows(doctype, names=[name], attention=False)
    return found[0] if found else None


def needs_person(row) -> bool:
    """`ledger_of` 的一行算不算「待处理」（同 `_ledger_rows` 的口径：已提交、状态要人处理）。"""
    return bool(row) and row.get("status") == "submitted" and contracts.needs_person(row.get("ledger_state"),
                                                                                       row.get("ledger_reason"))


def _amount(value) -> str:
    return contracts.money(value) if value not in (None, "") else ""


def _item(row) -> dict:
    route = KINDS[row["doctype"]][1]
    return {"document": row["name"], "company": row["company"], "doctype": row["doctype"],
            "reason_code": reason_code(row["ledger_state"], row["ledger_reason"]),
            "reason_label": reason_label(row["ledger_state"], row["ledger_reason"]),
            "invoice_no": row["number"], "counterparty": row["party"], "amount": _amount(row["total"]),
            "date": row["date"], "filename": "", "month": row["date"][:7], "action": "open",
            "url": f"/desk/{route}/{row['name']}"}


def _items(companies, user=None) -> list:
    rows = []
    for doctype, (capability, _route, _party) in KINDS.items():
        if guard.has(capability, user=user):
            rows += _ledger_rows(doctype, companies=companies, user=user)
    rows.sort(key=lambda row: (row["modified"], row["name"]), reverse=True)
    return [_item(row) for row in rows]


def pending(company=None, limit=None, search=None) -> dict:
    """要人处理的单（当前用户看得到的），新 → 旧：`{"items": [...], "count": n}`；给了 `company` 只看这家（不在本人可见
    公司里的回空）。"""
    try:
        limit = max(1, min(int(limit or LIMIT_MAX), LIMIT_MAX))
    except (TypeError, ValueError):
        limit = LIMIT_MAX
    companies = visible()
    if company:
        companies = [name for name in companies if name == company]
    items = [item for item in _items(companies) if matches(item, search)]
    return {"items": items[:limit], "count": len(items)}


def pending_counts() -> dict:
    """侧栏用：按公司计数与总数（`counts`）。"""
    return counts(_items(visible()))


# ---------------- 状态变了 ----------------


def notify(doctype, name, before) -> None:
    """单据的账本状态刚改过（共用写账通道交回的同一事务里）：``before`` 是改之前的 `ledger_of`。进出「待处理」时给能看
    这家公司的人推一次（提交后）。推不出去不影响写回。"""
    if doctype not in KINDS:
        return
    try:
        after = ledger_of(doctype, name)
        if not after or not after["company"] or needs_person(before) == needs_person(after):
            return
        from pearnly_erp_portal.masters import cc_service

        cc_service.announce(after["company"], part="pending")
    except Exception:
        frappe.log_error(title="ERP pending announce failed")


__all__ = ["KINDS", "REASONS", "counts", "ledger_of", "matches", "needs_person", "notify", "pending", "pending_counts",
           "reason_code", "reason_label"]
