"""Invoice extraction from decoded table/text units; no file or framework IO."""
from pearnly_cowork.ocr.table_engine.runner import run_table, validate_document

__all__ = ["run_table", "validate_document"]
