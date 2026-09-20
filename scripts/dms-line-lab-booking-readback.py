"""Read-only verification of the uniquely marked interactive booking E2E fixture.

No order replay, update, upload or deletion. Detailed synthetic values stay in
the isolated Site private directory; stdout is limited to evidence summaries.
"""
import json
import os
from decimal import Decimal

import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dms-line-lab.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")

from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

try:
    operator = frappe.get_doc("DMS Operator", {"display_name": "E2E DMS operator"})
    log = frappe.get_doc("Pearnly Push Log", {
        "actor": operator.user, "business_key": "BK000002609000020",
    })
    request = json.loads(log.request_body)
    response = json.loads(log.response_body)
    assert log.status == "success" and request["mode"] == "booking"
    assert request["qa"]["regis_name"] == "E2E 20260920 Edited Registration"
    assert request["customer_id"] == "127" and response["booking_id"]
    expected = {
        "cusval": "127", "txtpeopleid": "1000000730317", "txtcus": "E2E 20260920 ทดสอบ",
        "txttel": "0990000921", "txtbirthday": "01/01/2530",
        "usersval": request["advisor_id"], "txtusers": "sale02",
        "placebookval": request["qa"]["place_id"], "txtplacebook": "โชว์รูม",
        "carval": "2", "carpaintval": request["paint_id"], "carpaintname": "แดง",
        "txtcardeliverydate": "21/10/2569", "termsaleval": request["qa"]["term_id"],
        "regisbehalfval": request["qa"]["regis_id"],
        "txtregisname": "E2E 20260920 Edited Registration",
        "txthousenum": "920/1", "txtbuilding": "E2E Building", "txtfloor": "2", "txtroom": "20",
        "txtvillage": "E2E Village", "txtmoo": "9", "txtsoi": "E2E Soi", "txtroad": "E2E Road",
        "txtdetailother": "E2E-20260920 no payment collected",
        "txtmoneycash": "25.75", "txtmoneyother": "3.25", "txtearnestmoney": "29.00",
    }
    money = {"txtmoneycash", "txtmoneyother", "txtearnestmoney"}

    def read(client, adapter):
        form = client._parse_form_defaults(client._post_text(
            "drfcbc/form.php", {"status": "e", "id": response["booking_id"]},
        ))
        customer = client.read_customer("127")
        checks = {}
        for key, value in expected.items():
            actual = str(form.get(key, ""))
            equal = (Decimal(actual.replace(",", "") or "0") == Decimal(value)
                     if key in money else actual == value)
            checks[key] = {"expected": value, "actual": actual, "match": equal}
        docno = str(form.get("txtdocno") or "")
        if not docno.startswith(str(form.get("txtprefixautonum") or "")):
            docno = str(form.get("txtprefixautonum") or "") + docno
        checks["document_number"] = {"match": docno == log.business_key}
        checks["customer_phone_updated"] = {"match": customer.get("phone") == "0990000921"}
        names = [v for k, v in form.items() if k.startswith("fulcurrname")]
        checks["id_card_attachment"] = {"match": "สำเนาบัตรประชาชน" in names}
        checks["attachment_ledger"] = {"match": response.get("attach_ok") is True and response.get("attached") == 1}
        return {"ok": all(row["match"] for row in checks.values()), "checks": checks,
                "booking_id": response["booking_id"], "booking_no": log.business_key, "ledger": log.name}

    result = run_logged_in(resolve_dms_endpoint(operator.user, None), read, authoritative_read=True)
    path = "dms-line-lab.localhost/private/line-lab-booking-readback.json"
    with open(path, "w") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    checks = result.get("checks", {})
    print(json.dumps({"ok": result.get("ok"), "booking_no": result.get("booking_no"),
                      "checked": len(checks), "matched": sum(row["match"] for row in checks.values()),
                      "mismatched_fields": [key for key, row in checks.items() if not row["match"]],
                      "error_code": result.get("error_code")}, ensure_ascii=False))
    if not result.get("ok"):
        raise SystemExit(1)
finally:
    frappe.destroy()
