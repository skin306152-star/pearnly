"""Claude 识别（ADR-022）给的单据种类（``doc_kind``）→ 全平台票种码（`codes`）。Cowork 与 ERP 的 Claude 路线共用。

由 Cowork 的 Claude 路线（`pearnly_cowork.ocr.execution.claude`）原样搬来（2026-10-23）：ERP 原先把 ``doc_kind`` 直接当票种，
收据兼税票、水电费、账单等认不出成了「其他」，于是不抵进项（10-23 验收：泰国税票识别后增值税为 0）。纯函数，不依赖 Frappe。
"""
from pearnly_core.tax_documents import codes as tax_codes

# 不在表里的按「其他」。
TYPES = {"tax_invoice": tax_codes.TAX_INVOICE, "receipt_tax_invoice": tax_codes.TAX_INVOICE, "debit_note": tax_codes.TAX_INVOICE,
         "customs_import": tax_codes.TAX_INVOICE, "utility": tax_codes.TAX_INVOICE, "bill_invoice": tax_codes.TAX_INVOICE,
         "receipt": tax_codes.RECEIPT, "credit_note": tax_codes.CREDIT_NOTE, "transfer_slip": tax_codes.PAYMENT_EVIDENCE,
         "ecommerce_order": tax_codes.ORDER_EVIDENCE}


def document_type(kind, doc):
    """一张单的票种：``kind`` 是整份的 ``doc_kind``，``doc`` 是 ``documents`` 里的这一张。"""
    if kind == "fuel_receipt":
        return tax_codes.TAX_INVOICE if doc.get("seller_tax") and doc.get("buyer_tax") else tax_codes.SIMPLIFIED_TAX_INVOICE
    if kind in ("tax_invoice", "receipt_tax_invoice", "bill_invoice", "utility") and not doc.get("seller_tax"):
        return tax_codes.RECEIPT
    if kind == "receipt_tax_invoice" and doc.get("price_mode") == "inclusive" and not doc.get("buyer_tax"):
        return tax_codes.SIMPLIFIED_TAX_INVOICE
    return TYPES.get(kind, tax_codes.OTHER)
