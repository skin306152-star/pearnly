"""Recognize the legacy deterministic reimport before any model invocation.

Only the source sheet-name and real first-row fingerprint are used. A history
key in an uploaded cell never confers permission to another history record.
"""

DATA_SHEETS = frozenset(("ขาย", "ซื้อ", "รอจำแนก"))
REQUIRED_HEADERS = frozenset((
    "เลขประจำตัวผู้เสียภาษีคู่ค้า", "ภาษีมูลค่าเพิ่มรวม", "เลขที่เอกสาร ERP",
    "รหัสสินค้า ERP", "รหัสคู่ค้า ERP", "สถานะการส่ง", "รหัสอ้างอิงระบบ",
))


def has_roundtrip_sheet(provenance: dict) -> bool:
    """Match erp_roundtrip_reader's source sheet and row-1 header contract."""
    for unit in provenance.get("units", ()):
        if str(unit.get("sheet_name") or "").strip() not in DATA_SHEETS:
            continue
        # Invoice reimport reads formula text (data_only=False); normal table
        # recognition uses cached values. Do not infer its fingerprint from a
        # formula's cached result when the decoder supplies the real row.
        if "roundtrip_headers" in unit:
            headers = {str(cell).strip() for cell in unit["roundtrip_headers"]
                       if str(cell or "").strip()}
            if REQUIRED_HEADERS.issubset(headers):
                return True
            continue
        for row in unit.get("rows", ()):
            if row.get("source_row") != 1:
                continue
            headers = {str(cell).strip() for cell in row.get("cells", ())
                       if str(cell or "").strip()}
            if REQUIRED_HEADERS.issubset(headers):
                return True
    return False
