"""上传发票的识别调用（薄层）。

归一化在 `contracts.map_recognised`（纯函数、离线可断言）；这里只做"取图 → 问模型"。
**按页计费**，所以只由人点了"识别"的那条路径调用，测试与探针一律替换本模块的
`recognise`，不真花钱。

泰文税票提示词只要票面上真有的字段，**不要模型替我们算金额** —— 算错看不出来。
"""

import json

import frappe
from frappe import _

PROMPT = (
    "Read this Thai sales document (invoice, tax invoice, receipt or quotation) and return "
    "JSON with: doc_no, doc_date (YYYY-MM-DD), seller_name, buyer_type "
    "(company|person|foreign|anonymous), buyer_name, buyer_tax_id, buyer_branch_type "
    "(hq|branch), buyer_branch_no, buyer_address, has_vat (true/false), vat_rate, wht_rate, "
    "and lines: [{description, qty, unit, unit_price, discount}]. Return only fields that "
    "are printed on the document; never compute totals yourself."
)

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic")
ALLOWED_TYPES = IMAGE_TYPES + ("application/pdf",)


def recognise(content: bytes, content_type: str) -> dict:
    """认这张票，返回归一化**之前**的原始 JSON。没配好或认不出来都直接抛。"""
    from pearnly_core import ocr

    if content_type not in ALLOWED_TYPES:
        frappe.throw(_("Only an image or a PDF can be recognised"))
    if not ocr.is_configured():
        frappe.throw(_("Recognition is not configured for this account"))
    text = ocr.json_from_images(PROMPT, [(content, content_type)])
    if not text:
        frappe.throw(_("Recognition returned nothing"))
    try:
        payload = text if isinstance(text, dict) else json.loads(text)
    except ValueError:
        frappe.throw(_("Recognition returned nothing"))
    if not isinstance(payload, dict):
        frappe.throw(_("Recognition returned nothing"))
    return payload
