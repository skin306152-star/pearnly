"""销售单据服务：取号、开出、作废、红冲补开，以及"开出即出库"。

三条口径：

1. **草稿不占号。** 号码在**开出**时于事务内取（行锁在计数器那一行上），作废不回收号 ——
   税票连号不跳，跳了要跟税局解释。
2. **只有已开出的销售凭证动货**（`contracts.should_post_stock`）：过账走出库流水，
   与采购入库同源；幂等锚钉在单据名上，重复开出不会扣两次。
3. **出库不需要库存岗位**：开票的人已经过了 `sales.write` 这道闸，库存那边走
   `internal=True` 通道 —— 一个用户动作只过一道闸（与收银卖货同一口径）。

本批已落：建单、改单、开出（含取号与出库）、作废（含回滚出库）、红冲/补开（引用原单）。
上传发票的识别入口与复核、手工录入的表单、PDF/导出在后续批次；它们都落在这同一张表上。
"""

from datetime import date

import frappe
from frappe import _

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.inventory import positions, stock, warehouses
from pearnly_erp_portal.masters import company
from pearnly_erp_portal.sales import contracts, ocr, render

DOCTYPE = "ERP Sales Document"
SEQUENCE = "ERP Doc Sequence"

NOT_FOUND = "Document not found"
NOT_EDITABLE = "A issued document cannot be edited"
ALREADY_ISSUED = "This document was already issued"
BLOCKED = "This document cannot be issued yet"
POINTLESS = "Only an issued document can be voided or noted"

# 取号策略：按年归 1（泰国多数按年；换月/永不只改这一处与桶键）。
RESET = contracts.RESET_YEARLY

LIST_FIELDS = ("name", "status", "doc_kind", "source", "doc_no", "doc_date", "customer",
               "buyer_name", "party_type", "tax_id", "branch_type", "branch_no", "address",
               "has_vat", "vat_rate", "wht_rate", "header_discount", "subtotal",
               "discount_total", "vat_amount", "wht_amount", "grand_total", "net_payable",
               "payment_status", "payment_method", "payment_date", "stock_posted_at",
               "source_file", "references_document", "note")


def _get(name):
    if not name or not frappe.db.exists(DOCTYPE, name):
        frappe.throw(_(NOT_FOUND), frappe.DoesNotExistError)
    return frappe.get_doc(DOCTYPE, name)


# ── 取号 ────────────────────────────────────────────────────────────────────


def allocate_number(doc_kind, on) -> str:
    """事务内取下一个连号。调用方负责提交；同一个桶上并发时靠行锁排队。"""
    prefix = contracts.resolve_prefix(
        None, company.get_profile().get("doc_prefix"),
        contracts.DEFAULT_PREFIXES.get(doc_kind, "SD"))
    bucket = contracts.period_key(RESET, on)
    key = f"{doc_kind}-{prefix}-{bucket}"
    if not frappe.db.exists(SEQUENCE, key):
        frappe.get_doc({"doctype": SEQUENCE, "doc_kind": doc_kind, "prefix": prefix,
                        "bucket": bucket, "next_number": 1}).insert(ignore_permissions=True)
    rows = frappe.db.sql(
        f"select next_number from `tab{SEQUENCE}` where name=%s for update",
        (key,), as_dict=True)
    number = int(rows[0].next_number)
    frappe.db.set_value(SEQUENCE, key, "next_number", number + 1, update_modified=False)
    return contracts.format_number(prefix, RESET, on, number)


# ── 建单 / 改单 ─────────────────────────────────────────────────────────────


