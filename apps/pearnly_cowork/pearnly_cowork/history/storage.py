"""Persist full invoice evidence in native DocTypes, not the early amount-only demo."""
from datetime import datetime, timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from pearnly_core.execution.work import step
from pearnly_cowork.access import scope
from .push_state import FIELDS as PUSH_FIELDS
from . import categories, duplicates, names, review
from pearnly_core.express_rules.contracts.history import (GATEWAY_SOURCE, HistoryContractError, PARTY_FIELDS, initial_history, prepare_pages,
    extract_party_fields, extract_summary_fields, list_status, normalize_posting_kind, primary_page, primary_page_fields)
from pearnly_core.express_rules.contracts.push import declared_direction_pages

DOCTYPE = "Cowork OCR History"
_CREATE_TOKEN = object()
_UPDATE_TOKEN = object()
IMMUTABLE = ("workspace", "client", "source", "request_key", "request_fingerprint",
             "ai_raw", "confidence", "posting_kind", "source_pdf_id", "source_page_indices",
             "source_index", "source_total", "filename", "file_hash", "page_count", "source_file",
             # 新建录入时选的推送目标（连接 / 账套），识别落库时写入，之后按它推（用户 2026-09-26「按第一次选择」）。
             "push_connection", "push_account_set", "intake_target")
