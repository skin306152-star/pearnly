"""Pure preparation for the fixed review-workbook protocol.

The caller authorizes every history and supplies any verified ERP facts. This
module never looks up records or treats uploaded invoice fields as push proof.
"""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from pearnly_cowork.roundtrip import money


class ExportError(ValueError):
    def __init__(self, code, *, record_index=0, field=""):
        self.code, self.record_index, self.field = code, record_index, field
        super().__init__(code)


@dataclass(frozen=True)
class PreparedWorkbook:
    sales: tuple[dict, ...]
    purchase: tuple[dict, ...]
    pending: tuple[dict, ...]
    created_masters: tuple[dict, ...]


def text(value):
    if value is None:
        return ""
    result = str(value).strip()
    result = "" if result.lower() in ("none", "null", "nan") else result
    if len(result) > 32767 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", result):
        raise ExportError("roundtrip.export_text_invalid")
    return result


def number(value, *, allow_thb=True):
    """Decimal calculation with an explicit Excel numeric precision boundary."""
    if value is None or value == "":
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple)):
        raise ExportError("roundtrip.export_number_invalid")
    raw = str(value).replace(",", "").replace("฿", "").strip()
    if allow_thb:
        raw = raw.replace("THB", "")
    try:
        result = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ExportError("roundtrip.export_number_invalid") from None
    if not result.is_finite():
        raise ExportError("roundtrip.export_number_invalid")
    # Excel stores only 15 significant decimal digits. Refuse values that its
    # numeric cells could silently change; identifiers are written as strings.
    normalized = result.normalize()
    if result and (len(normalized.as_tuple().digits) > 15 or not -307 <= result.adjusted() <= 14):
        raise ExportError("roundtrip.export_number_precision")
    return result


def sales_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    raw = text(value)
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            result = datetime.strptime(raw[:10], pattern)
            if result.year > 2400:
                result = result.replace(year=result.year - 543)
            return result
        except ValueError:
            continue
    raise ExportError("roundtrip.export_date_invalid")


def _first(fields, *names, allow_thb=True):
    for name in names:
        if fields.get(name) not in (None, ""):
            return number(fields[name], allow_thb=allow_thb)
    return None


def _equal_if_present(fields, names, expected):
    for name in names:
        if fields.get(name) not in (None, "") and number(fields[name]) != expected:
            raise ExportError("roundtrip.export_amount_not_roundtrippable", field=name)


def _check_sales(fields):
    sales_date(fields.get("date"))
    total = number(fields.get("total_amount"))
    if total is None:
        raise ExportError("roundtrip.export_amount_missing", field="total_amount")
    items = fields.get("items")
    if not isinstance(items, list) or not items:
        # Source used total as the fallback tax base, then added VAT on return.
        raise ExportError("roundtrip.export_sales_items_required", field="items")
    amounts = []
    for item in items:
        if not isinstance(item, dict):
            raise ExportError("roundtrip.export_record_invalid", field="items")
        # New-platform LineItem names its printed line amount "subtotal".
        # Adapt that known schema field only when the source aliases are absent;
        # all supplied monetary values must still agree with the returned line.
        if item.get("amount") in (None, "") and item.get("total") in (None, ""):
            if item.get("subtotal") not in (None, ""):
                # A string preserves a real zero through the deployed writer's
                # truthiness aliases, without changing the visible numeric cell.
                item["amount"] = str(number(item["subtotal"]))
        qty = number(item.get("qty") or item.get("quantity"))
        price = number(item.get("unit_price") or item.get("price"))
        amount = number(item.get("amount") or item.get("total"))
        if qty is not None and price is not None:
            actual = money.line_amount(qty, price)
        elif amount is not None:
            actual = amount
        else:
            raise ExportError("roundtrip.export_line_amount_missing", field="items")
        for name in ("amount", "total", "subtotal"):
            _equal_if_present(item, (name,), actual)
        # Source truthiness aliases can otherwise drop a numeric zero.
        _equal_if_present(item, ("qty", "quantity"), qty)
        _equal_if_present(item, ("unit_price", "price"), price)
        amounts.append(actual)
    printed = number(fields.get("vat_amount")) or number(fields.get("vat"))
    base, vat, returned_total = money.doc_totals(amounts, printed_vat=printed)
    for value in (base, vat, returned_total):
        number(value)
    _equal_if_present(fields, ("amount_before_vat", "subtotal"), base)
    _equal_if_present(fields, ("vat_amount", "vat"), vat)
    if total != returned_total:
        raise ExportError("roundtrip.export_amount_not_roundtrippable", field="total_amount")


def _check_purchase(fields):
    base = _first(fields, "amount_before_vat", "subtotal", allow_thb=False)
    vat = _first(fields, "vat_amount", "vat", allow_thb=False)
    total = number(fields.get("total_amount"), allow_thb=False)
    if base is None or total is None:
        raise ExportError("roundtrip.export_amount_missing")
    if base == 0 and vat is None:
        # The deployed reader deliberately returns None/None/None for this
        # shape. Requiring an explicit zero VAT distinguishes a zero invoice.
        raise ExportError("roundtrip.export_amount_not_roundtrippable", field="vat_amount")
    returned_base, returned_vat, returned_total = money.doc_totals([], explicit_base=base, printed_vat=vat)
    for value in (returned_base, returned_vat, returned_total):
        number(value)
    _equal_if_present(fields, ("amount_before_vat", "subtotal"), returned_base)
    _equal_if_present(fields, ("vat_amount", "vat"), returned_vat)
    if returned_total != total:
        raise ExportError("roundtrip.export_amount_not_roundtrippable", field="total_amount")


