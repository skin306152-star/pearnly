"""Decoded logical sections; no framework, persistence, or paid provider access."""
from dataclasses import dataclass

from pearnly_cowork.ocr.schema import Page


class TableInputError(ValueError):
    def __init__(self, code, **details):
        self.code = code
        self.details = details
        super().__init__(code)


@dataclass(frozen=True)
class TableDocument:
    pages: tuple[Page, ...]
    page_count: int
    engine: str
    billing_characters: int
    provenance: dict
    source_sha256: str

    @property
    def coverage_complete(self):
        return self.provenance["coverage_complete"]


MAX_ARCHIVE_MEMBERS = 10_000
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 256
MAX_CELLS = 500_000
MAX_TEXT_CHARACTERS = 8_000_000


class DecodeBudget:
    def __init__(self):
        self.cells = 0
        self.characters = 0

    def row(self, cells):
        if len(cells) > MAX_COLUMNS:
            raise TableInputError("ocr.table_column_limit")
        self.cells += max(1, len(cells))
        self.characters += sum(len(cell) for cell in cells)
        if self.cells > MAX_CELLS:
            raise TableInputError("ocr.table_cell_limit")
        if self.characters > MAX_TEXT_CHARACTERS:
            raise TableInputError("ocr.table_text_limit")

    def text(self, text):
        self.characters += len(text)
        if self.characters > MAX_TEXT_CHARACTERS:
            raise TableInputError("ocr.table_text_limit")
