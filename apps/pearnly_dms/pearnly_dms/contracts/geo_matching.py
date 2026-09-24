"""Pure address matching shared by live writes and cached previews."""
import html
import unicodedata
from dataclasses import replace

PREFIXES = ("จังหวัด", "อำเภอ", "ตำบล", "แขวง", "เขต", "จ.", "อ.", "ต.")


def normalized(name):
    value = " ".join(unicodedata.normalize("NFKC", html.unescape(str(name or ""))).split())
    for prefix in PREFIXES:
        prefix = unicodedata.normalize("NFKC", prefix)
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def unique(options, name):
    target = normalized(name)
    matches = {str(value) for value, label in options if value and normalized(label) == target} if target else set()
    return next(iter(matches)) if len(matches) == 1 else ""


def resolve(address, provinces, children):
    """Never guess the first option; unresolved parents leave all children empty."""
    address = replace(address, province_id="", district_id="", subdistrict_id="", zipcode_id="")
    parent = ""
    for level, id_field, name_field in (
        ("provinces", "province_id", "province_name"),
        ("districts", "district_id", "district_name"),
        ("subdistricts", "subdistrict_id", "subdistrict_name"),
        ("zipcodes", "zipcode_id", "zipcode"),
    ):
        rows = provinces if level == "provinces" else children(level, parent)
        parent = unique(rows, getattr(address, name_field))
        if not parent:
            break
        address = replace(address, **{id_field: parent})
    return address


def complete_tree(geo):
    """Shape and parent coverage of a whole native address tree (no IO)."""
    if not isinstance(geo, dict) or set(geo) != {"provinces", "districts", "subdistricts", "zipcodes"}:
        return False
    parents = {""}
    for level in ("provinces", "districts", "subdistricts", "zipcodes"):
        mapping = geo[level]
        if not isinstance(mapping, dict) or set(mapping) != parents:
            return False
        children = set()
        for rows in mapping.values():
            if not isinstance(rows, list):
                return False
            ids = []
            for row in rows:
                if (not isinstance(row, (list, tuple)) or len(row) != 2
                        or not str(row[0] or "").strip() or not str(row[1] or "").strip()):
                    return False
                ids.append(str(row[0]))
            if len(ids) != len(set(ids)) or (level != "zipcodes" and children.intersection(ids)):
                return False
            children.update(ids)
        if level == "provinces" and not children:
            return False
        parents = children
    return True