SUMMARY_FIELDS = ("invoice_no", "invoice_date", "seller_name", "total_amount")


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def parse(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        frappe.throw(_("history.json_invalid"))


def contract(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except HistoryContractError as error:
        frappe.throw(_(error.code))


def revision(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        frappe.throw(_("A positive record revision is required."))
    return int(value)


def anchor_year():
    return datetime.now(ZoneInfo("Asia/Bangkok")).year


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def conflict():
    frappe.throw(_("The record changed. Reload it before saving."), frappe.TimestampMismatchError)


def validate_document(doc):
    scope.require("intake")
    scope.require_scope(doc.workspace, doc.client)
    previous = doc.get_doc_before_save()
    if previous:
        if doc.flags.cowork_history_update is not _UPDATE_TOKEN:
            frappe.throw(_("Update invoice records through the Cowork review service."))
        if any(doc.get(key) != previous.get(key) for key in (*IMMUTABLE, *PUSH_FIELDS, *duplicates.FIELDS)):
            frappe.throw(_("Original evidence and ownership cannot be changed."))
        # Only dedicated APIs may mutate; native PUT omitting a client revision is rejected.
        if int(doc.record_version or 0) != int(previous.record_version or 0):
            conflict()
        if not previous.staged and doc.staged:
            frappe.throw(_("A completed record cannot become a draft."))
        doc.record_version = int(previous.record_version) + 1
        # 同一原件里人刚改了名称、跟着改的草稿不算人改过（edit_count 是"人核过"的依据）；补上会计员说明的方向也不算。
        changed = (doc.pages != previous.pages and not doc.flags.cowork_name_follow
                   and not doc.flags.cowork_declared_direction)
        doc.edit_count = int(previous.edit_count or 0) + int(changed)
        doc.fields_edited_at = now_utc() if changed else previous.fields_edited_at
        if previous.staged and not doc.staged:
            doc.confirmed_by, doc.confirmed_at = frappe.session.user, now_utc()
        else:
            doc.confirmed_by, doc.confirmed_at = previous.confirmed_by, previous.confirmed_at
    else:
        # Native REST must not bypass the creation service to forge OCR/identity evidence.
        if doc.flags.cowork_history_create is not _CREATE_TOKEN:
            frappe.throw(_("Create invoice records through the Cowork intake service."))
        # Frappe clears fields above the actor's permlevel before validation.
        # Restore only this service-computed private reference after checking
        # the creation capability; keep every other native permission intact.
        doc.duplicate_match = doc.flags.cowork_duplicate_match
        if any(doc.get(key) for key in PUSH_FIELDS):
            frappe.throw(_("Original evidence and ownership cannot be changed."))
        doc.record_version, doc.edit_count = 1, 0
        doc.fields_edited_at, doc.confirmed_by, doc.confirmed_at = None, None, None
        if not doc.staged:
            frappe.throw(_("New records must start as drafts."))
    # 新建前各入口已按自己的口径（手工严格、识别宽松）过了合约；页面没变的保存（确认、推送状态）
    # 只复算，不因识别时留下的认不出的票面日期拦住。改页面的只有复核编辑，仍严格。
    # 改了页面时，日期写法没动的页仍按入库口径（复核页只改一页；别的页有认不出的日期不能拦住整条保存，2026-09-29）。
    edited = bool(previous) and doc.pages != previous.pages
    pages = contract(prepare_pages, parse(doc.pages), anchor_year=anchor_year(), recognized=not edited,
                     previous=parse(previous.pages or "[]") if edited else None)
    doc.pages = encode(pages)
    summary = extract_summary_fields(pages)
    for field in SUMMARY_FIELDS:
        doc.set(field, summary[field])
    # 列表的买卖方与税号列：与摘要同一主页面，随每次写入重算，才能在服务端排序。
    for field, value in extract_party_fields(pages).items():
        doc.set(field, value)
    doc.quality_status = list_status(doc.confidence, summary)
    doc.review_reasons = encode(review_reasons(doc, pages))


def party_numbers(workspace, name, pages):
    """同一开票方以前人核过（改过或推送成功）的票号，只取写法用，不回显给别人看。"""
    # 票号格式由开票方定：进项看供应商、销项看本公司自己开过的票，所以一律按卖方税号取历史。
    raw_tax = str(primary_page_fields(pages).get("seller_tax") or "").strip()
    digits = review.history_tax(raw_tax)
    if not digits or not workspace:
        return []
    # 一家可能同时用几套票号（压测：本公司给 Big C 与给别家的是两套），取近 500 张统计各种写法，只取最近几十张会漏掉另一套。
    return frappe.db.sql_list(f"""SELECT invoice_no FROM `tab{DOCTYPE}`
        WHERE workspace=%s AND name!=%s AND seller_tax IN (%s, %s) AND COALESCE(invoice_no, '')!=''
          AND (last_push_status='success' OR edit_count>0)
        ORDER BY creation DESC LIMIT 500""", (workspace, name or "", raw_tax, digits))


def settle(workspace, name, pages):
    """入库时有确定答案的直接改、不问人（用户 2026-10-02，规则见 `history/review.py`）：12 位税号补 0 校验位就对的补上；
    单号两种读法按这家以前的写法定；进项单号像凭证号、另有候选号的换成候选号。不确定的不改。返回改好的副本。"""
    pages = json.loads(encode(pages))
    review.repair_tax_ids(pages)
    review.settle_number(pages, party_numbers=party_numbers(workspace, name, pages))
    return pages


def review_reasons(doc, pages):
    """这张票需要人看一眼的原因（规则见 `history/review.py`）；推送成功过的不再提。

    2026-10-02 起不显示给用户，只作内部质量信号（评测统计、定向重读）。"""
    if doc.last_push_status == "success":
        return []
    numbers = party_numbers(doc.workspace, doc.name, pages)
    own_tax = frappe.db.get_value("Cowork Workspace", doc.workspace, "tax_id") if doc.workspace else None
    return review.reasons(pages, own_tax=own_tax, party_numbers=numbers,
                          duplicate=doc.duplicate_check_status == "matched", edited=int(doc.edit_count or 0) > 0)


def guard_write(doc):
    token = _CREATE_TOKEN if doc.is_new() else _UPDATE_TOKEN
    actual = doc.flags.cowork_history_create if doc.is_new() else doc.flags.cowork_history_update
    if actual is not token:
        frappe.throw(_("Update invoice records through the Cowork review service."))


def create_manual(workspace, pages, request_key, client=None):
    # A caller-entered page is not an OCR result. Only a trusted recognition
    # workflow may set source/confidence/first-model evidence.
    scope.require("intake")
    scope.require_scope(workspace, client)
    body = contract(initial_history, parse(pages), public_create=True, anchor_year=anchor_year())
    body["ai_raw"] = None
    return _create(workspace, body, request_key, client)


def create_gateway(workspace, pages, request_key):
    """Pearnly ERP 前台交来的单（`erp_gateway`）：页面由网关从 ERP 单据转出，不是识别结果、不扣费、不查重、不换名称。
    调用方已切成网关服务账号（录入权限同网页）。同一请求号同样内容回原记录，不同内容拒绝（同 :func:`create_manual`）。"""
    body = contract(initial_history, pages, source=GATEWAY_SOURCE, anchor_year=anchor_year())
    body["ai_raw"] = None
    return _create(workspace, body, request_key)


def gateway_name(workspace, request_key):
    """网关建的票叫什么（与 :func:`_create` 同一算法，当前用户是网关服务账号）：按 ERP 单据查结果时不必另存对照表。"""
    key = hashlib.sha256(encode([workspace, frappe.session.user, request_key]).encode()).hexdigest()
    return "CWH-" + key[:48]


def create_recognized(workspace, pages, request_key, *, source, confidence, ai_raw,
                      client=None, posting_kind=None):
    """Internal persistence boundary for a future OCR handler; not a whitelisted RPC.

    This does not invoke a model or charge usage. The handler must restore the
    verified actor before calling, as ordinary workspace permission still applies.
    """
    body = contract(initial_history, pages, source=source, confidence=confidence, ai_raw=ai_raw,
                           posting_kind=posting_kind, anchor_year=anchor_year(), recognized=True)
    return _create(workspace, body, request_key, client, check_duplicates=True)


def _create(workspace, body, request_key, client=None, *, attachment=None, check_duplicates=False):
    scope.require("intake")
    scope.require_scope(workspace, client)
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128:
        frappe.throw(_("A request key of 1 to 128 characters is required."))
    identity = {"workspace": workspace, "client": client or None, "body": body}
    if attachment:
        identity["attachment"] = attachment
    fingerprint = hashlib.sha256(encode(identity).encode()).hexdigest()
    key = hashlib.sha256(encode([workspace, frappe.session.user, request_key]).encode()).hexdigest()
    name = "CWH-" + key[:48]
    if frappe.db.exists(DOCTYPE, name):
        return _existing(name, fingerprint)
    # 识别入库：先按税号换成人定的名称、清掉不是品名的品名（分类记忆按卖方名称取，要用换过的名称）。
    with step("persist_names"):
        pages = (categories.prepare(workspace, client, names.prepare(workspace, body["pages"]))
                 if check_duplicates else body["pages"])
        # 放在请求指纹之后：重试时这家的票号历史可能已变，指纹只认调用方交来的内容。
        pages = settle(workspace, name, pages)
    body = {**body, "pages": pages, **extract_summary_fields(pages)}
    doc = frappe.get_doc({"doctype": DOCTYPE, "name": name, "workspace": workspace, "client": client or "",
                          "source": body["source"], "pages": encode(pages),
                          "ai_raw": encode(body["ai_raw"]), "confidence": body["confidence"], "staged": 1,
                          "posting_kind": body["posting_kind"],
                          "request_key": key, "request_fingerprint": fingerprint,
                          "page_count": len(body["pages"]),
                          "source_page_indices": encode(body["source_page_indices"])})
    if attachment:
        doc.update(attachment)
    if check_duplicates:
        with step("persist_duplicates"):
            doc.update(duplicates.check(workspace, client, body))
        doc.flags.cowork_duplicate_match = doc.duplicate_match
    else:
        doc.duplicate_check_status = "not_checked"
    doc.flags.cowork_history_create = _CREATE_TOKEN
    frappe.db.savepoint("cowork_create")
    try:
        with step("persist_insert"):
            doc.insert()
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point="cowork_create")
        return _existing(name, fingerprint)
    return project(doc)


def _existing(name, fingerprint):
    # Current read also sees a competing transaction's just-committed insert
    # under MariaDB REPEATABLE READ after rolling back our savepoint.
    doc = read(name, for_update=True)
    if doc.request_fingerprint != fingerprint:
        frappe.throw(_("This request key has already been used for different content."))
    return project(doc)


def read(name, *, for_update=False):
    scope.require_read()
    # Frappe native resource APIs also use the registered permission/query hooks.
    doc = frappe.get_doc(DOCTYPE, name, for_update=for_update)
    scope.require_scope(doc.workspace, doc.client)
    doc.check_permission("read")
    return doc


PARTY_SCAN = 50  # 按票面单号找、再按本方税号筛时最多看几张


def by_invoice_no(invoice_no, limit=6, party_tax="") -> list:
    """票面单号是这个的单据（本人看得到的，权限同列表；新的在前，最多 ``limit`` 张）：``[{name, invoice_date, seller_name,
    buyer_name}]``。CC 按单据存公司规则时把票面单号当成了单据编号，用它认出是哪一张（`assistant.company_tools._document`）。
    ``party_tax``（规则所属公司的 13 位税号）：只要这家是买方或卖方的单（第 8 轮审查：别家客户常有同号单，不能拿 B 公司的单给
    A 公司存规则）；不是 13 位不筛。"""
    scope.require_read()
    value = str(invoice_no or "").strip()
    if not value:
        return []
    mine = re.sub(r"\D", "", str(party_tax or ""))
    if len(mine) != 13:
        return frappe.get_list(DOCTYPE, filters={"invoice_no": value},
                               fields=["name", "invoice_date", "seller_name", "buyer_name"],
                               order_by="creation desc", limit_page_length=int(limit))
    rows = frappe.get_list(DOCTYPE, filters={"invoice_no": value},
                           fields=["name", "invoice_date", "seller_name", "buyer_name", "seller_tax", "buyer_tax"],
                           order_by="creation desc", limit_page_length=PARTY_SCAN)
    own = [row for row in rows if mine in (re.sub(r"\D", "", str(row.get("seller_tax") or "")),
                                           re.sub(r"\D", "", str(row.get("buyer_tax") or "")))]
    return [{key: row.get(key) for key in ("name", "invoice_date", "seller_name", "buyer_name")} for row in own[:int(limit)]]


def project(doc):
    # auto_class / auto_reasons：自动推前判的类别与原因码，复核页对「选出多份同期账套」（``book_many``）列候选账套
    result = {key: doc.get(key) for key in (
        "name", "workspace", "client", "source", "staged", "record_version", *PUSH_FIELDS,
        "confidence", "quality_status", "posting_kind", *SUMMARY_FIELDS,
        "edit_count", "fields_edited_at", "confirmed_by", "confirmed_at",
        "source_file", "source_pdf_id", "source_index", "source_total", "filename", "file_hash", "page_count",
        "push_connection", "push_account_set", "intake_target", "auto_class", "auto_reasons")}
    for key in ("pages", "ai_raw", "source_page_indices"):
        result[key] = parse(doc.get(key) or "null")
    result["review_reasons"] = parse(doc.get("review_reasons") or "[]")
    result["posting_kind"] = normalize_posting_kind(doc.posting_kind)
    result["duplicate_check_status"] = doc.duplicate_check_status or "not_checked"
    result["duplicate_warning"] = duplicates.warning(doc)
    if doc.source_file:
        from pearnly_cowork.files.access import reference_kind
        original = frappe.get_doc("Cowork Source File", doc.source_file)
        result["source_reference_kind"] = reference_kind(original)
    else:
        result["source_reference_kind"] = None
    return result


def set_push_targets(names, connection, account_set, intake_target):
    """新建录入时选的推送目标抄到刚落库的票上（与识别落库 `ocr.execution.workflow._copy_targets` 同一做法，之后按它推，
    用户 2026-09-26「按第一次选择」）。已经有目标的票不改；调用方刚以当前用户身份建好这些票。"""
    scope.require("intake")
    for name in names or []:
        doc = read(name, for_update=True)
        if doc.push_connection or doc.intake_target:
            continue
        frappe.db.set_value(DOCTYPE, name, {"push_connection": connection or "", "push_account_set": account_set or "",
                                            "intake_target": intake_target or ""}, update_modified=False)


def declare_direction(names, direction):
    """会计员事后说明的进 / 销项写到这份原件识别出的票上（2026-10-15：CC 先「仅识别」、会计员说是费用后再交同一附件，
    识别服务按第一次的口径拿回原来那份，说明的方向丢了，票上方向空着）。

    只写主页面还没有方向的、还是草稿且没推过的票（不覆盖已有的明确方向，不改推过 / 正在推的）；不算人改过票面。
    回写了的票号。"""
    scope.require("intake")
    written = []
    for name in names or []:
        doc = read(name, for_update=True)
        if not doc.staged or doc.last_push_status:
            continue
        pages = parse(doc.pages or "[]")
        changed = declared_direction_pages(pages, primary_page(pages) if isinstance(pages, list) else None, direction)
        if changed is None:
            continue
        doc.pages = encode(changed)
        doc.flags.cowork_history_update = _UPDATE_TOKEN
        doc.flags.cowork_declared_direction = True
        doc.save()
        written.append(name)
    return written


def update_pages(name, pages, expected_version):
    scope.require("intake")
    doc = read(name, for_update=True)
    if revision(expected_version) != int(doc.record_version):
        conflict()
    pages = contract(prepare_pages, parse(pages), anchor_year=anchor_year(), previous=parse(doc.pages or "[]"))
    if not pages:
        frappe.throw(_("At least one invoice page is required when editing."))
    changed = doc.pages != encode(pages)
    previous = parse(doc.pages or "[]")
    doc.pages = encode(pages)
    doc.flags.cowork_history_update = _UPDATE_TOKEN
    doc.save()
    from pearnly_cowork.ocr.feedback.service import capture_saved_edit
    capture_saved_edit(doc)
    followed = []
    if changed:
        decided = names.learn(doc.workspace, previous, pages)
        followed = _follow_names(doc, decided)
        categories.learn(doc.workspace, doc.client, pages)
    result = project(doc)
    # 跟着改了名称的草稿版本号变了：复核页按这份清单重读它们，免得拿旧版本保存或推送。
    result["followed"] = followed
    return result


def _follow_names(doc, decided):
    """同一原件识别出来、还没人动过也没推过的草稿：同一税号的名称换成人刚改定的（改一张，整批都对）。"""
    if not decided or not doc.source_file:
        return []
    followed = []
    for name in frappe.get_all(DOCTYPE, filters={"source_file": doc.source_file, "name": ["!=", doc.name],
                                                 "staged": 1, "edit_count": 0}, pluck="name", order_by="name asc"):
        row = read(name, for_update=True)
        if row.last_push_status or not row.staged or int(row.edit_count or 0):
            continue
        pages = names.renamed(parse(row.pages or "[]"), decided)
        if pages is None:
            continue
        row.pages = encode(pages)
        row.flags.cowork_history_update = _UPDATE_TOKEN
        row.flags.cowork_name_follow = True
        row.save()
        followed.append(row.name)
    return followed


def complete(records):
    """One transaction, all-or-nothing; repeated completion does not write again."""
    scope.require("intake")
    records = parse(records)
    if not isinstance(records, list) or not 1 <= len(records) <= 500:
        frappe.throw(_("Select between 1 and 500 records."))
    if any(not isinstance(r, dict) or set(r) != {"name", "record_version"} for r in records):
        frappe.throw(_("Each record must have a name and revision."))
    if any(not isinstance(r["name"], str) or not r["name"] or len(r["name"]) > 140 for r in records):
        frappe.throw(_("Each record must have a valid name."))
    for item in records:
        item["record_version"] = revision(item["record_version"])
    if len({r["name"] for r in records}) != len(records):
        frappe.throw(_("Duplicate records are not allowed."))
    locked = []
    for item in sorted(records, key=lambda r: r["name"]):
        doc = read(item["name"], for_update=True)
        if doc.staged and int(item["record_version"]) != int(doc.record_version):
            conflict()
        locked.append(doc)
    # Validate the entire scope/version set before any writes.
    by_name = {}
    frappe.db.savepoint("cowork_complete")
    try:
        for doc in locked:
            was_draft = bool(doc.staged)
            if was_draft:
                doc.staged = 0
                doc.flags.cowork_history_update = _UPDATE_TOKEN
                doc.save()
            by_name[doc.name] = {"name": doc.name, "staged": False, "already_completed": not was_draft,
                                 "record_version": doc.record_version}
    except frappe.QueryDeadlockError:
        # InnoDB can remove every savepoint when rolling back a deadlock.
        # A database conflict requires a fresh transaction, not a savepoint retry.
        frappe.db.rollback()
        raise
    except Exception:
        frappe.db.rollback(save_point="cowork_complete")
        raise
    return [by_name[item["name"]] for item in records]
