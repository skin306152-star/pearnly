"""Reconciliation contracts.

Normalization, both strong-key matching passes, per-field comparison, the
registered binary-float amount rule, the bank-statement balance chain, the
statement-versus-GL matching core, invoice-candidate scoring and the conversion
from already-decoded statement/GL pages into rows are migrated. Runtime adapters
are not: persistent VAT tasks, the statement/GL file readers, OCR and model
extraction and caching, candidate retrieval and result persistence, exports,
billing and the accountant review API still live in the legacy project. The
native bank task store in this package persists decoded rows only.
"""
