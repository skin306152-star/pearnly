"""票面：标签、金额大写、PromptPay 付款载荷、打印模型（纯规则）。

照旧线上 `services/sales/pdf_labels.py` 与 `promptpay.py` 的口径。**不连库、不渲染图** ——
票面文字与付款载荷拼错了，顾客扫不出码、税局看不对票，而这两件事都不该靠打开一张 PDF
才发现，所以它们在这一层被离线断言。

语言只做两档：`th`（仅泰文）与 `th_en`（泰 + 英，默认）。旧线上还有一档"泰 + 中"，
中文那一档等语言包批一起做 —— 这里不写死中文（`check-i18n` 的存量清单不再增长）。
"""

from __future__ import annotations

from decimal import Decimal

from pearnly_erp_portal.sales import contracts

# 泰文恒在（泰国票面硬要求），次语只是补一行。
LABEL_LANGS = ("th", "th_en")

DOC_LABELS = {
    contracts.QUOTATION: ("ใบเสนอราคา", "Quotation"),
    contracts.TAX_INVOICE: ("ใบกำกับภาษี", "Tax Invoice"),
    contracts.TAX_INVOICE_SIMPLE: ("ใบกำกับภาษีอย่างย่อ", "Simplified Tax Invoice"),
    contracts.TAX_INVOICE_RECEIPT: ("ใบกำกับภาษี/ใบเสร็จรับเงิน", "Tax Invoice / Receipt"),
    contracts.RECEIPT: ("ใบเสร็จรับเงิน", "Receipt"),
    contracts.CREDIT_NOTE: ("ใบลดหนี้", "Credit Note"),
    contracts.DEBIT_NOTE: ("ใบเพิ่มหนี้", "Debit Note"),
}

# 买方证件标签随买方类型变：公司 = 税号，个人 = 身份证，外国 = 护照。
BUYER_TIN_LABELS = {
    "company": ("เลขประจำตัวผู้เสียภาษี", "Tax ID"),
    "person": ("เลขบัตรประชาชน", "National ID"),
    "foreign": ("เลขหนังสือเดินทาง", "Passport No."),
    "anonymous": ("", ""),
}

FIELD_LABELS = {
    "doc_no": ("เลขที่", "No."),
    "doc_date": ("วันที่", "Date"),
    "buyer": ("ลูกค้า", "Customer"),
    "address": ("ที่อยู่", "Address"),
    "branch": ("สาขา", "Branch"),
    "subtotal": ("มูลค่าสินค้า", "Subtotal"),
    "discount": ("ส่วนลด", "Discount"),
    "vat": ("ภาษีมูลค่าเพิ่ม", "VAT"),
    "grand_total": ("ยอดรวม", "Grand Total"),
    "net_payable": ("ยอดชำระสุทธิ", "Net Payable"),
    "amount_in_words": ("จำนวนเงินตัวอักษร", "Amount in words"),
    "item": ("รายการ", "Description"),
    "qty": ("จำนวน", "Qty"),
    "unit_price": ("ราคาต่อหน่วย", "Unit Price"),
    "line_total": ("รวม", "Amount"),
}


def label(key: str, lang: str = "th_en") -> str:
    """取一条票面标签。未知键原样返回（便于在断言里看出漏了哪条）。"""
    th, en = FIELD_LABELS.get(key, (key, key))
    return f"{th} / {en}" if lang != "th" and en else th


def doc_label(doc_kind: str, lang: str = "th_en") -> str:
    th, en = DOC_LABELS.get(doc_kind, (doc_kind or "", ""))
    return f"{th} / {en}" if lang != "th" and en else th


def buyer_tin_label(party_type: str, lang: str = "th_en") -> str:
    th, en = BUYER_TIN_LABELS.get(party_type or "company", ("", ""))
    return f"{th} / {en}" if lang != "th" and en else th


# ── PromptPay 付款载荷（EMVCo / BOT anyID）───────────────────────────────────

_AID = "A000000677010111"
_CURRENCY_THB = "764"
_COUNTRY_TH = "TH"


def _emv(tag: str, value: str) -> str:
    """EMVCo TLV：标签(2) + 长度(2 位十进制) + 值。"""
    return f"{tag}{len(value):02d}{value}"


