"""POS 导出 → 录入工作台的逐日销售票（以当前用户身份；不识别、不调模型、不扣费，与对账直读 Excel / CSV 同一口径）。

一份导出（原件已由 `files.service` 建好）：读表 → 取这家公司记过的版式（没有就认列，认不准回问题，人答了记住）→ 逐日汇总 →
冻结原件的逻辑分段清单（一天一段，`pos_day`）→ 一天一张票（`history.recognition.persist_batch`，按原件幂等）→ 推送目标抄到票上。
之后推送走现有推送链（选择记忆、账套规则、复核表、推送账本），载荷 3 带付款方式转账凭证（`push/express/sales_mapper.py`）。

只推 Express（付款方式拆分按 Express 原生记法写凭证）。不查重（用户 2026-10-08）：同一份导出换名再放会再记一次。
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import service as connections
from pearnly_cowork.files import service as files
from pearnly_cowork.history import storage
from pearnly_cowork.history.recognition import persist_batch
from pearnly_cowork.ocr.pages import assess_confidence
from pearnly_cowork.pos_import import contracts, layouts, tables
from pearnly_core.express_rules.targets.identity import account_set_company, normalize_express_account_key

ENGINE = "pos_import_v1"
TARGET = "express"


def _today_year() -> int:
    return datetime.now(ZoneInfo("Asia/Bangkok")).year


def company_key(connection, account_set_key) -> str:
    """版式按公司记：连接 + 账套所属公司（目录里账套的公司名，同一家公司跨年度的账套共用）；读不到公司名用账套本身。"""
    wanted = normalize_express_account_key(account_set_key)
    company = ""
    try:
        catalog = connections.targets(connection)
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.clear_messages()
        catalog = {}
    for row in catalog.get("account_sets") or []:
        if isinstance(row, dict) and normalize_express_account_key(row.get("source_id")) == wanted:
            company = account_set_company(row)
            break
    return f"{connection}|{company or wanted}".casefold()[:140]


def _failed(error: contracts.PosError) -> dict:
    return {"status": "failed", "code": error.code, "details": {key: str(value)[:80] for key, value in
                                                                 (error.details or {}).items()}}


def _require_target(connection, account_set_key):
    if not connection or not account_set_key:
        raise contracts.PosError("pos.target_required")
    if connections.adapter_of(connection) != TARGET:
        raise contracts.PosError("pos.not_express")


def _table(source):
    return tables.read(files.read_bytes(source), source.filename)


def _layout(workspace, company, headers, rows, answers=None):
    """→ ``(版式或 None, 还要问的问题, 指纹, 问的时候记住的版式的修订)``；答了的、认得出不用问的记下。

    记住的版式也要看这份导出里有没有没登记过的状态值（`contracts.pending`）：有就照样停下问那几个值，答了合进记住的
    版式（不直接失败，文件夹任务里停着的那份答完接着走）。

    自动记下的版式（``auto``，当初认得出、没问人）按现在的认列规则重认：重认也不用问就换上并记下（认列规则改进后同一
    版式跟着用新的，例如 2026-10-13 起「VAT 7%」认作税额列）；重认要问（这份导出日 / 月分不清等）就照旧用记住的。
    人答过的版式不动。"""
    fingerprint = contracts.fingerprint(headers)
    known = layouts.remembered(workspace, company, fingerprint)
    revision = layouts.revision(known)
    if answers:
        detection = contracts.apply_answers(headers, rows, answers, base=known)
        if not detection.questions:
            layouts.remember(workspace, company, fingerprint, headers, detection.layout, origin="person")
        return (None if detection.questions else detection.layout), detection.questions, fingerprint, revision
    if known and layouts.origin(workspace, company, fingerprint) == "auto":
        fresh = contracts.detect(headers, rows)
        if not fresh.questions and contracts.valid_layout(fresh.layout):
            if fresh.layout != known:
                layouts.remember(workspace, company, fingerprint, headers, fresh.layout, origin="auto")
            return fresh.layout, [], fingerprint, revision
    if known:
        detection = contracts.pending(headers, rows, known)
        return (None if detection.questions else known), detection.questions, fingerprint, revision
    detection = contracts.detect(headers, rows)
    if detection.questions or not contracts.valid_layout(detection.layout):
        return None, detection.questions, fingerprint, revision
    layouts.remember(workspace, company, fingerprint, headers, detection.layout, origin="auto")
    return detection.layout, [], fingerprint, revision


def _needed(headers, rows, questions, fingerprint, company, revision):
    """``layout_revision``：停下时这家公司这份版式记的样子（没有是空串），文件夹任务据此判断人答过没有。"""
    return {"status": "layout_needed", "fingerprint": fingerprint, "company": company, "layout_revision": revision,
            "headers": [header for header in headers if header], "samples": contracts.samples(headers, rows),
            "questions": questions}


def layout_changed(workspace, connection, account_set_key, held) -> bool:
    """文件夹任务里停着等认列的那份导出现在该不该重读（便宜地看一眼，不重读文件）：这家公司的这份版式自停下以后记上
    / 改过了，或者公司键变了（账套目录这次读得到、上次读不到，答好的版式记在另一个键下）——都重读，由 `admit` 按此刻的
    公司键再判断一次。``held``：停下时 `admit` 回的 ``company`` / ``fingerprint`` / ``layout_revision``。"""
    company = company_key(connection, account_set_key)
    if company != held.get("company"):
        return True
    current = layouts.revision(layouts.remembered(workspace, company, held.get("fingerprint") or ""))
    return current != (held.get("layout_revision") or "")


def preview(source_file, *, workspace, connection, account_set_key, answers=None, posting_kind=None):
    """认列 / 回答认列问题，不建票：回 ``layout_needed``（表头、前几行、问题）、``failed`` 或 ``ready``（逐日汇总）。
    答完的版式记在这家公司名下，之后同版式的导出（含文件夹任务里停着的那份）直接用。"""
    scope.require("intake")
    source = files.get_source(source_file)
    scope.require_scope(workspace, source.client or None)
    try:
        _require_target(connection, account_set_key)
        headers, rows, first_row = _table(source)
        company = company_key(connection, account_set_key)
        layout, questions, fingerprint, revision = _layout(workspace, company, headers, rows, answers)
        if layout is None:
            return _needed(headers, rows, questions, fingerprint, company, revision)
        found, voids = contracts.parse(headers, rows, layout, anchor_year=_today_year(),
                                       with_items=posting_kind == "stock", first_row=first_row)
    except contracts.PosError as error:
        return _failed(error)
    return {"status": "ready", "fingerprint": fingerprint, "company": company, "layout": layout, "voids": voids,
            "days": [contracts.summary(day) for day in found]}


def _document(source, found, fingerprint, voids):
    """冻结进原件的逻辑分段清单：一天一段，只记行号区间（不存单元格），免得把整份导出抄进清单。"""
    from pearnly_cowork.ocr.schema import Page
    from pearnly_cowork.ocr.table_inputs.model import TableDocument

    pages = tuple(Page(page_number=number, width=0, height=0, avg_confidence=1, blocks=[],
                       full_text=json.dumps(contracts.summary(day), ensure_ascii=False, sort_keys=True))
                  for number, day in enumerate(found, 1))
    issues = [{"code": "pos_voided_receipts", "count": voids}] if voids else []
    provenance = {"version": 1, "reference_kind": "logical_section", "unit_kind": "pos_day",
                  "coverage_complete": not issues, "coverage_issues": issues, "layout": fingerprint,
                  "units": [{"page_number": number, "rows": [list(pair) for pair in day.rows][:500]}
                            for number, day in enumerate(found, 1)]}
    return TableDocument(pages, len(found), ENGINE, 0, provenance, source.sha256)


def admit(source_file, request_key, *, workspace, connection, account_set_key, client=None, posting_kind=None):
    """一份 POS 导出建成逐日的票（按原件与请求号幂等：再调一次回同一批票）。

    回 ``{"status": "ok", "histories": [票名…], "days": [逐日汇总带 document]}``、``layout_needed``（同 `preview`）或
    ``failed``（``code`` 是 `pos.*` 词条键）。存货（``posting_kind="stock"``）且导出带商品明细时逐日带商品行，
    否则按金额记（服务类，不动库存）。"""
    scope.require("intake")
    scope.require_scope(workspace, client)
    source = files.get_source(source_file)
    try:
        _require_target(connection, account_set_key)
        headers, rows, first_row = _table(source)
        company = company_key(connection, account_set_key)
        layout, questions, fingerprint, revision = _layout(workspace, company, headers, rows)
        if layout is None:
            return _needed(headers, rows, questions, fingerprint, company, revision)
        found, voids = contracts.parse(headers, rows, layout, anchor_year=_today_year(),
                                       with_items=posting_kind == "stock", first_row=first_row)
    except contracts.PosError as error:
        return _failed(error)
    files.freeze_doc_manifest(source, _document(source, found, fingerprint, voids))
    groups = []
    for number, day in enumerate(found, 1):
        assessed = assess_confidence([contracts.day_page(day, number, layout_fingerprint=fingerprint)])
        groups.append({"workspace": workspace, "client": client, "source": "manual",
                       "source_page_indices": [number], "pages": assessed["pages"],
                       "ai_raw": json.loads(json.dumps(assessed["pages"])), "confidence": assessed["confidence"],
                       "posting_kind": "stock" if day.items else "service"})
    rows_saved = persist_batch(source.name, groups, request_key)
    names = [row["name"] for row in rows_saved]
    storage.set_push_targets(names, connection, account_set_key, TARGET)
    days = []
    for name, day in zip(names, found):
        days.append({"document": name, **contracts.summary(day)})
    return {"status": "ok", "histories": names, "days": days, "voids": voids, "fingerprint": fingerprint}


__all__ = ["ENGINE", "admit", "company_key", "layout_changed", "preview"]
