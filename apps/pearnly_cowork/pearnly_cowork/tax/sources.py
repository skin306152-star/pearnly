"""报税草稿的数据来源（只读）：Express 回读的 ISVAT（`readback`）与识别记录、50 ทวิ 参考记录（`history`）。

识别记录经 DocType 自己的权限查询读（`frappe.get_list`，同智能体列表工具），先核读识别记录的权限。
"""
import json
from datetime import timedelta

import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.contracts.history import primary_page_fields
from pearnly_cowork.history import certificates
from pearnly_cowork.tax import input_vat, wht

HISTORY = "Cowork OCR History"
PAGE = 200
SCAN_MAX = 5000
CHUNK = 200
WHT_FIELDS = ("document_type", "document_flag", "direction", "invoice_number", "date", "seller_name", "seller_tax",
              "seller_addr", "seller_branch", "buyer_name", "buyer_tax", "buyer_addr", "buyer_branch", "subtotal",
              "discount", "vat", "total_amount", "wht_rate", "wht_amount", "payment_method", "posting_payment_manual",
              "category")


def vat_readback(connection, account_set_key, year, month):
    """该账套该月当前用户最近一次完成的 Express 回读（`readback.service.vat_records` 的约定形状）；没有回 None。"""
    try:
        from pearnly_cowork.readback import service
    except ModuleNotFoundError as error:
        if error.name not in ("pearnly_cowork.readback", "pearnly_cowork.readback.service"):
            raise
        return None
    return service.vat_records(connection, account_set_key, year, month)


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


def _fields(pages) -> dict:
    try:
        pages = json.loads(pages) if isinstance(pages, str) else pages
    except ValueError:
        return {}
    return primary_page_fields(pages if isinstance(pages, list) else [])


def can_read_documents() -> bool:
    return scope.allowed_any(scope.READ_FEATURES)


def purchase_documents(purchases) -> dict:
    """进项行能关联到的识别记录主页字段：{记录名: 字段, document_key(卖方税号, 票号): 字段}。"""
    names = sorted({record.history for record in purchases if record.history})
    numbers = sorted({record.refnum for record in purchases if record.refnum})
    found = {}
    lookups = [{"name": ["in", chunk]} for chunk in _chunks(names)]
    lookups += [{"invoice_no": ["in", chunk]} for chunk in _chunks(numbers)]
    for filters in lookups:
        for row in frappe.get_list(HISTORY, filters=filters, fields=["name", "invoice_no", "seller_tax", "pages"],
                                   limit_page_length=0):
            fields = _fields(row.get("pages"))
            if not fields.get("document_type"):  # 没有可读的主页：当作没关联到，不说成「不是税票」
                continue
            found[row.name] = fields
            found[input_vat.document_key(fields.get("seller_tax") or row.get("seller_tax"), row.get("invoice_no"))] = fields
    return found


def wht_documents(workspace, company_tax_id, start, end) -> tuple[list, bool]:
    """这家公司（付款方税号）在 [start − 90 天, end] 的识别记录与 50 ทวิ 参考记录：
    ([{ref, origin, fields}], 是否只看了最新的 SCAN_MAX 张)。按票据日取：付款日只会等于或晚于票据日。"""
    scope.require_read()
    company = wht.tax_id(company_tax_id)
    window = start - timedelta(days=wht.LOOKBACK_DAYS)
    heads, offset, truncated = [], 0, False
    while True:
        rows = frappe.get_list(HISTORY, filters={"invoice_date": ["between", [window, end]]},
                               fields=["name", "seller_tax", "buyer_tax"], order_by="invoice_date desc, name desc",
                               limit_start=offset, limit_page_length=PAGE)
        heads += [row.name for row in rows
                  if company in (wht.tax_id(row.get("seller_tax")), wht.tax_id(row.get("buyer_tax")))]
        offset += PAGE
        if len(rows) < PAGE:
            break
        if offset >= SCAN_MAX:
            truncated = True
            break
    documents = []
    for chunk in _chunks(heads):
        for row in frappe.get_list(HISTORY, filters={"name": ["in", chunk]}, fields=["name", "pages"],
                                   limit_page_length=0):
            fields = _fields(row.get("pages"))
            documents.append({"ref": row.name, "origin": "history",
                              "fields": {key: fields.get(key) for key in WHT_FIELDS if fields.get(key) not in (None, "")}})
    documents.sort(key=lambda item: (str(item["fields"].get("date") or ""), item["ref"]))
    documents += certificates.listing(workspace, window, end)
    return documents, truncated
