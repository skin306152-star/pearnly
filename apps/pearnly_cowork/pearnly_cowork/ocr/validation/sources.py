"""Source-column checks for every document type from ec2e5c957e6b.

Source: ``services/ocr/validators.py`` — the module that stops a number printed
in a description, voucher or account-code column from being read as an amount
(the deployed "6091 case": a ledger row whose description contains 6091 was once
taken as a debit). The keyword tables and helpers are shared by all three document
types exactly as the deployed file shared them, so the same header text is judged
the same way everywhere.

Two things the deployed file carried but this module does not:

* ``_AMOUNT_TOKEN_RE`` is defined there and **never used** by anything in the
  deployed project, so it is not carried over (there is no rule to preserve).
* ``page`` is part of the deployed signatures and is unused by every rule: the
  checks read the row's own provenance and text. The parameter is kept so the
  wiring layer calls the same shape, and because a page-level containment check
  is a separate rule that already lives in ``confidence``.

Everything here only ever reports and, where the deployed rule did, clears the
value that came from a forbidden column — raw evidence is never erased.
"""
from datetime import date
import re
from typing import List, Optional, Set
from pearnly_cowork.ocr.schema.documents import (
    BankStatementDocument,
    BankStatementEntry,
    GeneralLedgerDocument,
    GLEntry,
)
from pearnly_cowork.ocr.schema.evidence import Page
from pearnly_cowork.ocr.schema.invoice import ThaiInvoice
from pearnly_cowork.ocr.validation.dates import validate_invoice_date

_DESCRIPTION_COLUMN_KEYWORDS: Set[str] = {'description', 'remark', 'remarks', 'note', 'notes', 'detail', 'details', 'particulars', 'narrative', 'รายการ', 'คําอธิบาย', 'คำอธิบาย', 'หมายเหตุ', '摘要', '说明', '备注', 'tekijä', 'memo'}
_VOUCHER_COLUMN_KEYWORDS: Set[str] = {'voucher', 'voucher no', 'voucher no.', 'voucherno', 'vch', 'vch no', 'doc no', 'doc no.', 'document no', 'journal no', 'jv', 'jv no', 'reference', 'ref', 'ref no', 'ref no.', 'เลขที่เอกสาร', 'เลขที่ใบสำคัญ', 'เลขที่อ้างอิง', 'เลขที่', '凭证号', '凭证编号'}
_ACCOUNT_CODE_COLUMN_KEYWORDS: Set[str] = {'account code', 'acc code', 'a/c code', 'account no', 'a/c no', 'รหัสบัญชี', 'เลขที่บัญชี', '科目代码', '科目编号'}

# Allowed source columns for GL amount/debit/credit
_GL_DEBIT_COLUMNS: Set[str] = {'debit', 'dr', 'เดบิต', '借方', '借'}
_GL_CREDIT_COLUMNS: Set[str] = {'credit', 'cr', 'เครดิต', '贷方', '贷'}
_GL_BALANCE_COLUMNS: Set[str] = {'balance', 'running balance', 'ending balance', 'ยอดคงเหลือ', 'คงเหลือ', '余额'}

# Allowed source columns for Bank Statement amount/deposit/withdrawal
_BANK_DEPOSIT_COLUMNS: Set[str] = {'deposit', 'credit', 'in', 'money in', 'amount in', 'received', 'เงินเข้า', 'ฝาก', 'รับ', '存入', '存款', '收入'}
_BANK_WITHDRAWAL_COLUMNS: Set[str] = {'withdrawal', 'debit', 'out', 'money out', 'amount out', 'paid', 'เงินออก', 'ถอน', 'จ่าย', '支出', '取款', '提款'}
_BANK_BALANCE_COLUMNS: Set[str] = _GL_BALANCE_COLUMNS

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


def _is_allowed_gl_amount_source(source_column: str, kind: str) -> bool:
    """Is this the right column for a 'debit' / 'credit' / 'balance' amount?

    An empty source column means the reader did not record where the number came
    from; that cannot be disproved, so it is not flagged.
    """
    c = _norm_col(source_column)
    if not c:
        return True
    if kind == 'debit':
        return c in _GL_DEBIT_COLUMNS
    if kind == 'credit':
        return c in _GL_CREDIT_COLUMNS
    if kind == 'balance':
        return c in _GL_BALANCE_COLUMNS
    return True


