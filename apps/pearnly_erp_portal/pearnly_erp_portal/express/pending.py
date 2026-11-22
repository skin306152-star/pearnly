"""ERP 版 CC 的「待处理」（ADR-020 同一形状，2026-11-02）：本人看得到的公司里，写账本没成、要人处理的单据：采购、销售，
以及同样写账本的收银日汇总 / 退货单、出入库（入库、出库、调拨）、过账后的盘点（第 6 步账本列改通用时补齐，主会话 11-02 同意）。

- 从单据上现有的状态现算，不新增存储。范围 = `contracts.needs_person`：停下等人（要人选一次、单据本身不对、电脑转了人工…，
  不含每分钟会自己再试的）、失败、写进去了但核对不上。单据要是「可以写了」的（采购、销售、出入库、日汇总已提交，盘点已过账；
  作废、草稿不算）。
- 只回 Cowork 页面已有的形状（`PendingItem`），字段是子集：`action` 一律 ``open``，`url` 是这张单在 ERP 里的页面（点开去那里处理，
  选一次、再写一次、标记已处理都在单据页的 Express 弹窗上）：采购 / 销售 / 盘点是单据页，出入库是对应的入库 / 出库 / 调拨页并打开
  这张单，收银日汇总是收银记录页并打开这张日汇总的 Express 弹窗。归哪家公司：单据上的公司；出入库与盘点按仓库的公司。
- 权限：公司在本人可见公司里（`masters.company.allowed_companies`），有这类单据的读权限（与单据上 Express 弹窗同一判据
  `express.api.CAPABILITIES`；出入库另要这个方向的权限，同出入库单的读权限），员工只看本人录入的（`guard.scope_filters`），
  同单据列表。收银日汇总是每晚系统合的，员工看不到（同收银记录只给管理员看日汇总）。
- **读账本状态的地方只有 `_ledger_rows` 一个函数**（单据上的通用账本栏 ``ledger_state`` / ``ledger_reason``，评估 2026-11-02
  §12 第 6 条）。
- 状态变了（共用写账通道交回 `push.job_changed`）：这张单进出「待处理」时，经共用层 `projects.announce` 给能看这家公司的人推
  ``pearnly.project``（``upsert``，``part = "pending"``），页面重取计数与列表（`notify`）。
"""
from __future__ import annotations

import re
from typing import NamedTuple

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.express import contracts

PURCHASE = "Purchase Document"
SALES = "ERP Sales Document"
STOCK = "ERP Stock Entry"
COUNT = "ERP Stock Count"
POS_DAY = "ERP POS Day Summary"


class Kind(NamedTuple):
    capability: str  # 读权限（同 `express.api.CAPABILITIES` 的查看权）
    route: str  # 单据页（出入库按方向另取，`STOCK_ROUTES`）
    party: str  # 对方名称字段（没有对方的单据为空）
    number: str  # 单号字段
    date: str  # 日期字段
    total: str  # 金额字段（没有金额的单据为空）
    ready: str  # 「可以写了」的状态（同 `push.ready`）
    by_warehouse: bool  # 单据上没有公司栏，按仓库的公司


KINDS = {PURCHASE: Kind("purchase.read", "pearnly-purchase", "supplier_name", "system_no", "doc_date", "grand_total",
                        "submitted", False),
         SALES: Kind("sales.read", "pearnly-sales", "buyer_name", "system_no", "doc_date", "grand_total", "submitted", False),
         POS_DAY: Kind("cashier.read", "pearnly-pos-receipts", "", "name", "day", "grand_total", "submitted", False),
         STOCK: Kind("stock.read", "", "", "doc_no", "posting_date", "", "submitted", True),
         COUNT: Kind("count.read", "pearnly-stock-count", "", "title", "posted_at", "", "Posted", True)}
STOCK_ROUTES = {"in": "pearnly-stock-in", "out": "pearnly-stock-out", "transfer": "pearnly-stock-transfer",
                "adjust": "pearnly-stock-adjust"}  # 物料调整（岳父 11-06）
