"""Editor projections from complete, manually read independent directory datasets.

No DMS login, synchronous refresh, or restamping of old data. Final writes still
perform their independent live validation/readback in the write service.
"""

from typing import Any, Dict, Optional, Tuple

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.id_validate import normalize_thai_id
from pearnly_dms.snapshots.directory import Directory

GEO_FIELDS = (
    ("province_id", "province_name"), ("district_id", "district_name"),
    ("subdistrict_id", "subdistrict_name"), ("zipcode_id", "zipcode"),
)


def geo_levels(customer: Optional[Dict[str, Any]]) -> Tuple[Tuple[str, str], ...]:
    if not customer:
        return ()
    levels = [("provinces", "")]
    for level, parent in (("districts", "province_id"),
                          ("subdistricts", "district_id"), ("zipcodes", "subdistrict_id")):
        if customer.get(parent):
            levels.append((level, str(customer[parent])))
    return tuple(levels)


def _merge_customer_defaults(draft, live, *, preserve_empty_geo=False):
    out = {str(key): str(value or "") for key, value in (draft or {}).items()}
    for key, value in (live or {}).items():
        target = "zipcode" if key == "zipcode_name" else str(key)
        if not str(out.get(target) or "").strip():
            out[target] = str(value or "")
    if preserve_empty_geo:
        unresolved = False
        for id_field, name_field in GEO_FIELDS:
            unresolved = unresolved or (
                id_field in (draft or {}) and not str(draft[id_field] or "").strip()
            )
            if unresolved:
                out[id_field] = out[name_field] = ""
    return out


def read_edit_snapshot(endpoint, *, car_ids=(), customer=None, customer_id="",
                       preserve_empty_geo=False, include_booking_masters=True):
    view = Directory(endpoint)
    saved = view.read_customer(str(customer_id)) if customer_id else {}
    expected = normalize_thai_id(str((customer or {}).get("people_id") or ""))
    if expected and saved and normalize_thai_id(str(saved.get("people_id") or "")) != expected:
        raise DmsClientError("editor identity mismatch", "ERR_DMS_CUSTOMER_LOOKUP")
    resolved = _merge_customer_defaults(customer, saved, preserve_empty_geo=preserve_empty_geo)
    # Customer-only editors use prefixes and addresses, not the independently
    # loaded vehicle/company catalogues. Booking callers keep the full default.
    masters = view.masters() if include_booking_masters else {}
    prefixes = view.list_prefixes()
    return {
        "masters": masters if include_booking_masters else {"prefixes": prefixes},
        "paints": {str(car): view.paints(str(car)) for car in car_ids if car}
        if include_booking_masters else {},
        "prefixes": prefixes,
        "geo": {level: view.list_geo(level, parent) for level, parent in geo_levels(resolved)},
        "customer": saved, "resolved_customer": resolved,
        "snapshot": view.metadata(),
    }


__all__ = ["geo_levels", "read_edit_snapshot"]
