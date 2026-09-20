"""Authorized-history review workbook export; no database or external writes."""
from pearnly_cowork.roundtrip.export_mapping import ExportError, PreparedWorkbook, validate_records
from pearnly_cowork.roundtrip.export_writer import write_workbook


def build_review_workbook(records, *, push_facts=None) -> bytes:
    return write_workbook(validate_records(records, push_facts=push_facts))


__all__ = ["ExportError", "PreparedWorkbook", "validate_records", "build_review_workbook"]