# 写账前置判定的单号前缀（`express.precheck`）：采购、销售同代录；收银日汇总 / 退货单、出入库单、盘点另起
PRECHECK_PREFIXES = {"P": PURCHASE, "S": SALES, "D": POS_DAY, "T": STOCK, "C": COUNT}
PENDING_MAX = 500  # 每类单据最多看这么多张（同 Cowork 待处理的粗筛上限）
LIMIT_MAX = 200
# 停下的原因码 → 文案词条（与单据页 `public/desk/transactions.js` 的 EXPRESS_REASONS 同一张表，契约测试比对）
REASONS = {
    "express.company_tax_missing": "express.reason.taxMissing", "express.account_set_missing": "express.reason.noBook",
    "express.no_writer": "express.reason.noWriter", "express.masters_reading": "express.reason.reading",
    "express.account_period_mismatch": "express.reason.periodOutside",
    "express.stock_book_missing": "express.reason.noStockBook", "express.pos_net_negative": "express.reason.posNetNegative",
    "express.pos_refund_waiting": "express.reason.refundWaiting", "pos_refund_items_invalid": "express.reason.refundItems",
    "express.pos_refund_cross_year": "express.reason.refundCrossYear",
    "express.pos_refund_original_manual": "express.reason.refundOriginalManual",
    "express.warehouse_unmapped": "express.reason.warehouseUnmapped",
    "express.item_unmapped": "express.reason.itemUnmapped",
    "express.operator_owned": "express.reason.operatorOwned",
    "express.printed_total_missing": "express.reason.printedTotalMissing",
    "express.printed_total_mismatch": "express.reason.printedTotalMismatch",
    "express.cc_desktop_not_connected": "No computer connected", "express.cc_desktop_offline": "express.reason.offline",
    "express.cc_desktop_not_yours": "express.reason.notYours",
    "express.voided_after_write": "express.reason.voidedAfterWrite",
    # 账本是 MR.ERP 的公司（`mrerp.contracts`；开关 MRERP_LEDGER_ENABLED 关着时不会出现）
    "mrerp.not_configured": "mrerp.reason.notConfigured", "mrerp.login_missing": "mrerp.reason.loginMissing",
    "mrerp.account_busy": "mrerp.reason.accountBusy",
    "mrerp.importing": "mrerp.reason.importing",
    # 写账件被撤下（共用通道 `cc_desktop.jobs.cancel` 的原因，11-15 线上右栏露出英文 cancelled）：撤销 / 换了一件 / 来源作废 → 已取消；
    # 断开 Express 时撤的 → 未写入（已断开）
    "cancelled": "Cancelled", "replaced": "Cancelled", "voided": "Cancelled",
    "disconnected": "express.state.detached", "express.disconnected": "express.state.detached",
}
# 电脑回报的「[错误码] 原文」里认得的码（同单据页 EXPRESS_CODES）；其余码用 `express.error.<码>`，没有译文的原样
CODES = {"PERIOD_LOCKED": "express.reason.periodLocked", "STOCK_INSUFFICIENT": "express.reason.stockInsufficient"}
# MR.ERP 导入结论存的原因：结果不明是「mrerp.result_unknown: <码>」；失败是共用层的「码」「码:细节」「码; 原文」（不带方括号；
# 大写 ERR_ 码与小写防呆码），
# 译文在 `mrerp.error.<码>`（同 Cowork `intake.error.<码>` 的四语），没有译文的原样
MRERP_UNKNOWN = "mrerp.result_unknown"
_MRERP_CODE = re.compile(r"^(ERR_[A-Z0-9_]+|[a-z][a-z0-9_]*)(?=$|[:;])")  # 共用层的 ERR_ 码与防呆码（mrerp_credit_note、date_future…）
_BARE_CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
# 「码:细节」也只是码（low_confidence:vendor_missing,invoice_no_missing）：没有译文时同样不原样给人看
_CODE_DETAIL = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*:[a-z0-9_,.:]*$")  # 只有小写码、没有原文的原因（cancelled、cc_desktop.result_unknown…）
STATES = {contracts.HELD: "Pending", contracts.FAILED: "Failed", contracts.MISMATCH: "Mismatch"}


def line_key(doctype, name) -> str:
    """LINE 卡片「保存」后在等这张单写账结果的键（`pearnly_core.line_ui.receipt`，`integrations.handlers._await`）。"""
    return f"erp:doc:{doctype}:{name}"


