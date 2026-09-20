"""Invoice source-column warnings from ec2e5c957e6b; never erase raw evidence."""
from datetime import date
from typing import List, Optional, Set
from pearnly_cowork.ocr.schema.invoice import ThaiInvoice
from pearnly_cowork.ocr.validation.dates import validate_invoice_date

_DESCRIPTION_COLUMN_KEYWORDS: Set[str] = {'description', 'remark', 'remarks', 'note', 'notes', 'detail', 'details', 'particulars', 'narrative', 'รายการ', 'คําอธิบาย', 'คำอธิบาย', 'หมายเหตุ', '摘要', '说明', '备注', 'tekijä', 'memo'}
_VOUCHER_COLUMN_KEYWORDS: Set[str] = {'voucher', 'voucher no', 'voucher no.', 'voucherno', 'vch', 'vch no', 'doc no', 'doc no.', 'document no', 'journal no', 'jv', 'jv no', 'reference', 'ref', 'ref no', 'ref no.', 'เลขที่เอกสาร', 'เลขที่ใบสำคัญ', 'เลขที่อ้างอิง', 'เลขที่', '凭证号', '凭证编号'}
_ACCOUNT_CODE_COLUMN_KEYWORDS: Set[str] = {'account code', 'acc code', 'a/c code', 'account no', 'a/c no', 'รหัสบัญชี', 'เลขที่บัญชี', '科目代码', '科目编号'}

def _norm_col(col: str) -> str:
    """Lowercase, strip, drop trailing punctuation — for column header match."""
    if not col:
        return ''
    s = col.strip().lower()
    s = s.rstrip(':.•·').strip()
    return s

def _is_forbidden_amount_source(source_column: str) -> Optional[str]:
    """Return the rejection reason if source_column is a description /
    voucher / account-code column. None = OK."""
    c = _norm_col(source_column)
    if not c:
        return None
    if c in _DESCRIPTION_COLUMN_KEYWORDS:
        return f'sourced from description column ({source_column!r})'
    if c in _VOUCHER_COLUMN_KEYWORDS:
        return f'sourced from voucher-number column ({source_column!r})'
    if c in _ACCOUNT_CODE_COLUMN_KEYWORDS:
        return f'sourced from account-code column ({source_column!r})'
    return None

def validate_invoice_sources(inv: ThaiInvoice, *, today: date) -> List[str]:
    """Field-source check for invoices: any source_refs present must NOT
    have description as their source_column for amount fields."""
    warnings: List[str] = validate_invoice_date(inv, today=today)
    if not inv.source_refs:
        return warnings
    for (fname, ref) in inv.source_refs.items():
        if fname not in ('total_amount', 'subtotal', 'vat', 'wht_amount'):
            continue
        reason = _is_forbidden_amount_source(ref.source_column)
        if reason:
            warnings.append(f'Invoice {fname}={ref.value!r} {reason} — likely mis-sourced')
    return warnings
