"""Bank-reconciliation review workbook (XLSX) as a pure presentation layer.

Source: ec2e5c957e6b ``services/recon/bank_recon_excel*.py``. The caller owns the
authorization, the task lock and the row and summary data; this package only
renders them, in the four languages the deployed workbook shipped. Layout,
labels, colors and the file-info fallback keep the deployed behaviour.
"""
from pearnly_cowork.reconciliation.export.workbook import workbook

__all__ = ["workbook"]