def _is_allowed_bank_amount_source(source_column: str, kind: str) -> bool:
    """Same question for a bank statement's 'deposit' / 'withdrawal' / 'balance'."""
    c = _norm_col(source_column)
    if not c:
        return True
    if kind == 'deposit':
        return c in _BANK_DEPOSIT_COLUMNS
    if kind == 'withdrawal':
        return c in _BANK_WITHDRAWAL_COLUMNS
    if kind == 'balance':
        return c in _BANK_BALANCE_COLUMNS
    return True


def _value_appears_in_description(value: str, description: str) -> bool:
    """Does this amount token literally appear in the other text?

    This is the only safe signal that a number was grabbed from the wrong column:
    the value has to appear as a **standalone** token (nothing numeric touching it)
    so a real reference such as "Invoice 6091/2024" is not treated as the amount.
    A trailing ``.0`` / ``.00`` is also stripped and retried, so a debit printed as
    ``6091.00`` still matches a description that only says ``6091``.
    """
    if not value or not description:
        return False
    v = value.replace(',', '').strip()
    if not v:
        return False
    if re.search(r'(?<!\d)' + re.escape(v) + r'(?!\d)', description):
        return True
    # Strip trailing .0 / .00 zeros and try the integer form
    if '.' in v:
        v_int = v.rstrip('0').rstrip('.')
        if v_int and v_int != v and re.search(r'(?<!\d)' + re.escape(v_int) + r'(?!\d)',
                                              description):
            return True
    return False


# ── 总账（那张 6091 的表就在这里被挡下来）────────────────────────────────
def validate_gl_document(doc: GeneralLedgerDocument, page: Page) -> List[str]:
    """Run every GL rule over the rows; returns warnings (empty = clean).

    R1. a debit/credit/balance reference must come from an amount column — a
        description, voucher or account-code column is rejected **and the value
        it carried is cleared**, so nothing downstream can use the wrong number;
    R2/R5. a value that also appears as a standalone token in the description
        (and not in the voucher or account code, where numbers are legitimate) is
        reported as possibly mis-sourced;
    R3. debit and credit are mutually exclusive;
    R4. the direction has to agree with that split;
    R5b. the derived magnitude has to agree with debit-or-credit.
    """
    warnings: List[str] = []
    for idx, entry in enumerate(doc.entries):
        row_id = entry.voucher_no or f'row#{idx + 1}'

        # R1: source-column check (when refs are present)
        for ref_name, ref, expected_kind in (
            ('debit_ref', entry.debit_ref, 'debit'),
            ('credit_ref', entry.credit_ref, 'credit'),
            ('balance_ref', entry.balance_ref, 'balance'),
        ):
            if ref is None:
                continue
            reason = _is_forbidden_amount_source(ref.source_column)
            if reason:
                warnings.append(f'GL {row_id}: {ref_name}={ref.value!r} {reason} — rejected')
                _clear_amount_field(entry, ref_name)
                continue
            if not _is_allowed_gl_amount_source(ref.source_column, expected_kind):
                warnings.append(
                    f'GL {row_id}: {ref_name} source column '
                    f'{ref.source_column!r} is not a {expected_kind} column'
                )

        # R2 + R5: 6091-style description-leak check
        for amount_field in ('debit', 'credit', 'amount', 'balance'):
            value = getattr(entry, amount_field, '') or ''
            if not value:
                continue
            if _value_appears_in_description(value, entry.description):
                # A number that also sits in the voucher or account code is
                # legitimately numeric there, so the row is left alone.
                if _value_appears_in_description(
                    value, entry.voucher_no
                ) or _value_appears_in_description(value, entry.account_code):
                    pass  # ambiguous — skip
                else:
                    warnings.append(
                        f'GL {row_id}: {amount_field}={value!r} also appears '
                        f'in description={entry.description!r} — possibly '
                        f'mis-sourced from description column'
                    )

        # R3: debit & credit mutually exclusive
        try:
            d = float(entry.debit) if entry.debit else 0.0
            c = float(entry.credit) if entry.credit else 0.0
        except ValueError:
            d = c = 0.0
            warnings.append(
                f'GL {row_id}: debit/credit not numeric '
                f'(debit={entry.debit!r}, credit={entry.credit!r})'
            )
        else:
            if d > 0 and c > 0:
                warnings.append(
                    f'GL {row_id}: both debit ({d}) AND credit ({c}) non-zero '
                    f'— GL rows must have exactly one'
                )

        # R4: direction consistency
        if entry.direction == 'deposit' and d <= 0:
            warnings.append(
                f'GL {row_id}: direction=deposit but debit={d} (deposit requires debit>0)'
            )
        elif entry.direction == 'withdrawal' and c <= 0:
            warnings.append(
                f'GL {row_id}: direction=withdrawal but credit={c} (withdrawal requires credit>0)'
            )

        # R5b: amount derivation check
        if entry.amount:
            try:
                a = float(entry.amount)
                expected = d if d > 0 else c
                if abs(a - expected) > 0.01 and (d > 0 or c > 0):
                    warnings.append(
                        f"GL {row_id}: amount={a} doesn't match "
                        f"debit-or-credit={expected}"
                    )
            except ValueError:
                warnings.append(f'GL {row_id}: amount={entry.amount!r} not numeric')

    return warnings


