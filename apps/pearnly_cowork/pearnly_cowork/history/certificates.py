"""50 ทวิ 参考记录（用户 2026-10-13）：租户把预扣税凭证设成「不录入」时，识别到的 50 ทวิ 仍存一条参考记录，
只给 ภ.ง.ด.3 / 53 草稿读：不成票、不进录入待办、不推送、不另外扣费（识别照常发生，不新增调用；整份都是凭证时
批次仍按非发票不计费）。

写：识别存档（`ocr.execution.workflow._persist`）在同一事务里调用 :func:`keep_skipped`，用保存点包住，存不了只记日志、
不影响票据入库；按「识别批次 + 读数序号」定名，重放、重试不重复写。哪页是凭证与存档、草稿同一条规则
（`contracts.document_policy.document_class`：原文标题打的类别，或读数本身就是凭证的样子）。
读：:func:`listing` 给报税草稿，先核读识别记录的权限；:func:`kept` 给 CC 说这份识别存了几张。
删：随原件一起删（`files.service.purge` 删原件与识别批次时按原件、批次删掉这些行；录入工作台删票即走这条）。
原生接口的读、建、改、删、分享一律拒绝（hooks）。
"""
import hashlib
import json
from datetime import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from pearnly_cowork.access import scope
from pearnly_cowork.contracts import document_policy as policy_rules

DOCTYPE = "Cowork WHT Certificate"
SAVEPOINT = "cowork_wht_certificate"
LIST_MAX = 5000
KEPT_FIELDS = ("document_type", "document_flag", "invoice_number", "date", "date_raw", "seller_name", "seller_tax",
               "seller_addr", "seller_branch", "buyer_name", "buyer_tax", "buyer_addr", "buyer_branch", "subtotal",
               "vat", "discount", "total_amount", "wht_rate", "wht_amount", "payment_method", "category", "notes")
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def guard(doc):
    if doc.flags.cowork_certificate_write is not _WRITE:
        deny()


def _key(batch, position) -> str:
    return hashlib.sha256(json.dumps([batch, position]).encode()).hexdigest()


def _date(value):
    try:
        return datetime.strptime(str(value or "").replace("/", "-")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _text(value, limit) -> str:
    return str(value or "").strip()[:limit]


def keep_skipped(batch, raw_pages, policy) -> int:
    """识别存档时调用（同一事务，未提交）：选了「不录入」的 50 ทวิ 页各存一条参考记录；回新写了几条。"""
    pages = policy_rules.skipped_certificates(raw_pages, policy or {})
    if not pages:
        return 0
    frappe.db.savepoint(SAVEPOINT)
    try:
        written = 0
        for position, page in pages:
            name = _key(batch.name, position)
            if frappe.db.exists(DOCTYPE, name):
                continue
            fields = {key: page["fields"][key] for key in KEPT_FIELDS if page["fields"].get(key) not in (None, "", [])}
            doc = frappe.get_doc({
                "doctype": DOCTYPE, "certificate_key": name, "workspace": batch.workspace,
                "client": batch.get("client") or None, "batch": _text(batch.name, 140),
                "source_file": _text(batch.get("source_file"), 140),
                "page_index": int(page.get("page_index") or 0), "invoice_date": _date(fields.get("date")),
                "seller_name": _text(fields.get("seller_name"), 200), "seller_tax": _text(fields.get("seller_tax"), 40),
                "buyer_name": _text(fields.get("buyer_name"), 200), "buyer_tax": _text(fields.get("buyer_tax"), 40),
                "wht_amount": _text(fields.get("wht_amount"), 40),
                "fields_json": json.dumps(fields, ensure_ascii=False, sort_keys=True), "created_at": now_datetime()})
            doc.flags.cowork_certificate_write = _WRITE
            doc.flags.ignore_permissions = True
            doc.insert(ignore_permissions=True)
            written += 1
        return written
    except frappe.QueryDeadlockError:
        raise
    except Exception:
        frappe.db.rollback(save_point=SAVEPOINT)
        frappe.log_error(title="Cowork WHT certificate reference not kept", message=frappe.get_traceback()[-1500:])
        return 0


def listing(workspace, start, end) -> list:
    """付款日在 [start, end] 的参考记录：[{ref, origin: "reference", fields}]，按日期先后。"""
    scope.require_read()
    scope.require_scope(workspace)
    rows = frappe.get_all(DOCTYPE, filters={"workspace": workspace, "invoice_date": ["between", [start, end]]},
                          fields=["name", "fields_json", "source_file", "page_index"],
                          order_by="invoice_date asc, name asc", limit_page_length=LIST_MAX)
    out = []
    for row in rows:
        try:
            fields = json.loads(row.fields_json or "{}")
        except ValueError:
            continue
        if isinstance(fields, dict):
            # page：在原件里的位置（原件, 页号），同一页也挂在识别记录里时草稿只算这一条（`tax.sources`）
            page = (row.source_file, int(row.page_index or 0)) if row.source_file else None
            out.append({"ref": row.name, "origin": "reference", "fields": fields, "page": page})
    return out


def kept(batch_name) -> int:
    """这份识别（识别批次）存下了几条参考记录。调用方已核过读这份识别的权限（`ocr.execution.service.certificates`）。"""
    return int(frappe.db.count(DOCTYPE, {"batch": batch_name})) if batch_name else 0


class CertificateDocument(Document):
    """只接受本模块的写入（对象标记伪造不了）；写定后不改。"""

    def check_permission(self, *args, **kwargs):
        guard(self)

    def autoname(self):
        guard(self)
        self.name = self.certificate_key

    def before_validate(self):
        guard(self)

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        deny()

    def before_rename(self, *args, **kwargs):
        deny()
