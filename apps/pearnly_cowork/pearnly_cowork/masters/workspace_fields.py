"""Workspace import field contract, separate from buyer-client fields. No IO."""
import re

from .client_import import structural_error

LIMITS = {"display_name": 200, "tax_id": 40, "address": 500, "branch": 120, "phone": 50}
HEAD_OFFICE = "สำนักงานใหญ่"


class WorkspaceInputError(ValueError):
    pass


def values(raw):
    result = {}
    for field, limit in LIMITS.items():
        value = raw.get(field)
        if value is not None and not isinstance(value, str):
            raise WorkspaceInputError("client_import.err_generic")
        value = (value or "").strip()
        if len(value) > limit:
            raise WorkspaceInputError("client_import.err_generic")
        result[field] = value or None
    if not result["display_name"]:
        raise WorkspaceInputError("client_import.err_missing_name")
    result["branch"] = result["branch"] or HEAD_OFFICE
    return result


def import_values(row):
    result = values({"display_name": row.get("name"), **{f: row.get(f) for f in LIMITS if f != "display_name"}})
    error = structural_error(result["display_name"], result["tax_id"])
    if error:
        raise WorkspaceInputError(error)
    return result


def tax_identity(value):
    value = (value or "").strip()
    digits = re.sub(r"[^0-9]", "", value)
    return digits if len(digits) == 13 else value.casefold()
