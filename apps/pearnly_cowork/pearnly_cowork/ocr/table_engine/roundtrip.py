"""Recognize the legacy deterministic reimport before any model invocation.

Only the source sheet-name and real first-row fingerprint are used. A history
key in an uploaded cell never confers permission to another history record.
"""

from pearnly_cowork.roundtrip import spec

DATA_SHEETS = frozenset(spec.DATA_SHEETS)
REQUIRED_HEADERS = frozenset(spec.REQUIRED_HEADERS)


def has_roundtrip_sheet(provenance: dict) -> bool:
    """Match erp_roundtrip_reader's source sheet and row-1 header contract."""
    for unit in provenance.get("units", ()):
        # Sheet names and headers may be in any export language; compare spec names.
        if spec.canonical(unit.get("sheet_name")) not in DATA_SHEETS:
            continue
        # Invoice reimport reads formula text (data_only=False); normal table
        # recognition uses cached values. Do not infer its fingerprint from a
        # formula's cached result when the decoder supplies the real row.
        if "roundtrip_headers" in unit:
            headers = {spec.canonical(cell) for cell in unit["roundtrip_headers"]
                       if str(cell or "").strip()}
            if REQUIRED_HEADERS.issubset(headers):
                return True
            continue
        for row in unit.get("rows", ()):
            if row.get("source_row") != 1:
                continue
            headers = {spec.canonical(cell) for cell in row.get("cells", ())
                       if str(cell or "").strip()}
            if REQUIRED_HEADERS.issubset(headers):
                return True
    return False
