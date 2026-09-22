"""Bounded uploaded roster bytes -> deployed header/row contract. No persistence."""
import io
import zipfile

from pearnly_cowork.tabular import readers
from pearnly_cowork.tabular.summary import is_summary_row
from . import client_import

TOTAL_CELL = frozenset({"รวม", "รวมทั้งสิ้น", "total", "sum", "合计", "总计", "小计"})
MAX_CELLS = 250_000


def _cell(value):
    return "" if value is None else str(value).strip()


def _numeric(value):
    try:
        float(value.replace(",", "").strip())
        return True
    except (ValueError, AttributeError):
        return False


def _header(cells):
    nonempty = [cell for cell in cells if cell.strip()]
    return len(nonempty) >= 3 and sum(_numeric(cell) for cell in nonempty) * 2 <= len(nonempty)


def from_sheets(sheets):
    # Same first nonempty sheet / first 15 nonempty-row header scan as legacy.
    nonempty = []
    for _, rows in sheets:
        nonempty = [[_cell(cell) for cell in row] for row in rows if any(_cell(cell) for cell in row)]
        if nonempty:
            break
    if not nonempty:
        raise ValueError("client_import.header_not_recognized")
    index = next((i for i, cells in enumerate(nonempty[:15]) if _header(cells)), 0)
    rows = [{"index": i, "cells": cells,
        "is_summary": any(is_summary_row(cell) or cell.lower() in TOTAL_CELL for cell in cells)}
        for i, cells in enumerate(nonempty[index + 1:index + 2001])]
    result = client_import.import_rows(nonempty[index], rows)
    result["truncated"] = result["truncated"] or len(nonempty[index + 1:]) > 2000
    if not result["name_column_found"]:
        raise ValueError("client_import.header_not_recognized")
    return result


def parse(content, filename=""):
    if len(content) > client_import.MAX_BYTES:
        raise ValueError("client_import.file_too_large")
    binary = content.startswith((b"PK", b"\xd0\xcf\x11\xe0"))
    if content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 64 * 1024 * 1024:
                    raise ValueError("client_import.file_too_large")
        except zipfile.BadZipFile as error:
            raise ValueError("client_import.header_not_recognized") from error
    # Recognized binary must never be treated as Latin-1 CSV after a decoder failure.
    sheets = readers.load_workbook_sheets(content, max_cells=MAX_CELLS) if binary else readers.load_csv_sheets(content, max_cells=MAX_CELLS)
    return from_sheets(sheets)
