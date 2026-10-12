"""Three-way customer writes. Pure values; no framework, cache or transport.

Missing keys mean no edit; an explicit empty string is an edit. Address blocks
remain independent. An upstream field changed to the same desired value is
already satisfied, not a conflict. No upstream version/CAS is available.
"""
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.id_validate import normalize_thai_id

IDENTITY = ("prefix_id", "name", "people_id", "tax_id", "birthday_be", "phone",
            "tel_work", "tel_home", "email", "line_id", "facebook", "credit_day")
ADDRESS = ("house_no", "building", "floor", "room", "village", "moo", "soi", "road",
           "province_id", "district_id", "subdistrict_id", "zipcode_id")
SUFFIXES = ("", "_ct", "_sd")
WRITABLE = IDENTITY + tuple(key + suffix for suffix in SUFFIXES for key in ADDRESS)


def value(value):
    return str(value if value is not None else "")


def flatten(fields, addresses=None, *, derive_tax=True):
    result = {key: value(fields[key]) for key in IDENTITY if key in fields}
    if derive_tax and fields.get("people_id") and not fields.get("tax_id"):
        result["tax_id"] = value(fields["people_id"])
    blocks = addresses if addresses is not None else {suffix: fields for suffix in SUFFIXES}
    for suffix in SUFFIXES:
        block = blocks.get(suffix) or {}
        result.update({key + suffix: value(block[key]) for key in ADDRESS if key in block})
    return result


def stored(key, item):
    """A value the way DMS keeps it: identity numbers without separators, other text trimmed."""
    text = value(item)
    return normalize_thai_id(text) if key in ("people_id", "tax_id") else text.strip()


def readback(fields, addresses=None):
    """What a customer write must read back from DMS. The readback right after the write
    and any later check of that same write compare against this, with ``stored`` on both sides."""
    return {key: stored(key, item) for key, item in flatten(fields, addresses, derive_tax=False).items()}


def readback_of(saved, keys):
    """The read-back values of ``saved`` (fields read from DMS) for ``keys``."""
    return {key: stored(key, saved.get(key)) for key in keys}


def secondary_addresses(desired, current, present=SUFFIXES[1:]):
    """DMS's own address rule (native ``dupaddr``): the ID-card block ``""`` is written as
    given; contact ``_ct`` and document-delivery ``_sd`` are filled from it only while that
    whole block is still empty in DMS. A block already in use is never overwritten: it is
    usually where the customer actually lives, not the hometown printed on the card.
    ``present`` lists the secondary blocks the native form actually has; others are left alone."""
    secondary = {key + suffix for suffix in SUFFIXES[1:] for key in ADDRESS}
    result = {key: item for key, item in desired.items() if key not in secondary}
    for suffix in present:
        if not any(value(current.get(key + suffix)).strip() for key in ADDRESS):
            result.update({key + suffix: value(desired.get(key, current.get(key))) for key in ADDRESS})
    return result


def split(values):
    return ({key: values[key] for key in IDENTITY if key in values},
            {suffix: {key: values[key + suffix] for key in ADDRESS if key + suffix in values}
             for suffix in SUFFIXES})


def merge(baseline, desired, current):
    """Return only intended changes plus conflicts; never copy a stale full form."""
    changes, conflicts = {}, []
    if (baseline and "people_id" in baseline
            and value(current.get("people_id")) != value(baseline["people_id"])
            and value(desired.get("people_id", baseline["people_id"])) != value(current.get("people_id"))):
        conflicts.append({"field": "people_id", "old": value(current.get("people_id")),
                          "new": value(desired.get("people_id", baseline["people_id"]))})
    for key, new in desired.items():
        if key not in WRITABLE:
            continue
        old, live = value((baseline or {}).get(key)), value(current.get(key))
        # Missing baseline is not permission to overwrite an unknown old value.
        if baseline is None or key not in baseline:
            changes[key] = value(new)
            conflicts.append({"field": key, "old": live, "new": value(new)})
        elif value(new) != old:
            changes[key] = value(new)
            if live != old and live != value(new):
                conflicts.append({"field": key, "old": live, "new": value(new)})
    return changes, conflicts


class CustomerConflict(DmsClientError):
    def __init__(self, code, customer_id, current, desired, conflicts=()):
        super().__init__("Customer requires a new review", code)
        self.response_body = {"customer_conflict": {
            "customer_id": str(customer_id), "current": dict(current),
            "desired": dict(desired), "conflicts": list(conflicts)}, "submitted": False}


def unknown():
    exc = DmsClientError("Customer submission outcome is not confirmed", "ERR_DMS_CUSTOMER_UNCONFIRMED")
    exc.response_body = {"submitted": True, "retry_safe": False, "submission_status": "unknown"}
    return exc
