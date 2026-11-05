"""Pure JSON boundary for stocktake scan operations (request ids, revisions, void flags)."""
import json
import re
from uuid import UUID

from pearnly_cowork.stocktake.contracts import StocktakeValidationError


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
