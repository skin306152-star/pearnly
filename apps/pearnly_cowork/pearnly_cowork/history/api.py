"""Desk-ready backend RPCs. No frontend or external recognition/push is installed here."""
from decimal import Decimal, InvalidOperation

import hashlib
import json

import frappe
from pearnly_cowork.access import scope
from pearnly_cowork.history import document_policy, duplicates, removal, storage

# 列头能点的字段：原生列表的排序回服务端，认不出的字段不许拼进 SQL。
SORT_FIELDS = frozenset(("invoice_no", "invoice_date", "seller_name", "seller_tax", "buyer_name", "buyer_tax",
                         "total_amount", "quality_status", "modified", "creation"))


@frappe.whitelist()
def duplicate_setting():
    return duplicates.get_setting()


@frappe.whitelist(methods=["POST"])
def save_duplicate_setting(enabled):
    return duplicates.save_setting(enabled)


@frappe.whitelist(methods=["POST"])
def choose_document_policy(name, value):
    """复核页对转账凭条 / 预扣税凭证选「录入 / 不录入」：这个租户同类以后都这样（见 `document_policy`）。"""
    return document_policy.choose(name, value)


@frappe.whitelist(methods=["POST"])
def create(workspace, pages, request_key, client=None):
    # This RPC owns a single local creation transaction. MariaDB may report
    # snapshot conflict 1020 instead of duplicate key for concurrent INSERT
    # RETURNING. Restart this read/insert-only request at most twice.
    # Internal workflows use storage directly and own their transaction retry.
    for attempt in range(3):
        try:
            return storage.create_manual(workspace, pages, request_key, client)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist()
def detail(name):
    return storage.project(storage.read(name))


@frappe.whitelist(methods=["POST"])
def save(name, pages, expected_version):
    for attempt in range(3):
        try:
            return storage.update_pages(name, pages, expected_version)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


@frappe.whitelist(methods=["POST"])
def complete(records):
    return storage.complete(records)


@frappe.whitelist(methods=["POST"])
def delete(records):
    """勾选删除（最多 500 条）：回 `{deleted, skipped}`；已推送的跳过（见 `removal`）。"""
    return removal.delete(records)


@frappe.whitelist()
def records(workspace, staged=0, start=0, limit=50):
    scope.require_read()
    scope.require_scope(workspace)
    limit, start = max(1, min(int(limit), 100)), max(0, int(start))
    return frappe.get_list(storage.DOCTYPE, filters={"workspace": workspace, "staged": int(staged) != 0},
        fields=["name", "client", "source", "staged", "record_version", "invoice_no", "invoice_date",
                "seller_name", "total_amount", "quality_status", "confidence", "modified",
                "duplicate_check_status", "duplicate_level", *storage.PUSH_FIELDS],
        order_by="creation desc, name desc", limit_start=start, limit_page_length=limit)


# 列表「推送状态」筛选（2026-10-07）：与状态列同一口径 —— 失败 = 转人工，或还没进推送账本就停下（只有原因、没有状态）。
PUSH_STATES = {"failed": [["last_push_status", "=", "manual"], ["last_push_reason", "is", "set"]],
               "pending": [["last_push_status", "=", "pending"]], "success": [["last_push_status", "=", "success"]]}


