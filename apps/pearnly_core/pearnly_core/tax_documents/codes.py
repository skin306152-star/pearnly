"""泰国票据的票种码表（ADR-0012）：全平台唯一的一份，多产品共用。

票种按法律定义分，不按票据长相分：长相无穷，法定票种只有这几类。每个票种写明法律依据与
ETDA e-Tax 标准（ขมธอ. 3-2560）里对应的文件名，产品只引用这里的常量，不再各写一份枚举。

没列在这里的票（例如 ใบเพิ่มหนี้ 增项通知单、ใบรับรองการหักภาษี ณ ที่จ่าย 预扣税证明）一律归
`OTHER`，按决策表交人工，不猜。要支持新票种：在这里加一个码、在 `rules` 的决策表里加一行、
在回归样本里加真实原件的用例，三处一起改。
"""

TAX_INVOICE = "tax_invoice"
SIMPLIFIED_TAX_INVOICE = "simplified_tax_invoice"
RECEIPT = "receipt"
CREDIT_NOTE = "credit_note"
PAYMENT_EVIDENCE = "payment_evidence"
ORDER_EVIDENCE = "order_evidence"
OTHER = "other"

# 顺序即 Cowork 结构化输出的枚举顺序（`ocr/schema/invoice.py`），第一个是缺省值。
DOCUMENT_TYPES = (TAX_INVOICE, SIMPLIFIED_TAX_INVOICE, RECEIPT, CREDIT_NOTE,
                  PAYMENT_EVIDENCE, ORDER_EVIDENCE, OTHER)

# 法律依据（泰国《税法典》）与 ETDA ขมธอ. 3-2560 的文件名；只作说明与审查依据，不参与计算。
LEGAL_BASIS = {
    TAX_INVOICE: "ม.86/4 ใบกำกับภาษีเต็มรูป — ETDA: ใบกำกับภาษี / ใบแจ้งหนี้/ใบกำกับภาษี / "
                 "ใบเสร็จรับเงิน/ใบกำกับภาษี / ใบส่งของ/ใบกำกับภาษี",
    SIMPLIFIED_TAX_INVOICE: "ม.86/6 ใบกำกับภาษีอย่างย่อ; ภาษีซื้อต้องห้าม ม.82/5 — ETDA: "
                            "ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างย่อ",
    RECEIPT: "ม.105 ทวิ ใบรับ / ใบเสร็จรับเงิน (not a tax invoice) — ETDA: ใบเสร็จรับเงิน",
    CREDIT_NOTE: "ม.86/10 ใบลดหนี้ — ETDA: ใบลดหนี้",
    PAYMENT_EVIDENCE: "bank / PromptPay transfer slip: proves payment, not a tax document",
    ORDER_EVIDENCE: "e-commerce order screenshot: not a tax document",
    OTHER: "anything not listed above (incl. ใบเพิ่มหนี้, 50 ทวิ): human review",
}

# 给识别模型的票种说明（Cowork 结构化输出的字段说明、ERP 提示词共用这一段）。逐字等于旧线上
# `services/ocr/schemas_invoice.py` 的字段说明（Cowork 的源码对照测试逐字比较），改它要同时登记。
PROMPT_DESCRIPTION = (
    "document type. tax_invoice = full Thai tax invoice "
    "(ใบกำกับภาษีเต็มรูป, can claim input VAT, legal invoice no required); "
    "simplified_tax_invoice = ใบกำกับภาษีอย่างย่อ/ABB (POS slip, no legal "
    "invoice no, cannot claim VAT); payment_evidence = bank/PromptPay transfer "
    "slip (proves payment, not a tax doc); order_evidence = e-commerce order "
    "screenshot; receipt/credit_note/other otherwise"
)
TITLE_PROMPT = ("document_title = the document's printed title exactly as printed (e.g. "
                "ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างย่อ, TAX INVOICE), null when none is printed")

# 界面词条（msgid 在各产品术语表里翻译）。
LABELS = {
    TAX_INVOICE: "Tax Invoice",
    SIMPLIFIED_TAX_INVOICE: "Abbreviated Tax Invoice",
    RECEIPT: "Receipt",
    CREDIT_NOTE: "Credit Note",
    PAYMENT_EVIDENCE: "Payment Evidence",
    ORDER_EVIDENCE: "Order Evidence",
    OTHER: "Other",
}
