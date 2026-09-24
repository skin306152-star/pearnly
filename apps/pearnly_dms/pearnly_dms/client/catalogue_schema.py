"""Native vehicle catalogue controls, verified read-only on DMS-TEST 2026-09-24.

These are complete records, including cancelled records, not booking pickers.
No permissions, credentials, transport or UI behavior lives in this registry.
"""


def table(fields, *, selects=(), textareas=(), arrays=(), singleton=False):
    return {"dataset": "vehicles", "fields": frozenset(fields.split()),
            "selects": frozenset(selects), "textareas": frozenset(textareas),
            "arrays": frozenset(arrays), "radios": frozenset(),
            "excluded": frozenset(("status",) if singleton else ("stsel", "idsel")),
            "singleton": singleton}


REGISTRY = {
    "car": table("txtmodelcode txtmodelname txtdescmodelname carbrandval txtcarbrand "
        "txtcost txtprice txtpriceincvat txtpriceincvatdel enginenoval txtengineno "
        "typecarval txttypecar subtypecarval txtsubtypecar typecardescval txttypecardesc "
        "cargearval txtcargear gradeval txtgrade enginepowerval txtenginepower txtmanuyear "
        "selstatuscancel chkcarpaint[]", selects=("selstatuscancel",), arrays=("chkcarpaint[]",)),
    "carpaint": table("txtcarpaintcode txtcarpaintname txtcarpaintengname"),
    "carbrand": table("txtcarbrandcode txtcarbrandname"),
    "engineno": table("txtenginenocode txtenginenoname"),
    "typecar": table("txttypecarcode txttypecarname"),
    "subtypecar": table("txtsubtypecarcode txtsubtypecarname"),
    "typecardesc": table("txttypecardesccode txttypecardescname"),
    "cargear": table("txtcargearname"),
    "grade": table("txtgradecode txtgradename"),
    "enginepower": table("txtenginepowername"),
    "ctgrcampaign": table("txtctgrcampaigncode txtctgrcampaignname tanote", textareas=("tanote",)),
    "product": table("txtproductcode txtproductname txtprice unitsval txtunits selstatuscancel",
                     selects=("selstatuscancel",)),
    "units": table("txtunitscode txtunitsname"),
    "carcampaign": table("txtproductcode txtproductname txtprice ctgrcampaignval txtctgrcampaign "
        "txtclaimpctcampaign txtstdatecampaign txteddatecampaign selstatuscampaign tacampaignnote selstatuscancel",
        selects=("selstatuscampaign", "selstatuscancel"), textareas=("tacampaignnote",)),
    "numdayspullcarback": table("txtnumdays", singleton=True),
}

# These children are rendered in the native edit HTML, not loaded by executing
# its inline scripts. Region presence distinguishes an empty table from loss.
FREEBIES = ("txtfbproduct[]", "txtfbunits[]", "txtfbquantity[]", "txtfbprice[]",
            "txtfbamount[]", "fbproductval[]")
CAMPAIGNS = ("txtccplproductname[]", "txtccplprice[]", "txtccplctgrcampaign[]",
             "txtccplclaimpctcampaign[]", "txtccplstdatecampaign[]",
             "txtccpleddatecampaign[]", "txtccplstatuscampaign[]")
LOAN_COLUMNS = ("txtslsddownpayment", "txtslsdperiod", "slsdpaymenttimingval",
                "txtslsdpaymenttiming", "txtslsdinterestrate", "txtslsdinterestratecampg",
                "txtslsdcommission", "txtslsdcampgdatebg", "txtslsdcampgdateed")
REGISTRY["car"].update(
    fields=REGISTRY["car"]["fields"] | frozenset(FREEBIES + CAMPAIGNS),
    repeat_fields=frozenset(FREEBIES + CAMPAIGNS),
    regions={"showfreebies": FREEBIES, "showcarcampaign": CAMPAIGNS, "showloanschedule": ()},
    indexed={"region": "showloanschedule", "parents": ("lsdfinanceval", "txtlsdfinance"),
             "columns": LOAN_COLUMNS},
)

# Existing campaign forms render their applicable vehicle-type groups here.
# An empty region is legitimate; a missing region is not a complete form.
REGISTRY["carcampaign"].update(
    regions={"showcartype": ()},
    indexed={"region": "showcartype", "parents": ("typecarval", "txttypecar"),
             "checked_one": ("cbctall",), "checked_many": ("cbct",)},
)