@frappe.whitelist()
def workbench(staged=None, quality_status=None, posting_kind=None,
              date_from=None, date_to=None, start=0, limit=20, sort_by=None, sort_order=None, source_file=None,
              push_state=None):
    """录入工作台的队列：读本站点当前用户看得见的识别记录。

    界面上不出现账套（Cowork 不记账、只留记录），所以这里也不收这个参数、不回这个
    字段 —— 行级可见性**不在这里拼条件**，走 DocType 自己的权限查询条件
    （`access/scope.history_query` → `_scope_query`），没有授权的行取不到，
    与 `records(workspace=…)` 是同一条判据。

    `staged`：1 待复核（草稿），0 已完成，空值表示不按它过滤（看全部）。
    工作台默认只看待办这条口径**不在这里偷偷默认** —— 页面把它作为一条看得见的
    筛选条件（`staged` 就是 DocType 自己的 "Draft" 字段）加上去，清掉条件就真的看全部。

    `source_file`：只看同一份原件识别出来的那几张票（一份多票 / 一页多票复核时切换用），
    按原件内的顺序（`source_index`）排；可见性同样走 DocType 的权限条件。

    回 `{items, total}`：原生列表要总数才画得出"加载更多"。计数与列表走**同一条**
    权限判据 —— 再查一次只要主键（与 ERP 列表页的 overview 同一做法），
    不用 `frappe.db.count`（那一条不过 DocType 的权限条件，会数出看不见的行），计数在数据库里做。

    筛选与排序都在这条 SQL 上做（列表是服务端分页的）：`sort_by` 只认白名单里的
    字段，认不出的退回默认排序，不把前端传来的字符串直接拼进 SQL。
    """
    from pearnly_cowork.contracts.history import normalize_posting_kind

    scope.require_read()
    start, limit = max(0, int(start)), max(1, min(int(limit), 100))
    filters = {}
    # 空串与 None 都是"没设这个条件"：`frappe.call` 会把 undefined 传成空串，
    # 直接 `int(None)` 会抛 TypeError（试过）。
    if staged is not None and str(staged).strip() != "":
        filters["staged"] = 1 if int(staged) else 0
    if quality_status:
        filters["quality_status"] = str(quality_status)
    if posting_kind:
        filters["posting_kind"] = normalize_posting_kind(posting_kind)
    if date_from or date_to:
        filters["invoice_date"] = ["between", [date_from or "1900-01-01", date_to or "2999-12-31"]]
    or_filters = PUSH_STATES.get(str(push_state or "")) or None
    if or_filters and len(or_filters) == 1:
        filters[or_filters[0][0]] = or_filters[0][2]
        or_filters = None
    order_by = "modified desc, name desc"
    if source_file:
        filters["source_file"] = str(source_file)
        order_by = "source_index asc, name asc"
    elif sort_by in SORT_FIELDS:
        order_by = "{0} {1}, name desc".format(
            sort_by, "asc" if str(sort_order or "").lower().startswith("asc") else "desc")
    fields = ["name", "client", "source", "staged", "record_version", "invoice_no",
              "invoice_date", "seller_name", *storage.PARTY_FIELDS, "total_amount", "quality_status", "confidence",
              "filename", "page_count", "modified", "duplicate_check_status", "duplicate_level",
              "review_reasons", "owner", "intake_target", *storage.PUSH_FIELDS]
    if sort_by == "total_amount" and not source_file:
        # 金额存的是 Decimal 文本（合同口径），数据库按文字排会得到 1200 < 13375 < 2140 < 450 这种顺序；
        # 框架的 order_by 又不许写表达式。所以与下面计数同一条权限查询取全部行，按数值排好再分页。
        rows = frappe.get_list(storage.DOCTYPE, filters=filters, or_filters=or_filters, fields=["name", "total_amount"],
            order_by="name desc", limit_page_length=0)
        rows.sort(key=lambda row: (_amount(row.total_amount), row.name),
                  reverse=not str(sort_order or "").lower().startswith("asc"))
        page = [row.name for row in rows[start:start + limit]]
        found = {row.name: row for row in frappe.get_list(storage.DOCTYPE,
            filters={**filters, "name": ["in", page]}, fields=fields, limit_page_length=0)} if page else {}
        return {"items": [found[name] for name in page if name in found], "total": len(rows)}
    items = frappe.get_list(storage.DOCTYPE, filters=filters, or_filters=or_filters, fields=fields,
        order_by=order_by, limit_start=start, limit_page_length=limit)
    return {"items": items, "total": _total(filters, start, limit, items, or_filters)}


TOTAL_SECONDS = 20


def _total(filters, start, limit, items, or_filters=None):
    """列表总数（原生列表画「已显示 / 总数」和「加载更多」用）。2026-10-02 m6：每次刷新都数一遍全部可见行（1.2 万行时
    0.2–0.7 秒），页面每 5–60 秒自动刷新一次，是列表 p95 约 2 秒的大头。
    - 翻页（``start`` > 0）不数，回 ``None``，页面沿用第一页拿到的总数；
    - 第一页没装满：总数就是这几行，不用数；
    - 第一页装满了才数，同一个人、同一组筛选 20 秒内沿用上一次的数（新票在列表里马上出现，只是总数最多晚 20 秒），
      且不小于这一页已有的行数。
    数法不变：同一条权限查询条件、在数据库里数（不把全部主键取回来）。"""
    if start:
        return None
    if len(items) < limit:
        return len(items)
    key = "cowork:history:total:" + hashlib.sha256(json.dumps([frappe.session.user, filters, or_filters or []], sort_keys=True,
                                                                default=str).encode()).hexdigest()
    try:
        cached = frappe.cache.get_value(key)
    except Exception:
        cached = None
    if cached is None:
        counted = frappe.get_list(storage.DOCTYPE, filters=filters, or_filters=or_filters,
            fields=[{"COUNT": "name", "as": "total"}], order_by="", limit_page_length=0)
        cached = int((counted[0].get("total") if counted else 0) or 0)
        try:
            frappe.cache.set_value(key, cached, expires_in_sec=TOTAL_SECONDS)
        except Exception:
            pass
    return max(int(cached), len(items))



def _amount(value):
    """排序用的数值；读不出来的（空、非数字）排在最小一端。"""
    try:
        return Decimal(str(value).replace(",", "")) if value not in (None, "") else Decimal("-Infinity")
    except InvalidOperation:
        return Decimal("-Infinity")
