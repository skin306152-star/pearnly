"""Reconciliation contracts.

Normalization, both strong-key matching passes, per-field comparison, the
registered binary-float amount rule, the bank-statement balance chain, the
statement-versus-GL matching core, invoice-candidate scoring, the conversion from
already-decoded statement/GL pages into rows and the four-sheet review workbook are
migrated. Runtime adapters are not: persistent VAT tasks, the statement/GL file
readers, OCR and model extraction and caching, candidate retrieval and result
persistence, the GL/VAT workbook, billing and the accountant review API still live
in the legacy project. The native bank task store in this package persists decoded
rows and renders the workbook from them.
"""
