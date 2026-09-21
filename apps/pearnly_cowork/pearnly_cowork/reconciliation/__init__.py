"""Reconciliation contracts.

Normalization, both strong-key matching passes, per-field comparison and the
registered binary-float amount rule are migrated. Runtime adapters are not:
persistent VAT/bank tasks, file parsing, exports, billing and the accountant
review API still live in the legacy project.
"""
