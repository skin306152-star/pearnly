"""Internal immutable admission snapshots and monotonic result receipts.

Provider checkpoints commit independently. No open history transaction may
cross a provider call; final history+manifest+batch receipt commit together.
"""
from contextlib import contextmanager
import hashlib
import json

import frappe
from frappe import _
from pearnly_cowork.ocr.execution import access

WRITE = object()
IMMUTABLE = ("source_file", "workspace", "client", "actor", "request_key", "request_fingerprint",
             "source_sha256", "page_count", "billing_reference", "settings_json", "examples_json",
             "anchor_date", "direction", "posting_kind", "payment", "work_name",
             "reference_kind", "billing_kind", "billing_units", "intake_target")


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def validate(doc):
    if doc.flags.cowork_ocr_write is not WRITE:
        frappe.throw(_("Use the Cowork OCR service to change recognition tasks."), frappe.PermissionError)
    previous = doc.get_doc_before_save()
    if previous:
        if any(doc.get(k) != previous.get(k) for k in IMMUTABLE):
            frappe.throw(_("Recognition input and ownership cannot be changed."))
        if previous.result_json and previous.result_json != doc.result_json:
            frappe.throw(_("The completed recognition result cannot be replaced."))
        if previous.history_records and previous.history_records != doc.history_records:
            frappe.throw(_("The recognized invoice manifest cannot be replaced."))
        if previous.billing_state == "charged" and doc.billing_state != "charged":
            frappe.throw(_("The billing receipt cannot be reverted."))
    else:
        doc.name = digest(["cowork.invoice", doc.source_file])


def reject_delete(doc):
    frappe.throw(_("Recognition tasks cannot be deleted through the document API."), frappe.PermissionError)


def read(name, *, lock=False, authorize=True, write=False):
    doc = frappe.get_doc(access.DOCTYPE, name, for_update=lock)
    if authorize:
        access.require_batch(doc, write=write)
    return doc


def save(doc):
    doc.flags.cowork_ocr_write = WRITE
    doc.flags.notify_update = False
    # 更新时不再逐个回库核对关联字段（原件 / 归属 / 客户 / 提交人）：它们都在 IMMUTABLE 里，建单时已核对过，``validate``
    # 保证一字不改；被引用的记录删不掉（Frappe 删除时查引用），归属与客户是否停用由每次的授权复核判断。一次识别要存 4 次，
    # 这一步每次 4 条查询（2026-10-01 生产压测）。新建照旧核对。
    if not doc.is_new():
        doc.flags.ignore_links = True
    return doc.save(ignore_permissions=True)


def create(values):
    doc = frappe.get_doc({"doctype": access.DOCTYPE, **values})
    doc.flags.cowork_ocr_write = WRITE
    doc.flags.notify_update = False
    return doc.insert(ignore_permissions=True)


@contextmanager
def actor(user):
    previous = frappe.session.user
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous)


# 录入识别按页的进度（用户 2026-09-30：「录入工作台按页分进度，不要按份」）：引擎一页一页按顺序读，开始读第 N 页时
# 前 N−1 页已读完。只作展示，存在缓存里；读完（已存 / 不是发票）就是全部页数。
PAGES_KEY = "cowork:ocr-pages:"


def pages_reached(name, page, user=None):
    """``user``：推给谁（默认当前用户；任务派发的进度回调里当前用户是访客，由调用方给出提交人）。"""
    if not page:
        return
    try:
        key = PAGES_KEY + name
        if page - 1 > int(frappe.cache.get_value(key) or 0):
            frappe.cache.set_value(key, page - 1, expires_in_sec=6 * 3600)
            # 读到新的一页就推给提交人（原生实时推送）：等候页收到直接更新按页进度，轮询只作兜底、可以放慢
            # （2026-10-01 生产压测：每人同时等 6 份、每份每 5 秒读一次状态，上百人同时用时网页进程被这些读状态占满）。
            frappe.publish_realtime("pearnly_ocr_work", {"kind": "cowork.invoice", "reference": name,
                                                         "pages_done": page - 1},
                                    user=user or frappe.session.user, after_commit=False)
    except Exception:
        pass  # 进度只作展示：缓存一时写不了 / 推送不了不影响识别


def reading_started(name, user=None):
    """这份开始识别了（已从队列取出、标成识别中）：推给提交人，等候页由「排队中」改成「识别中」。只作展示，推送不了不影响识别。"""
    try:
        frappe.publish_realtime("pearnly_ocr_work", {"kind": "cowork.invoice", "reference": name, "reading": True},
                                user=user or frappe.session.user, after_commit=False)
    except Exception:
        pass


def pages_done(doc, state):
    if state in {"saved", "not_invoice"}:
        return int(doc.page_count or 0)
    try:
        return min(int(frappe.cache.get_value(PAGES_KEY + doc.name) or 0), int(doc.page_count or 0))
    except Exception:
        return 0


def project(doc):
    """Return safe lifecycle fields, never provider/raw prompt/credential data."""
    access.require_batch(doc)
    work = frappe.db.get_value(access.WORK, doc.work_name,
        ["status", "last_code", "attempts"], as_dict=True) if doc.work_name else None
    state = doc.phase
    if state not in {"saved", "not_invoice"} and work:
        state = work.status
    return {"name": doc.name, "source_file": doc.source_file, "page_count": doc.page_count,
            "reference_kind": doc.get("reference_kind") or "physical_page",
            "billing_kind": doc.get("billing_kind") or "pdf",
            "billing_units": int(doc.billing_units) if doc.get("billing_kind") else int(doc.page_count),
            "state": state, "pages_done": pages_done(doc, state),
            # 已开始识别（离开队列）：等候页「排队中 / 识别中」按这个分，推送丢了时轮询兜底
            "reading": state not in {"saved", "not_invoice"} and doc.phase in {"recognizing", "recognized"},
            "billing_state": doc.billing_state, "billing_code": doc.billing_code or "",
            "requires_review": bool(doc.requires_review),
            "history_records": json.loads(doc.history_records or "[]"),
            "attempts": work.attempts if work else 0,
            "last_code": (work.last_code or "") if work and state not in {"saved", "not_invoice"} else ""}
