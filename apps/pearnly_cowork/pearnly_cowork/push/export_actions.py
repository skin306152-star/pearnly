"""Pure Express success-receipt projection for the review workbook.

The caller must establish success and authorization. Invoice fields are never
proof of ERP actions. Keep the deployed seq ordering and document-number aliases.
"""
from pearnly_cowork.push.receipts import _coerce_body
from pearnly_cowork.push.express.stock_acc_group import describe_from_request, stock_acc_group_label


def parse_actions(response_body, request_body=None, reported_groups=None):
    body = _coerce_body(response_body)
    meta = body.get('meta') if isinstance(body.get('meta'), dict) else {}
    customer = meta.get('created_customer')
    lines = body.get('line_modes')
    lines = [line for line in lines if isinstance(line, dict)] if isinstance(lines, list) else []
    try:
        lines = sorted(lines, key=lambda line: int(line.get('seq') or 0))
    except (ValueError, TypeError, OverflowError):
        # A malformed order cannot safely identify any invoice row. Retain the
        # document reference, but do not move later item codes onto earlier rows.
        lines = []
    return {
        'customer': customer if isinstance(customer, bool) else None,
        'items': [line.get('created') if isinstance(line.get('created'), bool) else None for line in lines],
        'item_codes': [str(line.get('stkcod') or '').strip() for line in lines],
        'docnum': str(body.get('express_docnum') or meta.get('docnum') or '').strip(),
        'party_code': str(meta.get('party_code') or '').strip(),
        'direction': str(meta.get('doc_type') or '').strip().lower(),
        'created_party': meta.get('created_customer') is True or meta.get('created_supplier') is True,
        'stock_acc_group': stock_acc_group_label(describe_from_request(request_body, reported_groups)),
    }