def _crc16(data: str) -> str:
    """CRC-16/CCITT-FALSE（poly 0x1021、init 0xFFFF），大写四位十六进制。"""
    crc = 0xFFFF
    for byte in data.encode("ascii"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _proxy(promptpay_id: str) -> tuple[str, str]:
    """代理归一化 → (子标签, 值)。手机号 01（0066 + 9 位，补零到 13 位）/ 身份证或税号 02
    （13 位）/ e-Wallet 03（15 位）。"""
    digits = "".join(char for char in str(promptpay_id or "") if char.isdigit())
    if len(digits) >= 15:
        return "03", digits[:15]
    if len(digits) == 13:
        return "02", digits
    mobile = "66" + digits[1:] if digits.startswith("0") else digits
    return "01", ("0000000000000" + mobile)[-13:]


def promptpay_payload(promptpay_id: str, amount=None) -> str:
    """拼 PromptPay 载荷字符串（可直接编码成 QR）。`amount` 为空 = 静态码（扫后自填）。"""
    if not str(promptpay_id or "").strip():
        return ""
    sub_tag, target = _proxy(promptpay_id)
    merchant = _emv("00", _AID) + _emv(sub_tag, target)
    parts = [
        _emv("00", "01"),
        _emv("01", "12" if amount is not None else "11"),
        _emv("29", merchant),
        _emv("58", _COUNTRY_TH),
        _emv("53", _CURRENCY_THB),
    ]
    if amount is not None:
        parts.append(_emv("54", f"{Decimal(str(amount)):.2f}"))
    body = "".join(parts) + "6304"
    return body + _crc16(body)


# ── 打印模型 ────────────────────────────────────────────────────────────────


def print_model(document: dict, *, seller: dict | None = None, lang: str = "th_en") -> dict:
    """把一张单据折成"票面要印什么"。不生成 PDF，也不做排版 —— 只给出确定的内容。"""
    if lang not in LABEL_LANGS:
        lang = "th_en"
    seller = seller or {}
    doc_kind = document.get("doc_kind")
    lines = []
    for index, line in enumerate(document.get("lines") or [], start=1):
        lines.append({
            "line_no": line.get("line_no") or index,
            "description": line.get("description") or "",
            "qty": line.get("qty"),
            "unit": line.get("unit") or "",
            "unit_price": line.get("unit_price"),
            "line_total": line.get("line_total"),
            "vat_applicable": line.get("vat_applicable", True),
        })
    unpaid = document.get("payment_status") in (None, "unpaid", "partial")
    return {
        "doc_kind": doc_kind,
        "doc_label": doc_label(doc_kind, lang),
        "doc_no": document.get("doc_no") or "",
        "doc_date": str(document.get("doc_date") or ""),
        "seller": {
            "name": seller.get("company_name") or "",
            "tax_id": seller.get("tax_id") or "",
            "branch": seller.get("branch") or "",
            "address": seller.get("address") or "",
            "phone": seller.get("phone") or "",
        },
        "buyer": {
            "name": document.get("buyer_name") or "",
            "party_type": document.get("party_type") or "company",
            "tin_label": buyer_tin_label(document.get("party_type"), lang),
            "tin": document.get("tax_id") or "",
            "branch_type": document.get("branch_type") or "",
            "branch_no": document.get("branch_no") or "",
            "address": document.get("address") or "",
        },
        "lines": lines,
        "totals": {
            "subtotal": document.get("subtotal"),
            "discount_total": document.get("discount_total"),
            "vat_amount": document.get("vat_amount"),
            "wht_amount": document.get("wht_amount"),
            "grand_total": document.get("grand_total"),
            "net_payable": document.get("net_payable"),
        },
        "amount_in_words": contracts.baht_text(document.get("grand_total") or 0),
        "wht_label": (contracts.wht_label(document.get("wht_rate"), lang)
                      if document.get("wht_amount") else ""),
        "labels": {key: label(key, lang) for key in
                   ("doc_no", "doc_date", "buyer", "address", "branch", "subtotal",
                    "discount", "vat", "grand_total", "net_payable", "amount_in_words",
                    "item", "qty", "unit_price", "line_total")},
        # 便利件，不是税务合规件：未收款（或有部分未收）才出付款码。
        "promptpay": (promptpay_payload(seller.get("promptpay_id"), document.get("net_payable"))
                      if unpaid and seller.get("promptpay_id") else ""),
    }
