"""ERP 的 Claude 识别路线（ADR-022）：本站点选了 Claude 时，采购 / 销售识别都从这里读——整份文件交共用层
``pearnly_core.claude_ocr``（Haiku→Opus），把它的标准格式转成 ERP 原来的「模型原始输出」，再照旧走各自的
``structured`` / ``review`` / ``finish``。不调谷歌（不调 Vision 取原文核对）。"""
import re

from pearnly_core.claude_ocr import client
from pearnly_core.tax_documents import claude_kinds


# 不是采购 / 销售单据的种类（ERP 提示词口径「is_document：根本不是采购 / 销售单据时为 false」）：报价单、订单、送货单仍算单据。
NOT_DOCUMENTS = {"statement", "vat_report_or_list", "other_non_invoice", "transfer_slip", "withholding_certificate"}


def _num(value):
    return None if value in (None, "") else str(value)


def _branch(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if not digits:
        return "none", None
    return ("head_office", None) if int(digits) == 0 else ("branch", digits.zfill(5))


def _lines(doc, purchase):
    out = []
    for line in doc.get("lines") or []:
        row = {"description": line.get("description"), "qty": _num(line.get("qty")), "unit": line.get("unit"),
               "unit_price": _num(line.get("unit_price")), "amount": _num(line.get("amount")),
               "discount": _num(line.get("discount")), "discount_percent": _num(line.get("discount_percent"))}
        if purchase:
            row.update(item_type=line.get("item_type"), code=line.get("code"), barcode=line.get("barcode"),
                       vat_rate=_num(line.get("vat_rate")))
        out.append({k: v for k, v in row.items() if v not in (None, "")})
    return out


def to_raw(read, doc, label):
    """标准格式的一张单 → ERP 采购 / 销售提示词要的原始字段（字段名照各自 PROMPT）。"""
    purchase = label.startswith("purchase")
    printed = {"subtotal": _num(doc.get("subtotal")), "discount": _num(doc.get("discount")), "vat": _num(doc.get("vat")),
               "total": _num(doc.get("total"))}
    raw = {"doc_no": doc.get("invoice_number"), "doc_date": doc.get("date"), "doc_date_raw": doc.get("date_raw"),
           "buyer_tax_id": doc.get("buyer_tax"), "buyer_name": doc.get("buyer_name"), "discount_total": _num(doc.get("discount")),
           "printed": printed, "lines": _lines(doc, purchase), "is_document": True,
           # 票种换成全平台码（与 Cowork 的 Claude 路线同一张对照）：ERP 按它定抵不抵进项
           "document_type": claude_kinds.document_type(read.get("doc_kind"), doc)}
    vat = printed["vat"]
    has_vat = bool(vat and vat not in ("0", "0.00")) or doc.get("price_mode") == "inclusive"
    if purchase:
        kind, number = _branch(doc.get("seller_branch"))
        raw.update(due_date=doc.get("due_date"), supplier_name=doc.get("seller_name"), supplier_tax_id=doc.get("seller_tax"),
                   supplier_branch_type=kind, supplier_branch_no=number, supplier_address=doc.get("seller_address"),
                   currency=doc.get("currency"), has_vat=has_vat, price_mode=doc.get("price_mode"), notes=doc.get("notes"),
                   wht_rate=_num(doc.get("wht_rate")), wht_amount=_num(doc.get("wht_amount")),
                   doc_kind="purchase_order" if read.get("doc_kind") == "purchase_order" else "purchase_invoice")
    else:
        tax = re.sub(r"\D", "", str(doc.get("buyer_tax") or ""))
        buyer_type = "anonymous" if not doc.get("buyer_name") and not tax else (
            "company" if tax.startswith("0") else "person" if tax else "company")
        raw.update(seller_tax_id=doc.get("seller_tax"), buyer_type=buyer_type, buyer_address=doc.get("buyer_address"),
                   buyer_branch=doc.get("buyer_branch"),
                   has_vat=has_vat, vat_rate="7" if has_vat else None, price_includes_vat=doc.get("price_mode") == "inclusive")
    return {k: v for k, v in raw.items() if v is not None}


def read_all(images, *, structured, review, label, finish=None, progress=None, own_tax=None, originals=None):
    """与 ``policy.read_all`` 同样的返回：[(结果, 检查代码, 模型), …]，第一张在前；读不出返回 []。
    ``originals``：附件都在甲骨文（原件层，ADR-024）时只交引用，甲骨文按同样做法转正、渲染，原件不经过生产。"""
    if progress:
        progress(0, 1)
    outcome = (client.read_originals("voucher", originals, own_tax) if originals
               else client.read("voucher", images, own_tax))
    if progress:
        progress(1, 1)
    read = outcome.get("read")
    if not read:
        return []
    model = outcome["stages"][-1]["model"] if outcome.get("stages") else "claude"
    docs = read.get("documents") or []
    if not docs or read.get("doc_kind") in NOT_DOCUMENTS:
        return [({"not_document": True, "lines": []}, [], model)]
    found = []
    for doc in docs:
        result = structured(to_raw(read, doc, label))
        if result:
            result.pop("additional_documents", None)
            result["claude"] = {"escalated": outcome.get("escalated"), "usd": outcome.get("usd"), "where": outcome.get("where")}
            if finish:
                finish(result)
            found.append((result, review(result), model))
    return found