def _clear_amount_field(entry: GLEntry, ref_name: str) -> None:
    """Drop a value whose reference was rejected, so nothing downstream uses it."""
    if ref_name == 'debit_ref':
        entry.debit = ''
        entry.debit_ref = None
        if entry.direction == 'deposit':
            entry.direction = ''
            entry.amount = ''
    elif ref_name == 'credit_ref':
        entry.credit = ''
        entry.credit_ref = None
        if entry.direction == 'withdrawal':
            entry.direction = ''
            entry.amount = ''
    elif ref_name == 'balance_ref':
        entry.balance = ''
        entry.balance_ref = None


# ── 银行对账单 ──────────────────────────────────────────────────────────
def validate_bank_document(doc: BankStatementDocument, page: Page) -> List[str]:
    """Same idea as the GL side, for a statement's deposit/withdrawal/balance.

    Amounts must come from the matching column, and a number that also appears in
    the description (and not in the reference, where numbers are legitimate) is
    reported. Deposit and withdrawal cannot both be filled.
    """
    warnings: List[str] = []
    for idx, entry in enumerate(doc.entries):
        row_id = entry.reference or f'tx#{idx + 1}'

        for ref_name, ref, expected_kind in (
            ('deposit_ref', entry.deposit_ref, 'deposit'),
            ('withdrawal_ref', entry.withdrawal_ref, 'withdrawal'),
            ('balance_ref', entry.balance_ref, 'balance'),
        ):
            if ref is None:
                continue
            reason = _is_forbidden_amount_source(ref.source_column)
            if reason:
                warnings.append(f'Bank {row_id}: {ref_name}={ref.value!r} {reason} — rejected')
                _clear_bank_amount_field(entry, ref_name)
                continue
            if not _is_allowed_bank_amount_source(ref.source_column, expected_kind):
                warnings.append(
                    f'Bank {row_id}: {ref_name} source column '
                    f'{ref.source_column!r} is not a {expected_kind} column'
                )

        # 6091-style description leak check
        for amount_field in ('deposit', 'withdrawal', 'balance'):
            value = getattr(entry, amount_field, '') or ''
            if not value:
                continue
            if _value_appears_in_description(value, entry.description):
                if not _value_appears_in_description(value, entry.reference):
                    warnings.append(
                        f'Bank {row_id}: {amount_field}={value!r} also appears '
                        f'in description={entry.description!r} — possibly mis-sourced'
                    )

        # Deposit & withdrawal mutually exclusive
        try:
            d = float(entry.deposit) if entry.deposit else 0.0
            w = float(entry.withdrawal) if entry.withdrawal else 0.0
        except ValueError:
            d = w = 0.0
        else:
            if d > 0 and w > 0:
                warnings.append(f'Bank {row_id}: both deposit ({d}) AND withdrawal ({w}) non-zero')

    return warnings


def _clear_bank_amount_field(entry: BankStatementEntry, ref_name: str) -> None:
    if ref_name == 'deposit_ref':
        entry.deposit = ''
        entry.deposit_ref = None
        if entry.direction == 'deposit':
            entry.direction = ''
            entry.amount = ''
    elif ref_name == 'withdrawal_ref':
        entry.withdrawal = ''
        entry.withdrawal_ref = None
        if entry.direction == 'withdrawal':
            entry.direction = ''
            entry.amount = ''
    elif ref_name == 'balance_ref':
        entry.balance = ''
        entry.balance_ref = None
