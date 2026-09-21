"""Reconciliation contracts.

Normalization, both strong-key matching passes, per-field comparison, the
registered binary-float amount rule and the bank-statement balance chain are
migrated. Runtime adapters are not: persistent VAT/bank tasks, statement/GL file
parsing, model extraction and caching, matching/scoring, exports, billing and the
accountant review API still live in the legacy project.
"""