def create_document(*, doc_kind, doc_date, buyer_name, lines, source=contracts.SOURCE_MANUAL,
                    customer=None, has_vat=True, vat_rate=7, wht_rate=0, header_discount=0,
                    tax_id=None, party_type=None, branch_type=None, branch_no=None,
                    address=None, note=None, source_file=None, image_sha256=None,
                    references_document=None, payment_status="unpaid", payment_method=None,
                    payment_date=None) -> dict:
    guard.require("sales.write")
    if doc_kind not in contracts.DOC_KINDS:
        frappe.throw(_("Unknown document kind"))
    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "status": contracts.DRAFT,
        "doc_kind": doc_kind,
        "source": source,
        "doc_date": doc_date,
        "customer": customer,
        "buyer_name": buyer_name,
        "party_type": party_type or "company",
        "tax_id": tax_id,
        "branch_type": branch_type,
        "branch_no": branch_no,
        "address": address,
        "has_vat": 1 if has_vat in (True, 1, "1", "true") else 0,
        "vat_rate": vat_rate,
        "wht_rate": wht_rate,
        "header_discount": header_discount,
        "note": note,
        "source_file": source_file,
        "image_sha256": image_sha256,
        "references_document": references_document,
        "payment_status": payment_status,
        "payment_method": payment_method,
        "payment_date": payment_date,
        "lines": [positions.prepare(line) for line in lines],
    })
    doc.insert()
    frappe.db.commit()
    return {"name": doc.name, "grand_total": str(doc.grand_total)}


def update_document(name, *, lines=None, **changes) -> dict:
    guard.require("sales.write")
    doc = _get(name)
    if doc.status not in contracts.EDITABLE_STATUSES:
        frappe.throw(_(NOT_EDITABLE))
    if lines is not None:
        doc.set("lines", [])
        for line in lines:
            doc.append("lines", positions.prepare(line))
    for field in ("doc_date", "buyer_name", "customer", "tax_id", "party_type",
                  "branch_type", "branch_no", "address", "has_vat", "vat_rate", "wht_rate",
                  "header_discount", "note", "source_file", "image_sha256",
                  "payment_status", "payment_method", "payment_date"):
        if field in changes:
            doc.set(field, changes[field])
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "grand_total": str(doc.grand_total)}


def list_documents(*, status=None, doc_kind=None, limit=50, start=0) -> list[dict]:
    guard.require("sales.read")
    filters = {}
    if status:
        filters["status"] = status
    if doc_kind:
        filters["doc_kind"] = doc_kind
    return frappe.get_list(DOCTYPE, filters=filters, fields=list(LIST_FIELDS),
                           order_by="doc_date desc, creation desc",
                           limit_start=int(start), limit_page_length=int(limit))


def get_document(name) -> dict:
    guard.require("sales.read")
    doc = _get(name)
    row = {field: doc.get(field) for field in LIST_FIELDS}
    row["lines"] = [{"line_no": line.line_no, "product": line.product,
                     "description": line.description, "qty": line.qty, "unit": line.unit,
                     "unit_price": line.unit_price, "discount": line.discount,
                     "line_total": line.line_total, **positions.public(line),
                     "vat_applicable": line.vat_applicable} for line in doc.lines]
    return row


# ── 开出前的闸 ──────────────────────────────────────────────────────────────


def _gate(doc) -> None:
    """开出前的全部拒因一起报，让人一次看清差什么（都在取号之前跑）。"""
    reasons = contracts.issue_gates({
        "doc_kind": doc.doc_kind,
        "grand_total": doc.grand_total,
        "payment_status": doc.payment_status,
        "payment_method": doc.payment_method,
        "payment_date": doc.payment_date,
        "buyer": {"party_type": doc.party_type, "customer_name": doc.buyer_name,
                  "address": doc.address, "tax_id": doc.tax_id,
                  "branch_type": doc.branch_type, "branch_no": doc.branch_no},
    })
    if reasons:
        frappe.throw(f"{_(BLOCKED)}: {', '.join(reasons)}")


