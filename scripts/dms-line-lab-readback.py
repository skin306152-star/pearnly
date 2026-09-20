"""Read back the explicitly marked interactive E2E customer, not arbitrary tenant data."""
import json
import os
import frappe

os.chdir("/home/frappe/frappe-bench/sites")
frappe.init(site="dms-line-lab.localhost", sites_path=".")
frappe.connect()
frappe.set_user("Administrator")

from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

expected = {
    "name": "E2E 20260920 ทดสอบ", "people_id": "1000000730317", "phone": "0990000920",
    "birthday_be": "01/01/2530", "house_no": "920/1", "building": "E2E Building",
    "floor": "2", "room": "20", "village": "E2E Village", "moo": "9", "soi": "E2E Soi", "road": "E2E Road",
}
try:
    operator = frappe.get_doc("DMS Operator", {"display_name": "E2E DMS operator"})
    log = frappe.get_doc("Pearnly Push Log", {"actor": operator.user, "business_key": "127"})
    assert log.status == "success"
    endpoint = resolve_dms_endpoint(operator.user, None)
    def read(client, adapter):
        actual = client.read_customer(log.business_key)
        assert actual.get("name") == expected["name"], "External row is not this test fixture"
        fields = {key: {"expected": value, "actual": actual.get(key, ""), "match": str(actual.get(key, "")) == value}
                  for key, value in expected.items()}
        for suffix in ("_ct", "_sd"):
            for key in ("house_no", "building", "floor", "room", "village", "moo", "soi", "road"):
                fields[key + suffix] = {"expected": expected[key], "actual": actual.get(key + suffix, ""),
                                         "match": str(actual.get(key + suffix, "")) == expected[key]}
        return {"ok": all(row["match"] for row in fields.values()), "customer_id": log.business_key,
                "fields": fields, "ledger": log.name}
    result = run_logged_in(endpoint, read, authoritative_read=True)
    path = "dms-line-lab.localhost/private/line-lab-customer-readback.json"
    with open(path, "w") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    print(json.dumps({"ok": result.get("ok"), "customer_id": result.get("customer_id"),
                      "matched": sum(row["match"] for row in result.get("fields", {}).values()),
                      "checked": len(result.get("fields", {})),
                      "mismatched_fields": [key for key, row in result.get("fields", {}).items() if not row["match"]],
                      "error_code": result.get("error_code")}, ensure_ascii=False))
    if not result.get("ok"):
        raise SystemExit(1)
finally:
    frappe.destroy()