def line_changed(doctype, name, values) -> None:
    """账本栏刚写了新状态（`express.push._set` / `mrerp.push._set`）：有了结论（`contracts.ledger_settled`，或清空了状态）而 LINE
    上有人在等这张的结果，就排提交后的短任务回结果（用户 11-22）。没人在等只读一次 Redis。"""
    if "ledger_state" not in values:
        return
    state = values["ledger_state"]
    if state is not None and not contracts.ledger_settled(state, values.get("ledger_reason")):
        return
    from pearnly_core.line_ui import receipt as line_receipt

    line_receipt.wake(line_key(doctype, name))


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
    if reason.startswith(MRERP_UNKNOWN):
        return _("mrerp.reason.resultUnknown")
    bare = _MRERP_CODE.match(reason)
    if bare:
        # 写入前通用检查的码（`express_rules.push` 的 mapper / confidence / ledger：low_confidence:vendor_missing、
        # bad_or_missing_date…）用 `express.fail.<码>`；MR.ERP 的用 `mrerp.error.<码>`（11-20 线上「需要处理」露出 low_confidence:…）
        for key in (f"express.fail.{bare.group(1)}", f"mrerp.error.{bare.group(1)}"):
            text = _(key)
            if text != key:
                return text
    code = _error_code(reason)
    if code in CODES:
        return _(CODES[code])
    if code:
        key = f"express.error.{code}"
        text = _(key)
        if text != key:
            return text
    if _BARE_CODE.match(reason) or _CODE_DETAIL.match(reason):
        return _(STATES.get(state, "Pending"))  # 认不得的原因码不原样给人看（11-15 右栏露出 cancelled）：说状态
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


def _directions(user=None) -> list:
    """出入库单本人能看的方向（同出入库单的读权限 `inventory.access.entry_permission`）。"""
    return [key for key in STOCK_ROUTES if guard.has("stock." + key, user=user)]


def _ledger_rows(doctype, *, companies=None, names=None, attention=True, user=None) -> list:
    """**账本状态只在这里读**（单据上的通用账本栏 ``ledger_state`` / ``ledger_reason``），回的行统一成同名的键。

    - ``attention``（默认）：这几家公司（``companies``）里「写账本没成、要人处理」的单，可以写了的、员工只看本人录入的，
      新 → 旧，最多 `PENDING_MAX` 张；出入库只看本人能看的方向；
    - ``attention=False`` + ``names``：这几张单此刻的账本状态（状态变了时比对进出「待处理」用，不按人筛）。"""
    kind = KINDS[doctype]
    fields = ["name", "status", kind.date, kind.number, "ledger_state", "ledger_reason", "modified"]
    fields += [field for field in (kind.party, kind.total) if field]
    fields += ["warehouse"] if kind.by_warehouse else ["company"]
    fields += ["direction"] if doctype == STOCK else []
    owners = {}
    if attention:
        if not companies:
            return []
        if kind.by_warehouse:
            owners = {row.name: row.company for row in frappe.get_all(  # bounded: 这几家公司的仓
                "ERP Warehouse", filters={"company": ["in", list(companies)]}, fields=["name", "company"],
                limit_page_length=0)}
            if not owners:
                return []
            mine = {"warehouse": ["in", list(owners)]}
        else:
            mine = {"company": ["in", list(companies)]}
        if doctype == STOCK:
            directions = _directions(user)
            if not directions:
                return []
            mine["direction"] = ["in", directions]
        filters = {**mine, "status": kind.ready, "ledger_state": ["in", list(contracts.ATTENTION_STATES)],
                   **guard.scope_filters(user)}
    else:
        if not names:
            return []
        filters = {"name": ["in", list(names)]}
    rows = frappe.get_all(doctype, filters=filters, fields=fields, order_by="modified desc, name desc",
                          limit_page_length=PENDING_MAX)
    out = []
    for row in rows:
        if attention and not contracts.needs_person(row.ledger_state, row.ledger_reason):
            continue
        company = row.get("company")
        if kind.by_warehouse:
            company = owners.get(row.warehouse) if attention else \
                frappe.db.get_value("ERP Warehouse", row.warehouse, "company") if row.warehouse else None
        out.append({"doctype": doctype, "name": row.name, "company": company or "", "status": row.status,
                    "ready": row.status == kind.ready, "date": str(row.get(kind.date) or "")[:10],
                    "number": row.get(kind.number) or row.name, "party": (row.get(kind.party) or "") if kind.party else "",
                    "total": row.get(kind.total) if kind.total else None, "direction": row.get("direction") or "",
                    "ledger_state": row.ledger_state or "", "ledger_reason": row.ledger_reason or "",
                    "modified": str(row.modified or "")})
    return out


