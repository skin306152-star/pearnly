"""Validate full native geographic catalogues and project the LINE address tree.

No network, framework or persistence. English names and selected parent labels
remain in the full catalogues; the existing tree keeps its Thai-label contract.
Every detail must belong to the recorded parent chain, including empty parents.
"""
from copy import deepcopy

from pearnly_dms.client.company_catalogue_schema import GEO_TABLES, REGISTRY
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.geo_matching import complete_tree


def _fail(reason):
    raise DmsClientError("DMS geographic catalogue incomplete: " + reason,
                         "ERR_DMS_SNAPSHOT_INCOMPLETE")


def geo_from_catalogues(catalogues):
    """Return a complete four-level projection plus all checked source fields.

    This does not turn an in-progress scan into a complete snapshot: the caller
    must first finish the collector's native count and final ID-list passes.
    Parent IDs are authoritative here; labels never repair an unknown parent.
    """
    if not isinstance(catalogues, dict) or set(catalogues) != set(GEO_TABLES):
        _fail("missing or unknown table")
    for kind, required in GEO_TABLES.items():
        records = catalogues[kind]
        if not isinstance(records, dict):
            _fail("invalid table")
        for identity, record in records.items():
            if (not isinstance(identity, str) or not identity or identity != identity.strip()
                    or not isinstance(record, dict) or set(record) != {"id", "fields", "labels"}
                    or record["id"] != identity or not isinstance(record["fields"], dict)
                    or set(record["fields"]) != required or not isinstance(record["labels"], dict)
                    or set(record["labels"]) != REGISTRY[kind]["selects"]
                    or any(not isinstance(value, str) for value in record["fields"].values())
                    or any(not isinstance(value, str) for value in record["labels"].values())):
                _fail("invalid detail fields")
            if not record["fields"]["txt" + kind + "name"].strip():
                _fail("missing native display name")

    provinces = catalogues["provinces"]
    districts = catalogues["districts"]
    subdistricts = catalogues["subdistricts"]
    zipcodes = catalogues["zipcodes"]
    tree = {
        "provinces": {"": [[identity, record["fields"]["txtprovincesname"]]
                           for identity, record in provinces.items()]},
        "districts": {identity: [] for identity in provinces},
        "subdistricts": {identity: [] for identity in districts},
        "zipcodes": {identity: [] for identity in subdistricts},
    }
    for identity, record in districts.items():
        fields = record["fields"]
        province = fields["selprovinces"]
        if province not in provinces:
            _fail("district has unknown province")
        tree["districts"][province].append([identity, fields["txtdistrictsname"]])
    for identity, record in subdistricts.items():
        fields = record["fields"]
        district = fields["seldistricts"]
        if district not in districts:
            _fail("subdistrict has unknown district")
        if fields["selprovinces"] != districts[district]["fields"]["selprovinces"]:
            _fail("subdistrict province differs from district")
        tree["subdistricts"][district].append([identity, fields["txtsubdistrictsname"]])
    for identity, record in zipcodes.items():
        fields = record["fields"]
        subdistrict = fields["selsubdistricts"]
        if subdistrict not in subdistricts:
            _fail("zipcode has unknown subdistrict")
        parent = subdistricts[subdistrict]["fields"]
        if any(fields[name] != parent[name] for name in ("selprovinces", "seldistricts")):
            _fail("zipcode province or district differs from subdistrict")
        tree["zipcodes"][subdistrict].append([identity, fields["txtzipcodesname"]])
    if not complete_tree(tree):
        _fail("invalid complete tree")
    return {**tree, "catalogues": deepcopy(catalogues)}
