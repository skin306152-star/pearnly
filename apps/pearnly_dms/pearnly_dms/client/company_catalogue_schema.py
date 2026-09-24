"""Allowlisted native DMS company, customer auxiliary and address catalogues.

Field names and control types were checked against the native forms on
2026-09-24. These are dealer/company catalogues, not the buying customer's
``cus`` form. This module contains schema only: it neither reads nor grants
permissions, downloads images, or exposes catalogue values to LINE/the UI.

``fields`` is the business-value allowlist. Ordinary controls must exist once;
array checkboxes may have zero or more controls and radio groups may have zero
or one checked value. A scalar checkbox must exist once; unchecked means an empty
value, not a missing field. The reader validates context controls separately, never stores
``excluded`` values, and rejects unknown named controls. A missing/unchecked
value must not be confused with a missing required control. ``txtufimg`` is a
stored image reference only, not permission to download the referenced image.
"""

ADDRESS_FIELDS = frozenset({
    "txthousenum", "txtbuilding", "txtfloor", "txtroom", "txtvillage",
    "txtmoo", "txtsoi", "txtroad", "selprovinces", "seldistricts",
    "selsubdistricts", "selzipcodes",
})
ADDRESS_SELECTS = frozenset({
    "selprovinces", "seldistricts", "selsubdistricts", "selzipcodes",
})
CONTACT_FIELDS = frozenset({
    "txttel", "txtemail", "txtlineid", "txtfacebook", "txttaxid",
})
CONTEXT_FIELDS = frozenset({"stsel", "idsel"})
UPLOAD_CONTROLS = frozenset({"btnufimg", "fimg"})
USERS_EXCLUDED_FIELDS = CONTEXT_FIELDS | UPLOAD_CONTROLS | frozenset({
    "txtpasswords", "idsu", "iduo", "selstatususersgrant",
})
USERS_ARRAY_FIELDS = frozenset({
    "chkteamposi2[]", "chkuserheadposi2[]", "chkteamposi3[]", "chkuserheadposi3[]",
    "chkteamposi4[]", "chkuserheadposi4[]", "chkteamposi5[]",
    "chkuserheadposi5[]",
})
USERS_RADIO_FIELDS = frozenset({"chkteamrdo"})
USERS_OPTIONAL_FIELDS = frozenset({"selstatususers"})

# Only checked native business controls belong in these sets. In particular,
# users is a staff/directory projection, never a password/permission replica.
COMPANY_TABLES = {
    "company": ADDRESS_FIELDS | CONTACT_FIELDS | frozenset({
        "txtcompanycode", "txtcompanyname",
    }),
    "branch": ADDRESS_FIELDS | CONTACT_FIELDS | frozenset({
        "txtbranchcode", "txtbranchname", "selcompany", "txtbranchautonum",
    }),
    "team": frozenset({"txtteamcode", "txtteamname", "selbranch"}),
    "stores": frozenset({"txtstorescode", "txtstoresname"}),
    "orders": frozenset({"txtorderscode", "txtordersname"}),
    "works": frozenset({
        "txtworkscode", "txtworksname", "txtstartdate", "txtenddate",
        "ordersval", "txtorders",
    }),
    "users": ADDRESS_FIELDS | CONTACT_FIELDS | USERS_ARRAY_FIELDS | USERS_OPTIONAL_FIELDS
    | USERS_RADIO_FIELDS | frozenset({
        "txtuserscode", "txtusers", "txtusersname", "txttelwork",
        "txttelhome", "selposition", "selbranch", "txtufimg",
    }),
    # Native bank n/e forms have contact controls but no tax-ID control.
    "bank": ADDRESS_FIELDS | (CONTACT_FIELDS - {"txttaxid"}) | frozenset({
        "txtbankcode", "txtbankname", "txtbranchname", "txtaccountnum",
        "selstatuscancel",
    }),
    "finance": ADDRESS_FIELDS | CONTACT_FIELDS | frozenset({
        "txtfinancecode", "txtfinancename", "txtcontactother",
    }),
}
COMPANY_SINGLETONS = {
    "businessowner": ADDRESS_FIELDS | CONTACT_FIELDS | frozenset({
        "txtbusinessownername", "txtufimg",
    }),
    "setcbc": frozenset({"cbhidefreebiesprice"}),
}
CUSTOMER_AUX_TABLES = {
    "prefix": frozenset({"txtprefixname"}),
    "branchcus": frozenset({"txtbranchcuscode", "txtbranchcusname"}),
    "cusbank": frozenset({
        "txtcusbankcode", "txtcusbankname", "selstatuscancel",
    }),
}
GEO_TABLES = {
    "provinces": frozenset({"txtprovincesname", "txtprovincesengname"}),
    "districts": frozenset({
        "selprovinces", "txtdistrictsname", "txtdistrictsengname",
    }),
    "subdistricts": frozenset({
        "selprovinces", "seldistricts", "txtsubdistrictsname",
        "txtsubdistrictsengname",
    }),
    "zipcodes": frozenset({
        "selprovinces", "seldistricts", "selsubdistricts", "txtzipcodesname",
    }),
}

