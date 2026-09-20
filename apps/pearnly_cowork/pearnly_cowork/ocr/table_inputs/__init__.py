"""Legacy table/text decoding with explicit logical-source provenance."""
from .model import TableDocument, TableInputError
from .reader import extract_table

__all__ = ["TableDocument", "TableInputError", "extract_table"]