def _post_stock(doc) -> dict:
    """开出即出库。幂等锚 = 单据名；已经有留痕就跳过。"""
    if doc.stock_posted_at or not contracts.should_post_stock(doc.doc_kind, doc.status):
        return {"posted": False}
    lines = [{"product": line.product, "qty": line.qty, "unit": line.unit,
              **positions.saved(line)}
             for line in doc.lines if line.product]
    if not lines:
        return {"posted": False}
    # 单据上没有仓库字段（多数租户只有一个仓库），落默认仓库。
    stock.apply_lines(warehouse=warehouses.ensure_default(), movement_type="sale_out",
                      lines=lines,
                      posting_date=doc.doc_date, ref_type="ERP Sales Document",
                      ref_id=doc.name, doc_no=doc.doc_no, internal=True,
                      client_uuid=f"sales-doc:{doc.name}")
    frappe.db.set_value(DOCTYPE, doc.name, "stock_posted_at", frappe.utils.now(),
                        update_modified=False)
    return {"posted": True, "lines": len(lines)}


# ── 流转 ────────────────────────────────────────────────────────────────────


def issue_document(name) -> dict:
    """开出：闸 → 取号 → 过账出库。闸不过就不占号。"""
    guard.require("sales.write")
    frappe.db.sql("select name from `tabERP Sales Document` where name=%s for update", (name,))
    doc = _get(name)
    if doc.status == contracts.ISSUED:
        frappe.throw(_(ALREADY_ISSUED))
    if not contracts.can(doc.status, contracts.ISSUE):
        frappe.throw(_(NOT_EDITABLE))
    _gate(doc)
    doc.doc_no = allocate_number(doc.doc_kind, date.fromisoformat(str(doc.doc_date)))
    doc.status = contracts.ISSUED
    doc.save()
    posted = _post_stock(doc)
    frappe.db.commit()
    return {"name": doc.name, "doc_no": doc.doc_no, "stock": posted}


def void_document(name, reason=None) -> dict:
    """作废：把出库退回来（反向流水），号码不回收。"""
    guard.require("sales.write")
    frappe.db.sql("select name from `tabERP Sales Document` where name=%s for update", (name,))
    doc = _get(name)
    if doc.status != contracts.ISSUED:
        frappe.throw(_(POINTLESS))
    _reverse_stock(doc, reason)
    doc.status = contracts.VOID
    doc.note = (f"{doc.note or ''}\nvoid: {reason or ''}").strip()
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "status": doc.status}


def _reverse_stock(doc, reason=None) -> None:
    if not doc.stock_posted_at:
        return
    stock.reverse_movements(ref_type="ERP Sales Document", ref_id=doc.name,
                            client_uuid=f"sales-doc-void:{doc.name}", reason=reason)
    frappe.db.set_value(DOCTYPE, doc.name, "stock_posted_at", None, update_modified=False)


def create_note(original, note_kind, *, lines=None, reason=None, doc_date=None) -> dict:
    """红冲（贷项）/ 补开（借项）：引用一张**已开出**的原单，自身直接开出。"""
    guard.require("sales.write")
    source = get_document(original) if frappe.db.exists(DOCTYPE, original) else None
    blocked = contracts.note_gate(source)
    if blocked:
        frappe.throw(f"{_(BLOCKED)}: {blocked}")
    if note_kind not in contracts.NOTE_TYPES:
        frappe.throw(_("Only a credit or debit note can be created"))
    created = create_document(
        doc_kind=note_kind, doc_date=doc_date or source["doc_date"],
        buyer_name=source["buyer_name"], customer=source["customer"],
        lines=lines or [{"product": line["product"], "description": line["description"],
                         "qty": line["qty"], "unit": line["unit"],
                         "unit_price": line["unit_price"], "warehouse": line["warehouse"],
                         "location": line["location"]} for line in source["lines"]],
        tax_id=source["tax_id"], party_type=source["party_type"],
        branch_type=source["branch_type"], branch_no=source["branch_no"],
        address=source["address"], references_document=source["name"], note=reason)
    return issue_document(created["name"])

# ── 上传发票：识别 → 填进草稿 ───────────────────────────────────────────────
#
# 三条来源都落在同一张表上：手工录入直接调 `create_document`；上传发票走这里；
# POS 收银走收银域（已交付）。下游只有商品/库存/收发存 —— 开出即出库。


