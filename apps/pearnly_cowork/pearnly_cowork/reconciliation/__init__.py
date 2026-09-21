"""Reconciliation contracts.

Normalization, both strong-key matching passes, per-field comparison, the
registered binary-float amount rule, the bank-statement balance chain, the
statement-versus-GL matching core and invoice-candidate scoring are migrated.
Runtime adapters are not: persistent VAT/bank tasks, statement/GL file parsing,
model extraction and caching, candidate retrieval and result persistence, exports,
billing and the accountant review API still live in the legacy project.
"""