def ledger_of(doctype, name) -> dict | None:
    """一张单此刻的账本状态（`_ledger_rows` 读）；不是写账本的单据、没有这张回 None。"""
    if doctype not in KINDS or not name:
        return None
    found = _ledger_rows(doctype, names=[name], attention=False)
    return found[0] if found else None


def needs_person(row) -> bool:
    """`ledger_of` 的一行算不算「待处理」（同 `_ledger_rows` 的口径：可以写了、状态要人处理）。"""
    return bool(row) and bool(row.get("ready")) and contracts.needs_person(row.get("ledger_state"),
                                                                          row.get("ledger_reason"))


def _amount(value) -> str:
    return contracts.money(value) if value not in (None, "") else ""


def _url(row) -> str:
    route = STOCK_ROUTES.get(row["direction"], "") if row["doctype"] == STOCK else KINDS[row["doctype"]].route
    return f"/desk/{route}/{row['name']}"


def _item(row, no_book=None) -> dict:
    item = {"document": row["name"], "company": row["company"], "doctype": row["doctype"],
            "reason_code": reason_code(row["ledger_state"], row["ledger_reason"]),
            # 「没有账套」说出日期与已连接各本的期间（用户 11-14；每家公司只查一次，`target.no_book_texts`）
            "reason_label": (no_book or {})[row["company"]](row["date"])
            if row["ledger_reason"] == contracts.NO_BOOK and row["company"] in (no_book or {}) and row["date"]
            else reason_label(row["ledger_state"], row["ledger_reason"]),
            "invoice_no": row["number"], "counterparty": row["party"], "amount": _amount(row["total"]),
            "date": row["date"], "filename": "", "month": row["date"][:7], "action": "open", "url": _url(row)}
    if item["reason_code"] == "choice_needed":
        # 要人选的单：带写账前置判定的单号（`express.precheck`），对话尾部的确认卡片按它判、答（单据页照旧能打开）
        item["doc_id"] = precheck_id(row["doctype"], row["name"])
    return item


def precheck_id(doctype, name) -> str:
    """单据 → 写账前置判定的单号（``P:<名>`` 等）；不是写账本的单据回空。"""
    prefix = next((key for key, value in PRECHECK_PREFIXES.items() if value == doctype), "")
    return f"{prefix}:{name}" if prefix and name else ""


def _items(companies, user=None) -> list:
    rows = []
    for doctype, kind in KINDS.items():
        if guard.has(kind.capability, user=user):
            rows += _ledger_rows(doctype, companies=companies, user=user)
    rows.sort(key=lambda row: (row["modified"], row["name"]), reverse=True)
    stopped = {row["company"] for row in rows if row["ledger_reason"] == contracts.NO_BOOK and row["company"]}
    no_book = None
    if stopped:
        from pearnly_erp_portal.express import target

        no_book = target.no_book_texts(stopped)
    return [_item(row, no_book) for row in rows]


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
    """侧栏用：按公司计数与总数（`counts`）。`months`（Code 版 CC B3）：按「公司 · 月份」的数与本人这家这个月的对话
    （共用层 `sessions.pending_months`，同 Cowork，只读）。"""
    from pearnly_core.assistant import sessions

    items = _items(visible())
    return {**counts(items), "months": sessions.pending_months(items)}


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
    finally:
        _operator_receipt(doctype, name)


def _operator_receipt(doctype, name) -> None:
    """代录事件源的只读入口（共用层，统一对话框架 14.2）：代录的单据编号是 ``P:<名>`` / ``S:<名>``（``operator.KINDS``）；CC 放票
    提交出的单，代录任务里记的是放票时的收单记录 ``I:<记录>``，一并交上（11-17 验收：只交 ``P:`` 时任务按旧编号找不到，一直「排队中」）。"""
    try:
        from pearnly_core.express_operator import feed
        from pearnly_erp_portal.express import operator

        prefix = next((key for key, value in operator.KINDS.items() if value[0] == doctype), "")
        if prefix:
            feed.receipt([f"{prefix}:{name}", *operator.intake_ids(doctype, name)])
    except Exception:
        frappe.log_error(title="ERP operator receipt failed")


__all__ = ["KINDS", "PRECHECK_PREFIXES", "REASONS", "STOCK_ROUTES", "counts", "ledger_of", "matches", "needs_person", "notify", "pending", "pending_counts",
           "precheck_id", "reason_code", "reason_label"]