def create_from_upload(*, content, content_type, filename, doc_date=None, manual=False) -> dict:
    """上传一张发票：先识别，再把归一后的字段填进一张 `source=ocr` 的草稿。

    `manual=True` 表示人不想（或不能）识别：那就只留底、开一张空草稿，
    字段自己填 —— 上传不等于必须花钱识别。
    """
    guard.require("sales.write")
    payload = {} if manual else ocr.recognise(content, content_type)
    mapped = contracts.map_recognised(payload)
    mapped["doc_date"] = mapped.get("doc_date") or doc_date or str(frappe.utils.nowdate())
    if not mapped["lines"]:
        frappe.throw(_("Nothing was recognised; enter the lines by hand"))
    mapped["source_file"] = _store_source(content, filename) or None
    mapped["image_sha256"] = _sha256(content)
    created = create_document(**mapped, source=contracts.SOURCE_OCR)
    return {**created, "recognised": not manual, "source_file": mapped["source_file"]}


def attach_recognition(name, *, payload) -> dict:
    """人复核识别结果：把（可能改过的）识别字段填回草稿。只有草稿能改。"""
    guard.require("sales.write")
    doc = _get(name)
    if doc.status not in contracts.EDITABLE_STATUSES:
        frappe.throw(_(NOT_EDITABLE))
    mapped = contracts.map_recognised(payload)
    for field in ("doc_date", "buyer_name", "party_type", "tax_id", "branch_type",
                  "branch_no", "address", "has_vat", "vat_rate", "wht_rate"):
        if mapped.get(field) is not None:
            doc.set(field, mapped[field])
    doc.set("lines", [])
    for line in mapped["lines"]:
        doc.append("lines", line)
    doc.save()
    frappe.db.commit()
    return {"name": doc.name, "grand_total": str(doc.grand_total)}


def _sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _store_source(content: bytes, filename: str) -> str:
    """给单据留底（私有 File）。留底失败不拦业务，但要留痕。"""
    if not content:
        return ""
    try:
        file_doc = frappe.get_doc({
            "doctype": "File", "file_name": filename or "sales-source.bin",
            "is_private": 1, "content": content,
        })
        file_doc.insert(ignore_permissions=True)
        return file_doc.file_url
    except Exception:
        frappe.log_error(title="Sales source file storage failed",
                         message=frappe.get_traceback()[-1200:])
        return ""

def convert_quotation(name, target_doc_kind) -> dict:
    """报价单转成目标类型的**草稿**。报价单本身不动，也不占发票号（开出时才取号）。"""
    guard.require("sales.write")
    source = get_document(name) if frappe.db.exists(DOCTYPE, name) else None
    blocked = contracts.convert_gate(source, target_doc_kind)
    if blocked:
        frappe.throw(f"{_(BLOCKED)}: {blocked}")
    return create_document(
        doc_kind=target_doc_kind, doc_date=source["doc_date"],
        buyer_name=source["buyer_name"], customer=source["customer"],
        lines=[{"product": line["product"], "description": line["description"],
                "qty": line["qty"], "unit": line["unit"], "unit_price": line["unit_price"],
                "discount": line["discount"], "warehouse": line["warehouse"],
                "location": line["location"]} for line in source["lines"]],
        tax_id=source["tax_id"], party_type=source["party_type"],
        branch_type=source["branch_type"], branch_no=source["branch_no"],
        address=source["address"], has_vat=source["has_vat"], vat_rate=source["vat_rate"],
        wht_rate=source["wht_rate"], header_discount=source["header_discount"],
        references_document=source["name"], note="converted_from_quotation")


def print_document(name, lang="th_en") -> dict:
    """票面模型：标签、明细、金额大写、付款码。不生成 PDF（排版与字体是另一件事）。"""
    guard.require("sales.read")
    doc = get_document(name)
    return render.print_model(doc, seller=company.get_profile(), lang=lang)