COMPANY_SELECTS = {
    "company": ADDRESS_SELECTS,
    "branch": ADDRESS_SELECTS | frozenset({"selcompany"}),
    "team": frozenset({"selbranch"}),
    "stores": frozenset(),
    "orders": frozenset(),
    "works": frozenset(),
    "users": ADDRESS_SELECTS | USERS_OPTIONAL_FIELDS | frozenset({"selposition", "selbranch"}),
    "bank": ADDRESS_SELECTS | frozenset({"selstatuscancel"}),
    "finance": ADDRESS_SELECTS,
}
COMPANY_MENUS = {
    "businessowner": {"idmenu": "20", "menulv": "3"},
    "company": {"idmenu": "21", "menulv": "3"},
    "branch": {"idmenu": "22", "menulv": "3"},
    "team": {"idmenu": "23", "menulv": "3"},
    "stores": {"idmenu": "24", "menulv": "3"},
    "orders": {"idmenu": "25", "menulv": "3"},
    "works": {"idmenu": "26", "menulv": "3"},
    "users": {"idmenu": "1", "menulv": "2"},
    "bank": {"idmenu": "31", "menulv": "3"},
    "finance": {"idmenu": "28", "menulv": "2"},
    "setcbc": {"idmenu": "27", "menulv": "2"},
}
CUSTOMER_AUX_MENUS = {
    "prefix": {"idmenu": "20", "menulv": "2"},
    "branchcus": {"idmenu": "4", "menulv": "3"},
    "cusbank": {"idmenu": "5", "menulv": "3"},
}

# Plain mappings/frozen sets deliberately keep this import framework/IO free.
# Menus are observed navigation metadata, not a licence to visit extra pages.
# Singleton requests use status=n; native replies contain status=n or e.
# Row forms use stsel=e and their row ID. An optional absent field stays absent,
# never invented as empty/disabled; unknown fields still fail the reader schema.
REGISTRY = {
    **{
        kind: {
            "dataset": "company", "fields": fields,
            "selects": COMPANY_SELECTS[kind], "textareas": frozenset(),
            "arrays": USERS_ARRAY_FIELDS if kind == "users" else frozenset(),
            "radios": USERS_RADIO_FIELDS if kind == "users" else frozenset(),
            "optional": USERS_OPTIONAL_FIELDS if kind == "users" else frozenset(),
            "excluded": USERS_EXCLUDED_FIELDS if kind == "users" else CONTEXT_FIELDS,
            "singleton": False, "menu": COMPANY_MENUS[kind],
        }
        for kind, fields in COMPANY_TABLES.items()
    },
    **{
        kind: {
            "dataset": "company", "fields": fields,
            "selects": ADDRESS_SELECTS if kind == "businessowner" else frozenset(),
            "textareas": frozenset(), "arrays": frozenset(), "radios": frozenset(),
            "checkboxes": frozenset({"cbhidefreebiesprice"}) if kind == "setcbc" else frozenset(),
            "excluded": frozenset({"status"})
            | (UPLOAD_CONTROLS if kind == "businessowner" else frozenset()),
            "singleton": True, "menu": COMPANY_MENUS[kind],
        }
        for kind, fields in COMPANY_SINGLETONS.items()
    },
    **{
        kind: {
            "dataset": "customer_aux", "fields": fields,
            "selects": frozenset({"selstatuscancel"}) if kind == "cusbank" else frozenset(),
            "textareas": frozenset(), "arrays": frozenset(), "radios": frozenset(),
            "excluded": CONTEXT_FIELDS, "singleton": False,
            "menu": CUSTOMER_AUX_MENUS[kind],
        }
        for kind, fields in CUSTOMER_AUX_TABLES.items()
    },
    **{
        kind: {
            "dataset": "geo_aux", "fields": fields,
            "selects": fields & ADDRESS_SELECTS, "textareas": frozenset(),
            "arrays": frozenset(), "radios": frozenset(),
            "excluded": CONTEXT_FIELDS, "singleton": False,
        }
        for kind, fields in GEO_TABLES.items()
    },
}