_FACT_FIELDS = ("customer_erp_action", "buyer_erp_action", "customer_is_new", "erp_docnum",
                "erp_party_code", "push_status", "push_reason", "stock_acc_group")


def _apply_facts(fields, facts):
    for name in _FACT_FIELDS:
        fields.pop(name, None)
    for item in fields.get("items") or []:
        if isinstance(item, dict):
            for name in ("erp_action", "is_new", "erp_item_code"):
                item.pop(name, None)
    actions, state = facts.get("actions") or {}, facts.get("state") or {}
    if not isinstance(actions, dict) or not isinstance(state, dict):
        raise ExportError("roundtrip.export_facts_invalid")
    if isinstance(actions.get("customer"), bool):
        fields["customer_erp_action"] = "new" if actions["customer"] else "reused"
    for source, target in (("docnum", "erp_docnum"), ("party_code", "erp_party_code"),
                           ("direction", "direction"), ("stock_acc_group", "stock_acc_group")):
        value = text(actions.get(source))
        if value:
            fields[target] = value
    created, codes = actions.get("items") or [], actions.get("item_codes") or []
    if not isinstance(created, list) or not isinstance(codes, list):
        raise ExportError("roundtrip.export_facts_invalid")
    for i, item in enumerate(fields.get("items") or []):
        if not isinstance(item, dict):
            continue
        if i < len(created) and isinstance(created[i], bool):
            item["erp_action"] = "new" if created[i] else "reused"
        if i < len(codes) and codes[i]:
            item["erp_item_code"] = text(codes[i])
    if state.get("status"):
        fields["push_status"] = text(state["status"])
    if state.get("reason"):
        fields["push_reason"] = text(state["reason"])
    return actions


def _created_masters(records, facts_by_id):
    result, seen = [], set()
    for record in records:
        fields = record["merged_fields"]
        action = (facts_by_id.get(record["history_id"], {}).get("actions") or {})
        codes = action.get("item_codes") or []
        items = fields.get("items") or []
        for i, created in enumerate(action.get("items") or []):
            code = text(codes[i]) if i < len(codes) else ""
            if created is not True or not code or ("item", code) in seen:
                continue
            seen.add(("item", code))
            item = items[i] if i < len(items) and isinstance(items[i], dict) else {}
            result.append({"kind": "item", "code": code,
                           "name": text(item.get("description") or item.get("name")),
                           "docnum": text(action.get("docnum")), "acc_group": text(action.get("stock_acc_group"))})
        code = text(action.get("party_code")) if action.get("created_party") is True else ""
        if code and ("party", code) not in seen:
            seen.add(("party", code))
            kind = "supplier" if action.get("direction") == "purchase" else "customer"
            result.append({"kind": kind, "code": code,
                           "name": text(fields.get("seller_name" if kind == "supplier" else "buyer_name")),
                           "docnum": text(action.get("docnum"))})
    return tuple(result)


def validate_records(records, *, push_facts=None) -> PreparedWorkbook:
    """Return detached records only when financial sheets can return the same money.

    Pending is a review-only sheet in the source protocol; it retains visible
    amounts but is never represented as an executable financial document.
    """
    if not isinstance(records, (list, tuple)) or len(records) > 100:
        raise ExportError("roundtrip.export_records_invalid")
    facts = deepcopy(push_facts) if push_facts is not None else {}
    if not isinstance(facts, dict):
        raise ExportError("roundtrip.export_facts_invalid")
    groups, clean, seen = {"sales": [], "purchase": [], "pending": []}, [], set()
    for index, record in enumerate(records, 1):
        try:
            if not isinstance(record, dict) or not isinstance(record.get("merged_fields"), dict):
                raise ExportError("roundtrip.export_record_invalid")
            history_id = record.get("history_id")
            if (not isinstance(history_id, str) or not history_id or history_id != history_id.strip()
                    or ":" in history_id or history_id in seen):
                raise ExportError("roundtrip.export_history_invalid")
            seen.add(history_id)
            prepared = deepcopy(record)
            text(prepared.get("reason"))
            fields = prepared["merged_fields"]
            fields["history_id"] = history_id
            fact = facts.get(history_id, {})
            if not isinstance(fact, dict):
                raise ExportError("roundtrip.export_facts_invalid")
            _apply_facts(fields, fact)
            direction = text(fields.get("direction")).lower()
            bucket = ("sales" if direction in ("sales", "income") else
                      "purchase" if direction in ("purchase", "expense") else "pending")
            if bucket == "sales":
                _check_sales(fields)
            elif bucket == "purchase":
                _check_purchase(fields)
            else:
                number(fields.get("total_amount"), allow_thb=False)
            # Validate exported text before a writer has a chance to truncate it.
            for value in fields.values():
                if isinstance(value, str):
                    text(value)
            for item in fields.get("items") or []:
                if isinstance(item, dict):
                    for value in item.values():
                        if isinstance(value, str):
                            text(value)
            groups[bucket].append(prepared)
            clean.append(prepared)
        except ExportError as error:
            error.record_index = index
            raise
        except (InvalidOperation, OverflowError, TypeError) as error:
            raise ExportError("roundtrip.export_record_invalid", record_index=index) from error
    if set(facts) - seen:
        raise ExportError("roundtrip.export_facts_invalid")
    return PreparedWorkbook(tuple(groups["sales"]), tuple(groups["purchase"]), tuple(groups["pending"]),
                            _created_masters(clean, facts))
