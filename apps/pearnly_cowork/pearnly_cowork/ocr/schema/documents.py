"""Bank-statement, general-ledger and VAT-report document shapes from deployed ec2e5c957e6b.

Source: ``services/ocr/schemas_documents.py`` (the non-invoice document types the
reconciliation reads). ``VatReportDocument`` moved with the scanned / photographed
VAT report path on 2026-09-29; ``GenericTableDocument`` has no reading path and is
not declared here, so nothing pretends to accept it.

Every amount is a string exactly as printed (the deployed contract): the model
reads columns, the deterministic converters and repairs do the arithmetic in
Decimal. The field-source contract — deposit/withdrawal/balance and debit/credit
come from their own columns, never from a description or reference number — is
stated on each field and enforced separately by the column-source validators.

``BankStatementEntry`` carries the deployed audit markers the balance chain sets:
``chain_repaired`` (the swap was proved by arithmetic), ``chain_amount_imputed``
(the amount was inferred from the printed balance) and ``review_required``
(an imputed amount has no second image evidence, so it is never a final posting).
Pure schema: pydantic only, no Frappe, database, file, clock, HTTP or model access.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from pearnly_cowork.ocr.schema.evidence import FieldRef


class GLEntry(BaseModel):
    """One row of a general ledger.

    Field-source contract: debit / credit / balance come from their own columns;
    ``amount`` is the derived magnitude (debit when debit > 0, else credit) and
    ``direction`` is its bank view (debit = money in). Numbers printed in the
    description, voucher number or account code (6091, JV681130.1, 1112-07) must
    never be read as an amount.
    """

    transaction_date: str = Field(default="", description="YYYY-MM-DD or empty")
    transaction_date_raw: str = Field(default="", description="date text as printed")
    voucher_no: str = Field(default="", description="voucher/document number, e.g. JV681130.1")
    account_code: str = Field(default="", description="account code, e.g. 1112-07")
    description: str = Field(
        default="",
        description="row description text — may contain numbers like '6091' "
        "that MUST NOT be treated as amounts",
    )
    debit: str = Field(
        default="", description="debit amount, number-as-string no commas; empty = no debit"
    )
    credit: str = Field(
        default="", description="credit amount, number-as-string no commas; empty = no credit"
    )
    amount: str = Field(
        default="",
        description="derived: debit if debit>0 else credit (number-as-string). "
        "Always sourced from Debit/Credit column — never description.",
    )
    direction: Literal["deposit", "withdrawal", ""] = Field(
        default="",
        description="'deposit' = bank goes up (debit>0); 'withdrawal' = bank goes down (credit>0)",
    )
    balance: str = Field(default="", description="running balance, number-as-string")

    debit_ref: Optional[FieldRef] = Field(default=None)
    credit_ref: Optional[FieldRef] = Field(default=None)
    balance_ref: Optional[FieldRef] = Field(default=None)

    raw_row_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="original column→cell mapping for audit / re-parse",
    )

    @field_validator(
        "transaction_date",
        "transaction_date_raw",
        "voucher_no",
        "account_code",
        "description",
        "debit",
        "credit",
        "amount",
        "balance",
        mode="before",
    )
    @classmethod
    def _coerce_gl_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return v

    @field_validator("direction", mode="before")
    @classmethod
    def _coerce_direction(cls, v):
        if v is None or v == "":
            return ""
        if v in ("deposit", "withdrawal"):
            return v
        return ""

    @field_validator("raw_row_data", mode="before")
    @classmethod
    def _coerce_raw_row(cls, v):
        return {} if v is None else v


class GeneralLedgerDocument(BaseModel):
    """A general ledger — many rows plus the printed summary metadata.

    ``total_debit`` / ``total_credit`` are the printed footer totals. On the
    stacked layouts that merge the debit and credit columns they are the only
    deterministic source of the first row's direction, which is why the balance
    chain reads them.
    """

    document_type: Literal["general_ledger"] = "general_ledger"
    period_start: str = Field(default="", description="YYYY-MM-DD or empty")
    period_end: str = Field(default="", description="YYYY-MM-DD or empty")
    account_name: str = Field(default="", description="bank account / GL account name")
    account_number: str = Field(default="", description="account number if printed")
    opening_balance: str = Field(default="")
    closing_balance: str = Field(default="")
    total_debit: str = Field(default="", description="printed footer Total Debit or empty")
    total_credit: str = Field(default="", description="printed footer Total Credit or empty")
    entries: List[GLEntry] = Field(default_factory=list)

    # A model returns null for a continued page or a page without an opening
    # balance; the deployed contract coerces those back to "" instead of failing.
    @field_validator(
        "period_start",
        "period_end",
        "account_name",
        "account_number",
        "opening_balance",
        "closing_balance",
        "total_debit",
        "total_credit",
        mode="before",
    )
    @classmethod
    def _coerce_gl_doc_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            return str(v)
        return v

    @field_validator("entries", mode="before")
    @classmethod
    def _coerce_entries(cls, v):
        return [] if v is None else v


class BankStatementEntry(BaseModel):
    """One row of a bank statement.

    Field-source contract: deposit / withdrawal / balance come from their own
    columns; ``amount`` is the derived magnitude and ``direction`` its view.
    Reference and description numbers must never be read as amounts.
    """

    transaction_date: str = Field(default="")
    transaction_date_raw: str = Field(default="")
    description: str = Field(default="")
    reference: str = Field(default="", description="reference / transaction code")
    deposit: str = Field(default="", description="money in / เงินเข้า / credit column")
    withdrawal: str = Field(default="", description="money out / เงินออก / debit column")
    amount: str = Field(default="", description="derived: deposit if deposit>0 else withdrawal")
    direction: Literal["deposit", "withdrawal", ""] = Field(default="")
    balance: str = Field(default="", description="running balance")
    chain_repaired: bool = Field(
        default=False,
        description="the balance chain swapped this row's deposit/withdrawal columns; "
        "only set when the arithmetic proves it",
    )
    chain_amount_imputed: bool = Field(
        default=False,
        description="the balance chain inferred this row's single-sided amount from the "
        "printed balance and overwrote it",
    )
    review_required: bool = Field(
        default=False,
        description="the amount is chain-inferred (balance back-calculation) with no second "
        "image evidence, so posting and export must treat it as still to be reviewed; "
        "a proved column swap is not flagged",
    )

    deposit_ref: Optional[FieldRef] = Field(default=None)
    withdrawal_ref: Optional[FieldRef] = Field(default=None)
    balance_ref: Optional[FieldRef] = Field(default=None)

    @field_validator(
        "transaction_date",
        "transaction_date_raw",
        "description",
        "reference",
        "deposit",
        "withdrawal",
        "amount",
        "balance",
        mode="before",
    )
    @classmethod
    def _coerce_bank_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return v

    @field_validator("direction", mode="before")
    @classmethod
    def _coerce_direction(cls, v):
        if v is None or v == "":
            return ""
        if v in ("deposit", "withdrawal"):
            return v
        return ""


class BankStatementDocument(BaseModel):
    document_type: Literal["bank_statement"] = "bank_statement"
    bank_name: str = Field(default="")
    bank_code: str = Field(default="", description="kbank/bbl/scb/etc. or empty")
    account_name: str = Field(default="")
    account_number: str = Field(default="")
    account_last4: str = Field(default="")
    period_start: str = Field(default="")
    period_end: str = Field(default="")
    opening_balance: str = Field(default="")
    closing_balance: str = Field(default="")
    entries: List[BankStatementEntry] = Field(default_factory=list)

    @field_validator(
        "bank_name",
        "bank_code",
        "account_name",
        "account_number",
        "account_last4",
        "period_start",
        "period_end",
        "opening_balance",
        "closing_balance",
        mode="before",
    )
    @classmethod
    def _coerce_doc_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, (int, float)):
            return str(v)
        return v

    @field_validator("entries", mode="before")
    @classmethod
    def _coerce_entries(cls, v):
        return [] if v is None else v


# 销项 / 进项税报告：ec2e5c957e6b ``services/ocr/schemas_documents.py`` 逐字照搬（2026-09-29，扫描件与照片的税报读取）。
class VatReportEntry(BaseModel):
    """One row of a VAT report (销项/进项税报告)."""

    seq_no: str = Field(default="", description="row sequence number on report")
    transaction_date: str = Field(default="")
    transaction_date_raw: str = Field(default="")
    invoice_no: str = Field(default="")
    customer_name: str = Field(default="")
    customer_tax: str = Field(default="", description="13-digit Thai tax ID")
    customer_branch: str = Field(default="", description="买方分公司 / สำนักงานใหญ่ etc.")
    subtotal: str = Field(default="", description="净额, number-as-string")
    vat: str = Field(default="")
    total: str = Field(default="")

    invoice_no_ref: Optional[FieldRef] = Field(default=None)
    subtotal_ref: Optional[FieldRef] = Field(default=None)
    vat_ref: Optional[FieldRef] = Field(default=None)
    total_ref: Optional[FieldRef] = Field(default=None)

    raw_row_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "seq_no",
        "transaction_date",
        "transaction_date_raw",
        "invoice_no",
        "customer_name",
        "customer_tax",
        "customer_branch",
        "subtotal",
        "vat",
        "total",
        mode="before",
    )
    @classmethod
    def _coerce_vat_str(cls, v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return v

    @field_validator("raw_row_data", mode="before")
    @classmethod
    def _coerce_raw_row(cls, v):
        return {} if v is None else v


class VatReportDocument(BaseModel):
    document_type: Literal["vat_report"] = "vat_report"
    seller_name: str = Field(default="", description="申报方公司名 (报告主体)")
    seller_tax: str = Field(default="")
    period_year: str = Field(default="", description="public year, e.g. '2026'")
    period_month: str = Field(default="", description="'01'..'12'")
    total_subtotal: str = Field(default="")
    total_vat: str = Field(default="")
    total_total: str = Field(default="")
    entries: List[VatReportEntry] = Field(default_factory=list)

    @field_validator("entries", mode="before")
    @classmethod
    def _coerce_entries(cls, v):
        return [] if v is None else v
