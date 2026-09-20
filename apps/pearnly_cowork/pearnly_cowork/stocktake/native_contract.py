"""Pure JSON boundary for decoded stocktake imports and scan operations."""
import hashlib
import json
import re
from uuid import UUID

from pearnly_cowork.stocktake.contracts import (
    ImportCell, MAX_ROWS, StocktakeValidationError, validate_import_rows,
)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identifier(value):
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise StocktakeValidationError("stocktake.request_invalid") from None


def revision(value):
    if isinstance(value, bool) or not re.fullmatch(r"0|[1-9][0-9]*", str(value)):
        raise StocktakeValidationError("stocktake.request_invalid")
    result = int(value)
    if result >= 2147483647:
        raise StocktakeValidationError("stocktake.request_invalid")
    return result


def void_flag(value):
    # RPC form encoding may send 0/1 strings. Reject other truthy values.
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value in ("0", "1", "true", "false"):
        return value in ("1", "true")
    raise StocktakeValidationError("stocktake.request_invalid")


def decoded_import(title, header, rows):
    """Revalidate decoded cells; this does not claim to validate XLSX bytes.

    The digest identifies this endpoint's validated row snapshot. File size,
    ZIP limits and the old raw-file digest belong to a future XLSX adapter.
    """
    if not isinstance(title, str) or not title.strip() or len(title) > 120:
        raise StocktakeValidationError("stocktake.name_invalid")
    if not isinstance(header, list) or not isinstance(rows, list):
        raise StocktakeValidationError("stocktake.row_invalid")
    if len(rows) > MAX_ROWS:
        raise StocktakeValidationError("stocktake.too_many_rows")
    cells = []
    for number, row in enumerate(rows, 2):
        if not isinstance(row, list):
            raise StocktakeValidationError("stocktake.row_invalid", number)
        decoded = []
        for cell in row:
            if (not isinstance(cell, dict) or set(cell) != {"value", "data_type"}
                    or not isinstance(cell["data_type"], str)
                    or type(cell["value"]) not in (str, int, float, bool, type(None))):
                raise StocktakeValidationError("stocktake.row_invalid", number)
            # A JSON caller cannot label a numeric identifier as a text cell.
            if cell["data_type"] == "s" and cell["value"] is not None and not isinstance(cell["value"], str):
                raise StocktakeValidationError("stocktake.code_as_text", number)
            decoded.append(ImportCell(**cell))
        cells.append(decoded)
    items = validate_import_rows(header, cells)
    for item in items:
        item["book_qty"] = format(item["book_qty"], ".6f")
    digest = hashlib.sha256(encode(items).encode()).hexdigest()
    return title.strip(), items, digest
